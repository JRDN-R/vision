"""Signed Google-token integration checks for the existing Gemini queue.

Exercises Flask authorization, durable queue rows, owner decisions and worker
selection without making network requests or loading speech/sound models.
"""
import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

import gemini_processing
import server


class GeminiServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({
            'token': 'a' * 48, 'diagnosticToken': 'd' * 48, 'publicAccess': True,
            'firebaseAuth': {'enabled': True, 'projectId': 'visionboard-api'},
        }))
        self.configure()
        self.client = server.app.test_client()
        self.service = server.transcriptions
        self.alice, self.bob, self.owner = (self.headers(uid) for uid in ('alice', 'bob', 'owner'))
        self.project = '/api/projects/project_gemini_alice_01'
        self.bob_project = '/api/projects/project_gemini_bob_0001'
        for path, headers in ((self.project, self.alice), (self.bob_project, self.bob)):
            result = self.client.put(path, headers=headers,
                                     json={'revision': 0, 'project': {'title': 'Test project', 'nodes': []}})
            self.assertEqual(result.status_code, 200, result.json)
        (server.app.config['DATA_DIR'] / 'activity-admins.json').write_text(
            json.dumps({'uids': ['firebase:owner']}))
        self.body = {
            'clientRequestId': 'same-client-request', 'sourceName': 'meeting.wav', 'provider': 'gemini',
            'sections': [{'start': 0, 'end': 1, 'mimeType': 'audio/wav',
                          'audioData': 'data:audio/wav;base64,' + base64.b64encode(b'RIFF-test-audio').decode()}],
        }
        # Any accidental real transport fails these integration tests immediately.
        self.transport = self.enterContext(patch.object(
            gemini_processing.requests, 'post', side_effect=AssertionError('Unexpected external API request')))

    def configure(self):
        server.configure(self.config)
        server.app.config['TESTING'] = True
        identity = server.app.config['FIREBASE_IDENTITY']
        identity._keys = {'test': self.signing_key.public_key()}
        identity._expires = time.monotonic() + 3600
        server.transcriptions.stop.clear()

    def headers(self, uid):
        now = int(time.time())
        claims = {
            'sub': uid, 'aud': 'visionboard-api',
            'iss': 'https://securetoken.google.com/visionboard-api',
            'exp': now + 3600, 'iat': now - 1, 'auth_time': now - 10,
            'firebase': {'sign_in_provider': 'google.com'},
            'name': uid.title(), 'email': uid + '@example.test', 'email_verified': True,
        }
        token = jwt.encode(claims, self.signing_key, algorithm='RS256', headers={'kid': 'test'})
        return {'Authorization': 'Bearer ' + token, 'Origin': 'https://jrdn-r.github.io'}

    def submit(self, body=None, headers=None, project=None):
        return self.client.post((project or self.project) + '/transcriptions',
                                headers=headers or self.alice, json=body or self.body)

    def status(self, job_id, headers=None):
        return self.client.get(self.project + '/transcriptions/' + job_id, headers=headers or self.alice)

    def user_id(self, name='Alice'):
        response = self.client.get('/api/admin/gemini', headers=self.owner)
        self.assertEqual(response.status_code, 200, response.json)
        return next(item['userId'] for item in response.json['accessRequests'] if item['name'] == name)

    def decide(self, decision):
        response = self.client.post('/api/admin/gemini/access', headers=self.owner,
                                    json={'userId': self.user_id(), 'decision': decision})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def test_pending_submission_survives_restart_without_a_gemini_call(self):
        receipt = self.submit()
        self.assertEqual(receipt.status_code, 202, receipt.json)
        job_id = receipt.json['id']
        self.assertEqual(receipt.json['status'], 'approval_waiting')
        self.assertEqual(receipt.json['phase'], 'Waiting for Gemini approval')
        self.assertEqual(self.submit().json['id'], job_id)
        self.assertEqual(self.client.get('/api/gemini/access', headers=self.alice).json['status'], 'pending')
        self.assertEqual(self.service.row(job_id)['requester_uid'], 'firebase:alice')
        with patch.object(self.service, 'run_gemini') as runner:
            self.assertFalse(self.service.work_once())
            self.configure()
            self.service.recover()
            self.assertEqual(self.status(job_id).json['status'], 'approval_waiting')
            self.assertFalse(self.service.work_once())
            runner.assert_not_called()
        self.transport.assert_not_called()
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gemini_access').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gemini_usage').fetchone()[0], 0)

    def test_only_owner_can_approve_and_approval_releases_same_job_account_wide(self):
        job_id = self.submit().json['id']
        user_id = self.user_id()
        for headers in (self.alice, self.bob):
            self.assertEqual(self.client.get('/api/admin/gemini', headers=headers).status_code, 403)
            self.assertEqual(self.client.get('/api/admin/gemini/pricing', headers=headers).status_code, 403)
            response = self.client.post('/api/admin/gemini/access', headers=headers,
                                        json={'userId': user_id, 'decision': 'approved'})
            self.assertEqual(response.status_code, 403)
        decision = self.decide('approved')
        self.assertEqual(decision['releasedJobs'], 1)
        self.assertEqual(self.status(job_id).json['status'], 'queued')
        result = {'text': '[00:00:00.000] Hello.',
                  'sections': [{'start': 0.0, 'end': 1.0, 'text': '[00:00:00.000] Hello.'}]}
        with patch.object(self.service, 'run_gemini', return_value=result) as runner:
            self.assertTrue(self.service.work_once())
            self.assertFalse(self.service.work_once())
        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[0]['id'], job_id)
        self.assertEqual(self.status(job_id).json['result'], result)
        self.configure()
        future = self.submit({**self.body, 'clientRequestId': 'next-project-job'})
        self.assertEqual(future.status_code, 202, future.json)
        self.assertEqual(future.json['status'], 'queued')
        self.assertNotEqual(future.json['id'], job_id)
        self.assertEqual(self.client.get('/api/gemini/access', headers=self.alice).json['status'], 'approved')
        self.transport.assert_not_called()

    def test_revocation_and_denial_hold_existing_and_future_jobs_without_calls(self):
        job_id = self.submit().json['id']
        self.decide('approved')
        self.decide('revoked')
        self.assertEqual(self.status(job_id).json['status'], 'approval_waiting')
        self.assertEqual(self.status(job_id).json['phase'], 'Gemini access revoked')
        future = self.submit({**self.body, 'clientRequestId': 'after-revocation'})
        self.assertEqual(future.status_code, 202, future.json)
        self.assertEqual(future.json['status'], 'approval_waiting')
        with patch.object(self.service, 'run_gemini') as runner:
            self.assertFalse(self.service.work_once())
            # Even a stale queued row is rechecked against account approval.
            self.service.update(job_id, status='queued')
            self.assertTrue(self.service.work_once())
            self.assertEqual(self.status(job_id).json['status'], 'approval_waiting')
            self.decide('denied')
            response = self.client.post('/api/gemini/access', headers=self.alice)
            self.assertEqual(response.json['status'], 'denied')
            self.assertFalse(self.service.work_once())
            runner.assert_not_called()
        self.transport.assert_not_called()
        self.assertEqual(self.decide('approved')['releasedJobs'], 2)
        self.assertEqual(self.status(job_id).json['id'], job_id)

    def test_bob_cannot_read_cancel_or_submit_to_alice_project(self):
        alice_job = self.submit().json['id']
        path = self.project + '/transcriptions/' + alice_job
        self.assertEqual(self.status(alice_job, self.bob).status_code, 404)
        self.assertEqual(self.client.delete(path, headers=self.bob).status_code, 404)
        self.assertEqual(self.submit(headers=self.bob).status_code, 404)
        bob_receipt = self.submit(headers=self.bob, project=self.bob_project)
        self.assertEqual(bob_receipt.status_code, 202, bob_receipt.json)
        self.assertNotEqual(bob_receipt.json['id'], alice_job)
        self.assertEqual(self.service.row(bob_receipt.json['id'])['requester_uid'], 'firebase:bob')
        self.assertEqual(self.decide('approved')['releasedJobs'], 1)
        self.assertEqual(self.service.row(bob_receipt.json['id'])['status'], 'approval_waiting')
        self.assertEqual(self.client.get('/api/gemini/access', headers=self.bob).json['status'], 'pending')

    def test_local_provider_alias_preserves_whisper_receipts_and_worker(self):
        body = {**self.body, 'provider': 'local'}
        with patch.object(self.service, 'capability', return_value={'ready': True}):
            receipt = self.submit(body)
            self.assertEqual(receipt.status_code, 202, receipt.json)
            job_id = receipt.json['id']
            self.assertEqual(self.service.row(job_id)['provider'], 'whisper')
            for provider in ('whisper', None):
                legacy = {key: value for key, value in body.items() if key != 'provider'}
                if provider:
                    legacy['provider'] = provider
                duplicate = self.submit(legacy)
                self.assertEqual(duplicate.status_code, 202, duplicate.json)
                self.assertEqual(duplicate.json['id'], job_id)
            self.assertEqual(self.submit(self.body).status_code, 409)
            result = {'text': 'Local speech.', 'sections': [{'start': 0, 'end': 1, 'text': 'Local speech.'}]}
            with patch.object(self.service, 'run_local', return_value=result) as local, \
                    patch.object(self.service, 'run_gemini') as gemini:
                self.assertTrue(self.service.work_once())
            local.assert_called_once()
            gemini.assert_not_called()
        self.assertEqual(self.status(job_id).json['status'], 'complete')
        self.assertEqual(self.client.get('/api/gemini/access', headers=self.alice).json['status'], 'unrequested')
        self.transport.assert_not_called()


if __name__ == '__main__':
    unittest.main()
