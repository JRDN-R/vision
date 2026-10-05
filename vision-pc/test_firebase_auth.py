"""Real signed-token, account-isolation and encrypted credential-vault checks."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.fernet import Fernet
from cryptography.x509.oid import NameOID
import jwt
import requests

import firebase_auth
import server
import sessions
import audit_logs


class FirebaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'token': 'a'*48, 'diagnosticToken': 'd'*48, 'publicAccess': True,
                                           'firebaseAuth': {'enabled': True, 'projectId': 'visionboard-api'}}))
        server.configure(self.config)
        server.app.config['TESTING'] = True
        self.client = server.app.test_client()
        self.identity = server.app.config['FIREBASE_IDENTITY']
        self.identity._keys = {'test': self.signing_key.public_key()}
        self.identity._expires = time.monotonic() + 3600
        self.a, self.b = self.headers('alice'), self.headers('bob')
        self.legacy = {'Authorization': 'Bearer ' + 'a'*48, 'X-Vision-Project-Key': 's'*48}
        self.project = '/api/projects/project_firebase_0001'

    def tearDown(self):
        self.tmp.cleanup()

    def token(self, uid='alice', **changes):
        now = int(time.time())
        claims = dict(sub=uid, aud='visionboard-api', iss='https://securetoken.google.com/visionboard-api',
                      exp=now+3600, iat=now-1, auth_time=now-10, firebase={'sign_in_provider': 'google.com'})
        claims.update(changes)
        return jwt.encode(claims, self.signing_key, algorithm='RS256', headers={'kid': 'test'})

    def headers(self, uid):
        return {'Authorization': 'Bearer ' + self.token(uid), 'Origin': 'https://jrdn-r.github.io'}

    def save(self, headers=None, path=None, revision=0):
        return self.client.put(path or self.project, headers=headers or self.a,
                               json={'revision': revision, 'project': {'title': 'My board', 'nodes': []}})

    def save_legacy(self):
        with patch.dict(server.app.config, FIREBASE_IDENTITY=None):
            return self.save(self.legacy)

    def test_real_signature_and_required_claims(self):
        self.assertEqual(self.identity.verify(self.token())['sub'], 'alice')
        password = self.token(firebase={'sign_in_provider': 'password'})
        self.assertEqual(self.identity.verify(password)['sub'], 'alice')
        self.assertEqual(self.client.get('/api/health', headers={'Authorization': 'Bearer '+password,
                                                                 'Origin': 'https://jrdn-r.github.io'}).status_code, 200)
        invalid = [self.token(aud='another-project'), self.token(iss='https://securetoken.google.com/wrong'),
                   self.token(exp=int(time.time())-1), self.token(iat=int(time.time())+60),
                   self.token(auth_time=int(time.time())+60), self.token(auth_time='123'),
                   self.token(auth_time=float('nan')), self.token(uid=''),
                   self.token(firebase={'sign_in_provider': 'facebook.com'}),
                   jwt.encode({'sub': 'alice'}, 's'*32, algorithm='HS256', headers={'kid': 'test'}),
                   jwt.encode(jwt.decode(self.token(), options={'verify_signature': False}), self.other_key,
                              algorithm='RS256', headers={'kid': 'test'})]
        for token in invalid:
            with self.subTest(token=token[:20]):
                with self.assertRaises(firebase_auth.InvalidIdentity):
                    self.identity.verify(token)
                response = self.client.get('/api/health', headers={'Authorization': 'Bearer '+token})
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get('/api/health', headers={'Authorization': 'Bearer é'}).status_code, 401)

    def test_public_certificates_are_cached_and_fail_closed(self):
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'test signing key')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(self.signing_key.public_key()).serial_number(1)
                .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
                .sign(self.signing_key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode())
        class CertificateResponse:
            headers = {'Cache-Control': 'public, max-age=3600'}
            def raise_for_status(self): pass
            def json(self): return {'test': cert}
            def close(self): pass
        verifier = firebase_auth.FirebaseIdentity('visionboard-api')
        with patch.object(firebase_auth.requests, 'get', return_value=CertificateResponse()) as fetch:
            verifier.verify(self.token())
            verifier.verify(self.token())
            self.assertEqual(fetch.call_count, 1)
            unknown = jwt.encode(jwt.decode(self.token(), options={'verify_signature': False}), self.signing_key,
                                 algorithm='RS256', headers={'kid': 'unknown'})
            with self.assertRaises(firebase_auth.InvalidIdentity): verifier.verify(unknown)
            self.assertEqual(fetch.call_count, 1)
        verifier._expires = 0
        with patch.object(firebase_auth.requests, 'get', side_effect=requests.ConnectionError):
            with self.assertRaises(firebase_auth.IdentityUnavailable): verifier.verify(self.token())

    def test_account_discovery_isolation_and_revision_conflicts(self):
        self.assertEqual(self.save().status_code, 200)
        listing = self.client.get('/api/projects', headers=self.a).json['projects']
        self.assertEqual([(p['id'], p['title'], p['revision']) for p in listing],
                         [('project_firebase_0001', 'My board', 1)])
        self.assertEqual(self.client.get('/api/projects', headers=self.b).json['projects'], [])
        self.assertEqual(self.client.get('/api/projects', headers=self.legacy).status_code, 401)
        self.assertEqual(self.client.get(self.project, headers=self.a).status_code, 200)
        for headers in (self.b, {**self.b, 'X-Vision-Project-Key': 's'*48}):
            for path in ('', '/runs', '/media'):
                self.assertEqual(self.client.get(self.project+path, headers=headers).status_code, 404)
            self.assertEqual(self.save(headers).status_code, 404)
        self.assertEqual(self.save(revision=0).status_code, 409)
        self.assertEqual(self.save(revision=1).json['revision'], 2)

    def test_legacy_projects_require_explicit_possession_based_claim(self):
        self.assertEqual(self.save_legacy().status_code, 200)
        self.assertEqual(self.client.get('/api/projects', headers=self.a).json['projects'], [])
        denied = self.client.get(self.project, headers={**self.a, 'X-Vision-Project-Key': 's'*48})
        self.assertEqual((denied.status_code, denied.json['code']), (409, 'project-claim-required'))
        self.assertEqual(self.client.post(self.project+'/claim', headers=self.a).status_code, 403)
        self.assertEqual(self.client.post(self.project+'/claim', headers={**self.a, 'X-Vision-Project-Key': 'x'*48}).status_code, 403)
        claim = self.client.post(self.project+'/claim', headers={**self.a, 'X-Vision-Project-Key': 's'*48})
        self.assertEqual(claim.json['revision'], 1)
        self.assertEqual(self.client.get(self.project, headers=self.a).status_code, 200)
        self.assertEqual(self.client.get(self.project, headers=self.legacy).status_code, 401)
        self.assertEqual(self.client.post(self.project+'/claim', headers={**self.b, 'X-Vision-Project-Key': 's'*48}).status_code, 404)
        self.assertEqual(self.save(revision=1).status_code, 200)

    def test_jobs_and_openai_response_ownership(self):
        ids = []
        for headers in (self.a, self.b):
            response = self.client.post('/api/youtube', headers=headers,
                                        json={'url': 'https://youtu.be/abcdefghijk', 'clientRequestId': 'same'})
            self.assertEqual(response.status_code, 202)
            ids.append(response.json['id'])
        self.assertEqual(len(set(ids)), 2)
        for own, headers in zip(ids, (self.a, self.b)):
            for job in ids:
                self.assertEqual(self.client.get('/api/jobs/'+job, headers=headers).status_code, 200 if job == own else 404)
                if job != own:
                    self.assertEqual(self.client.get('/api/jobs/'+job+'/result', headers=headers).status_code, 404)
                    self.assertEqual(self.client.delete('/api/jobs/'+job, headers=headers).status_code, 404)
        server.track_event({'type': 'response.created', 'response': {'id': 'resp_alice', 'status': 'in_progress'}}, 'firebase:alice')
        with patch.object(server, 'upstream') as upstream:
            self.assertEqual(self.client.get('/api/openai/responses/resp_alice', headers=self.b).status_code, 404)
            self.assertEqual(self.client.post('/api/openai/responses/resp_alice/cancel', headers=self.legacy).status_code, 401)
            upstream.assert_not_called()

    def test_key_vault_encrypts_isolates_restores_and_deletes(self):
        cipher = Fernet(Fernet.generate_key())
        key = 'sk-account-test-secret-that-is-not-real'
        path = '/api/account/openai-key'
        with patch.object(sessions, 'protect_secret', side_effect=lambda value: cipher.encrypt(value.encode())), \
                patch.object(sessions, 'unprotect_secret', side_effect=lambda value: cipher.decrypt(bytes(value)).decode()):
            self.assertEqual(self.client.put(path, headers=self.a, json={'apiKey': key}).json, {'saved': True})
            self.assertEqual(self.client.get(path, headers=self.a).json, {'saved': True, 'apiKey': key})
            # An independently obtained token for the same account restores it.
            restored = self.client.get(path, headers=self.headers('alice'))
            self.assertEqual(restored.json['apiKey'], key)
            self.assertEqual(restored.headers['Cache-Control'], 'no-store')
            self.assertEqual(self.client.get(path, headers=self.b).json, {'saved': False, 'apiKey': ''})
            self.assertEqual(self.client.delete(path, headers=self.b).status_code, 200)
            for method in ('get', 'put', 'delete'):
                self.assertEqual(getattr(self.client, method)(path, headers=self.legacy).status_code, 401)
            with server.connect_db() as db:
                row = db.execute('SELECT * FROM account_credentials').fetchone()
                self.assertNotIn(key.encode(), bytes(row['openai_key_cipher']))
                self.assertNotIn(key, '\n'.join(db.iterdump()))
            with patch.object(sessions, 'protect_secret', side_effect=RuntimeError('DPAPI failed')):
                self.assertEqual(self.client.put(path, headers=self.a, json={'apiKey': 'sk-new-value'}).status_code, 503)
            self.assertEqual(self.client.get(path, headers=self.a).json['apiKey'], key)
            self.assertEqual(self.client.delete(path, headers=self.a).json, {'saved': False})
            self.assertEqual(self.client.get(path, headers=self.a).json['saved'], False)

    def test_disabled_firebase_keeps_legacy_private_auth(self):
        config = json.loads(self.config.read_text())
        del config['firebaseAuth']
        self.config.write_text(json.dumps(config))
        server.configure(self.config)
        self.assertEqual(self.client.get('/api/health', headers=self.a).status_code, 401)
        health = self.client.get('/api/health', headers=self.legacy)
        self.assertEqual(health.status_code, 200)
        self.assertFalse(health.json['firebaseAuth']['enabled'])

    def test_google_is_mandatory_except_scoped_private_local_diagnostics(self):
        self.assertEqual(self.client.get('/api/status').json,
                         {'service': 'vision', 'mode': 'private-pc', 'googleSignInRequired': True})
        self.assertEqual(self.client.get('/api/health', headers=self.legacy).status_code, 401)
        diagnostic = {**self.legacy, 'X-Vision-Diagnostic-Token': 'd'*48}
        self.assertEqual(self.client.get('/api/health', headers=diagnostic).status_code, 200)
        for extra in ({'Origin': 'null'}, {'X-Forwarded-For': '192.0.2.1'}, {'X-Vision-Diagnostic-Token': 'wrong'}):
            self.assertEqual(self.client.get('/api/health', headers={**diagnostic, **extra}).status_code, 401)
        self.assertEqual(self.client.get('/api/health', headers=diagnostic,
                                        environ_overrides={'REMOTE_ADDR': '192.0.2.1'}).status_code, 401)
        base = '/api/projects/vision_check_' + '1'*32
        self.assertEqual(self.save(diagnostic, base).status_code, 200)
        self.assertEqual(self.client.get(base, headers=diagnostic).status_code, 200)
        self.assertEqual(self.client.get(base+'/transcriptions/'+'2'*24, headers=diagnostic).status_code, 404)
        for path in (self.project, base+'/runs', '/api/account/openai-key', '/api/youtube'):
            self.assertEqual(self.client.get(path, headers=diagnostic).status_code, 401)
        self.assertEqual(self.client.delete(base, headers=diagnostic).status_code, 401)

    def test_logs_sanitize_bound_and_do_not_count_alternating_devices_twice(self):
        audit = server.app.config['AUDIT_LOGS']
        self.assertTrue((audit.directory/'Users.txt').is_file())
        now = int(time.time())
        identities = [{'Authorization': 'Bearer '+self.token(auth_time=when, name='../Alice\nForged\tName', email='a@example.test')}
                      for when in (now-100, now-200)]
        for _ in range(4):
            for headers in identities:
                self.assertEqual(self.client.get('/api/health', headers=headers).status_code, 200)
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT sign_in_sightings FROM audit_users').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0], 2)
        # Restart the audit object and encounter either existing session again.
        restarted = audit_logs.AuditLogs(audit.directory, server.connect_db, server.app.logger)
        restarted.initialize()
        restarted.identity('firebase:alice', jwt.decode(identities[0]['Authorization'][7:], options={'verify_signature': False}))
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT sign_in_sightings FROM audit_users').fetchone()[0], 2)
        self.assertEqual(self.save(identities[0]).status_code, 200)
        secret = 'sk-user-private-secret-never-write-this'
        with patch.object(sessions, 'protect_secret', return_value=b'encrypted'):
            self.assertEqual(self.client.put('/api/account/openai-key', headers=identities[0], json={'apiKey': secret}).status_code, 200)
        text = '\n'.join(path.read_text() for path in audit.directory.glob('*.txt'))
        self.assertNotIn(secret, text)
        self.assertNotIn(identities[0]['Authorization'], text)
        self.assertNotIn('../Alice\nForged', text)
        self.assertIn('project_saved', text)
        self.assertIn('requestBytes=', text)
        files = list(audit.directory.glob('User-*.txt'))
        self.assertEqual(len(files), 1)
        self.assertEqual(len(files[0].stem), len('User-')+64)
        with patch.object(audit_logs, 'MAX_EVENTS', 5):
            for _ in range(8):
                audit.event('firebase:alice', 'processing_finished', jobType='whisper', outcome='complete',
                            processingWallSeconds=1.25, uploadedBytes=80, filename='private-name', token=secret)
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0], 5)
        text = files[0].read_text()
        self.assertIn('processingWallSeconds=1.25', text)
        self.assertNotIn('private-name', text)
        self.assertNotIn(secret, text)

    def test_background_processing_reports_actual_outcome_and_wall_time(self):
        accepted = self.client.post('/api/youtube', headers=self.a,
                                   json={'url':'https://youtu.be/abcdefghijk','clientRequestId':'logged-job'})
        self.assertEqual(accepted.status_code, 202)
        def process(job, url, ffmpeg, update, **kwargs):
            update(job, status='complete', phase='Ready', progress=100, result=b'{}')
        with patch.object(server, 'process_job', side_effect=process):
            self.assertTrue(server.process_next_job())
        with server.connect_db() as db:
            row = db.execute("SELECT details FROM audit_events WHERE event='processing_finished'").fetchone()
        details = json.loads(row[0])
        self.assertEqual(details['outcome'], 'complete')
        self.assertEqual(details['jobType'], 'youtube')
        self.assertEqual(details['outputBytes'], 2)
        self.assertGreaterEqual(details['processingWallSeconds'], 0)


if __name__ == '__main__':
    unittest.main()
