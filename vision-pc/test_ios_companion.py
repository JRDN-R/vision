"""Offline account-isolation, PKCE and streaming-transfer integration checks."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

import ios_companion
import server


class IOSCompanionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        ffmpeg = self.root / 'ffmpeg'
        ffmpeg.touch()
        config = self.root / 'config.json'
        config.write_text(json.dumps(dict(token='a' * 48, publicAccess=True, ffmpeg=str(ffmpeg),
            backendUrl='https://desktop-vjt2br2.tail385c9d.ts.net',
            firebaseAuth=dict(enabled=True, projectId='visionboard-api'))))
        server.configure(config)
        server.app.config['TESTING'] = True
        identity = server.app.config['FIREBASE_IDENTITY']
        identity._keys = {'ios-test': self.signing_key.public_key()}
        identity._expires = time.monotonic() + 3600
        self.service = server.ios
        self.client = server.app.test_client()
        self.alice, self.bob = self.headers('alice'), self.headers('bob')
        self.verifier, self.state = 'v' * 64, 's' * 43
        self.patches = [
            patch.object(socket, 'getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]),
            patch('vortex.shutil.disk_usage', return_value=SimpleNamespace(free=100 * 1024 ** 3)),
            patch.object(server.vortex, 'capability', return_value=dict(ready=True, engines={'yt-dlp': True})),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        server.vortex.close()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def headers(self, uid):
        now = int(time.time())
        claims = dict(sub=uid, aud='visionboard-api', iss='https://securetoken.google.com/visionboard-api',
                      exp=now + 3600, iat=now - 1, auth_time=now - 10,
                      firebase=dict(sign_in_provider='google.com', identities={'google.com': ['google-' + uid]}))
        token = jwt.encode(claims, self.signing_key, algorithm='RS256', headers={'kid': 'ios-test'})
        return {'Authorization': 'Bearer ' + token, 'Origin': ios_companion.WEB_ORIGIN}

    def start(self):
        result = self.client.post('/api/ios/auth/start', json=dict(challenge=ios_companion.pkce_challenge(self.verifier), state=self.state))
        self.assertEqual(result.status_code, 201, result.json)
        return result.json

    def approve(self, start=None, subject='google-alice'):
        start = start or self.start()
        response = Mock(status_code=200)
        response.json.return_value = {'sub': subject}
        token = 'google-access-credential-example'
        with patch('ios_companion.requests.get', return_value=response) as get:
            result = self.client.post('/api/ios/auth/approve', headers=self.alice,
                json=dict(id=start['id'], state=self.state, googleAccessToken=token))
        if result.status_code == 200:
            get.assert_called_once_with('https://openidconnect.googleapis.com/v1/userinfo',
                headers={'Authorization': 'Bearer ' + token}, timeout=(5, 10), allow_redirects=False)
        return start, result

    def enqueue(self, owner=None, request_id='ios-job'):
        result = self.client.post('/api/vortex/jobs', headers=owner or self.alice,
            json=dict(input='https://www.youtube.com/watch?v=abcdefghijk', kind='download', quality='balanced', requestId=request_id))
        self.assertEqual(result.status_code, 202, result.json)
        return result.json['id']

    def complete(self, job_id, content=b'not-a-real-movie-but-exact-private-bytes', filename='media.mp4'):
        directory = server.vortex.root / job_id
        directory.mkdir(exist_ok=True)
        (directory / filename).write_bytes(content)
        with server.connect_db() as db:
            db.execute("UPDATE vortex_jobs SET status='processing' WHERE id=?", (job_id,))
        server.vortex._event(job_id, {'media': {'title': 'My movie', 'source': 'YouTube'}})
        self.assertTrue(server.vortex._finish(job_id, {'filename': filename}))
        return content

    def ticket(self, job_id, owner=None):
        result = self.client.post('/api/ios/vortex/' + job_id + '/transfer-ticket', headers=owner or self.alice, json={})
        self.assertEqual(result.status_code, 200, result.json)
        return result.json

    def transfer(self, job_id, ticket, buffered=False, **headers):
        return self.client.get('/api/ios/vortex/' + job_id + '/transfer',
            headers={'X-Vision-Transfer': ticket['token'], **headers}, buffered=buffered)

    def test_pkce_exchange_is_exact_origin_account_bound_and_single_use(self):
        started, approved = self.approve()
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertNotIn('credential', approved.get_data(as_text=True))
        self.assertNotIn('verifier', started['authURL'])
        self.assertTrue(started['authURL'].startswith(ios_companion.SIGNIN_PAGE + '#'))
        body = dict(id=started['id'], state=self.state, code=approved.json['code'], verifier='x' * 64)
        invalid = self.client.post('/api/ios/auth/exchange', json=body)
        self.assertEqual(invalid.status_code, 401)
        body['verifier'] = self.verifier
        valid = self.client.post('/api/ios/auth/exchange', json=body)
        self.assertEqual(valid.status_code, 200, valid.json)
        self.assertEqual(valid.json['uid'], 'alice')
        self.assertEqual(valid.json['googleAccessToken'], 'google-access-credential-example')
        self.assertIn('no-store', valid.headers['Cache-Control'])
        self.assertNotIn(started['id'], self.service.pending)
        self.assertEqual(self.client.post('/api/ios/auth/exchange', json=body).status_code, 401)

    def test_google_token_must_match_verified_firebase_account(self):
        started, approved = self.approve(subject='google-bob')
        self.assertEqual(approved.status_code, 401)
        self.assertNotIn('google_access', self.service.pending[started['id']])

    def test_auth_rejects_untrusted_origin_callback_parameters_and_missing_login(self):
        body = dict(challenge=ios_companion.pkce_challenge(self.verifier), state=self.state)
        self.assertEqual(self.client.post('/api/ios/auth/start', json=body, headers={'Origin': 'https://evil.invalid'}).status_code, 403)
        self.assertEqual(self.client.post('/api/ios/auth/start', json={**body, 'callback': 'evil://steal'}).status_code, 400)
        started = self.start()
        approve = dict(id=started['id'], state=self.state, googleAccessToken='some-token-that-is-long')
        self.assertEqual(self.client.post('/api/ios/auth/approve', json=approve).status_code, 401)
        headers = dict(self.alice)
        headers.pop('Origin')
        self.assertEqual(self.client.post('/api/ios/auth/approve', json=approve, headers=headers).status_code, 403)
        self.assertEqual(self.client.post('/api/ios/auth/arbitrary', json={}).status_code, 401)

    def test_expired_state_and_duplicate_approval_are_rejected(self):
        started, approved = self.approve()
        self.assertEqual(approved.status_code, 200)
        _, second = self.approve(started)
        self.assertEqual(second.status_code, 409)
        self.service.pending[started['id']]['expires'] = time.time() - 1
        result = self.client.post('/api/ios/auth/exchange', json=dict(id=started['id'], state=self.state, code=approved.json['code'], verifier=self.verifier))
        self.assertEqual(result.status_code, 401)
        self.assertNotIn(started['id'], self.service.pending)

    def test_start_rate_limit_is_bounded(self):
        for _ in range(12):
            self.start()
        result = self.client.post('/api/ios/auth/start', json=dict(challenge=ios_companion.pkce_challenge(self.verifier), state=self.state))
        self.assertEqual(result.status_code, 429)
        self.assertEqual(len(self.service.pending), 12)

    def test_transfer_ticket_can_be_issued_while_queued_and_stores_only_hash(self):
        job_id = self.enqueue()
        ticket = self.ticket(job_id)
        self.assertNotIn('?', ticket['url'])
        self.assertNotIn(ticket['token'], ticket['url'])
        self.assertEqual(ticket['protocol'], 'VTX1')
        with server.connect_db() as db:
            row = db.execute('SELECT * FROM ios_transfer_tokens').fetchone()
        self.assertEqual(row['token_hash'], ios_companion.digest(ticket['token']))
        self.assertNotIn(ticket['token'], json.dumps(dict(row)))
        self.assertEqual(self.client.post('/api/ios/vortex/' + job_id + '/transfer-ticket', headers=self.bob, json={}).status_code, 404)

    def test_completed_transfer_has_exact_framing_payload_and_releases_lease(self):
        job_id = self.enqueue()
        content = self.complete(job_id)
        ticket = self.ticket(job_id)
        response = self.transfer(job_id, ticket, buffered=True)
        self.assertEqual(response.status_code, 200)
        magic, line, payload = response.data.split(b'\n', 2)
        self.assertEqual(magic, b'VTX1')
        meta = json.loads(line)
        self.assertEqual(meta, dict(type='file', filename='My movie.mp4', mimeType='video/mp4', size=len(content)))
        self.assertEqual(payload, content)
        self.assertFalse(self.service.active)
        self.assertNotIn('firebase:alice', server.account_admin.active_requests)
        with server.connect_db() as db:
            self.assertFalse(self.service.source_leased(db, job_id))

    def test_native_mime_contract_ignores_missing_or_incorrect_os_associations(self):
        expected = {'.m4a': 'audio/mp4', '.mp4': 'video/mp4', '.mov': 'video/quicktime',
                    '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif',
                    '.webp': 'image/webp', '.zip': 'application/zip'}
        job_id = self.enqueue()
        ticket = self.ticket(job_id)
        for suffix, mime in expected.items():
            with self.subTest(suffix=suffix):
                content = self.complete(job_id, filename='media' + suffix)
                with patch('ios_companion.mimetypes.guess_type', return_value=('application/octet-stream', None)) as guess:
                    response = self.transfer(job_id, ticket, buffered=True)
                self.assertEqual(response.status_code, 200)
                _, line, payload = response.data.split(b'\n', 2)
                metadata = json.loads(line)
                self.assertEqual(metadata['mimeType'], mime)
                self.assertTrue(metadata['filename'].endswith(suffix))
                self.assertEqual(payload, content)
                guess.assert_not_called()

    def test_background_stream_waits_without_restarting_and_then_sends_movie(self):
        job_id = self.enqueue()
        ticket = self.ticket(job_id)
        response = self.transfer(job_id, ticket)
        iterator = iter(response.response)
        self.assertEqual(next(iterator), b'VTX1\n')
        progress = json.loads(next(iterator))
        self.assertEqual(progress['type'], 'progress')
        content = b'completed-while-phone-was-asleep'
        with patch.object(self.service.wait, 'wait', side_effect=lambda _: self.complete(job_id, content)):
            remaining = b''.join(iterator)
        metadata, media = remaining.split(b'\n', 1)
        self.assertEqual(json.loads(metadata)['size'], len(content))
        self.assertEqual(media, content)
        response.close()

    def test_stream_caps_preserve_other_server_threads_and_cleanup_on_disconnect(self):
        a = self.enqueue()
        a2 = self.enqueue(request_id='second')
        b = self.enqueue(self.bob, 'bob-job')
        c_headers = self.headers('carol')
        c = self.enqueue(c_headers, 'carol-job')
        ta, ta2, tb, tc = self.ticket(a), self.ticket(a2), self.ticket(b, self.bob), self.ticket(c, c_headers)
        ra = self.transfer(a, ta)
        try:
            second = self.transfer(a2, ta2)
            self.assertEqual(second.status_code, 503)
            self.assertEqual(second.headers['Retry-After'], '30')
            second.close()
            rb = self.transfer(b, tb)
            try:
                third = self.transfer(c, tc)
                self.assertEqual(third.status_code, 503)
                third.close()
                self.assertEqual(len(self.service.active), 2)
                with server.connect_db() as db:
                    self.assertTrue(self.service.source_leased(db, a))
                self.assertEqual(self.client.post('/api/vortex/jobs/' + a + '/cancel', headers=self.alice, json={}).status_code, 409)
                self.assertEqual(self.client.delete('/api/vortex/jobs/' + a, headers=self.alice).status_code, 409)
            finally:
                rb.close()
        finally:
            ra.close()
        self.assertFalse(self.service.active)
        self.assertFalse(server.account_admin.active_requests)

    def test_transfer_capability_cannot_be_reused_for_different_job_or_api(self):
        a, b = self.enqueue(), self.enqueue(request_id='different')
        ticket = self.ticket(a)
        response = self.transfer(b, ticket)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get('/api/vortex/jobs', headers={'X-Vision-Transfer': ticket['token']}).status_code, 401)
        self.assertEqual(self.transfer(a, ticket, Range='bytes=5-').status_code, 416)
        self.assertEqual(self.transfer(a, ticket, Origin='https://evil.invalid').status_code, 403)

    def test_expiry_retry_bound_and_deleted_account_reject_capability(self):
        a = self.enqueue()
        ticket = self.ticket(a)
        with server.connect_db() as db:
            db.execute('UPDATE ios_transfer_tokens SET attempts=?', (ios_companion.MAX_ATTEMPTS,))
        self.assertEqual(self.transfer(a, ticket).status_code, 401)
        with server.connect_db() as db:
            db.execute('UPDATE ios_transfer_tokens SET attempts=0,expires_at=?', (time.time() - 1,))
        self.assertEqual(self.transfer(a, ticket).status_code, 401)
        ticket = self.ticket(a)
        from activity_dashboard import public_id
        with server.connect_db() as db:
            db.execute("INSERT INTO account_deletions(id,uid,state,requested_at) VALUES(?,?,'pending',?)", (public_id('firebase:alice'), 'firebase:alice', time.time()))
        self.assertEqual(self.transfer(a, ticket).status_code, 403)
        self.assertFalse(self.service.active)

    def test_worker_failure_is_error_frame_and_never_a_successful_movie(self):
        a = self.enqueue()
        ticket = self.ticket(a)
        response = self.transfer(a, ticket)
        iterator = iter(response.response)
        self.assertEqual(next(iterator), b'VTX1\n')
        with server.connect_db() as db:
            db.execute("UPDATE vortex_jobs SET status='error',error='Media could not be retrieved' WHERE id=?", (a,))
        error = json.loads(next(iterator))
        self.assertEqual(error, dict(type='error', message='Media could not be retrieved'))
        self.assertEqual(b''.join(iterator), b'')
        response.close()
        self.assertFalse(self.service.active)

    def test_media_size_mismatch_rejected_before_file_frame(self):
        a = self.enqueue()
        self.complete(a)
        ticket = self.ticket(a)
        (server.vortex.root / a / 'media.mp4').write_bytes(b'truncated')
        response = self.transfer(a, ticket, buffered=True)
        magic, error, empty = response.data.split(b'\n', 2)
        self.assertEqual(magic, b'VTX1')
        self.assertEqual(json.loads(error)['type'], 'error')
        self.assertEqual(empty, b'')

    def test_wait_timeout_is_explicit_and_leaves_job_running(self):
        a = self.enqueue()
        ticket = self.ticket(a)
        response = self.transfer(a, ticket)
        iterator = iter(response.response)
        self.assertEqual(next(iterator), b'VTX1\n')
        with patch('ios_companion.time.monotonic', return_value=time.monotonic() + ios_companion.TRANSFER_WAIT_SECONDS + 1):
            error = json.loads(next(iterator))
        self.assertEqual(error['type'], 'error')
        self.assertIn('Reopen Vision', error['message'])
        self.assertEqual(b''.join(iterator), b'')
        response.close()
        self.assertEqual(server.vortex.row(a)['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
