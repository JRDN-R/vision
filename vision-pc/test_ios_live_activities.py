"""Mocked APNs tests; no Apple or external notification is sent."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
import jwt

import server
import ios_live_activities
import test_ios_companion as companion_tests


class IOSLiveActivitiesTests(unittest.TestCase):
    setUpClass = classmethod(companion_tests.IOSCompanionTests.setUpClass.__func__)
    headers = companion_tests.IOSCompanionTests.headers
    enqueue = companion_tests.IOSCompanionTests.enqueue
    complete = companion_tests.IOSCompanionTests.complete

    def setUp(self):
        companion_tests.IOSCompanionTests.setUp(self)
        self.push = server.ios_live
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.key_path = self.root / 'private-apple-key.p8'
        self.key_path.write_bytes(self.key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
        self.config = dict(iosAPNs=dict(enabled=True, teamId='TEAM123456', keyId='KEY1234567',
                                     keyPath=str(self.key_path), bundleId='com.jordan.vision', environment='sandbox'))
        self.client_mock = Mock()
        self.client_mock.post.return_value = Mock(status_code=200)
        self.protect = patch.object(self.push, 'encrypt', side_effect=lambda value: b'locked:' + value.encode()[::-1])
        self.unprotect = patch.object(self.push, 'decrypt', side_effect=lambda value: bytes(value)[7:][::-1].decode())
        self.protect.start()
        self.unprotect.start()
        with patch('httpx.Client', return_value=self.client_mock):
            self.push.initialize(self.config, self.root / 'config.json')
        self.assertTrue(self.push.ready)

    def tearDown(self):
        self.push.close()
        self.protect.stop()
        self.unprotect.stop()
        companion_tests.IOSCompanionTests.tearDown(self)

    def register(self, job_id, owner=None, activity='activity-1', token='ab' * 32, **extra):
        return self.client.post('/api/ios/live-activities', headers=owner or self.alice,
            json=dict(jobID=job_id, recordID='record-' + activity, activityID=activity, token=token, **extra))

    def rows(self):
        with server.connect_db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM ios_live_activities')]

    def test_disabled_setup_has_no_optional_dependency_or_private_key_requirement(self):
        with patch('builtins.__import__', wraps=__import__) as imports:
            self.push.initialize({}, self.root / 'config.json')
        self.assertFalse(self.push.enabled)
        self.assertFalse(self.push.ready)
        self.assertFalse(any(call.args[0] == 'httpx' for call in imports.call_args_list))
        response = self.client.get('/api/ios/capabilities', headers=self.alice)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['framedBackgroundTransfer'])
        self.assertFalse(response.json['liveActivityPush']['ready'])
        self.assertFalse(response.json['liveActivityPush']['pushToStart'])
        self.assertEqual(self.register(self.enqueue()).status_code, 503)

    def test_invalid_optional_configuration_does_not_disable_vision(self):
        with patch.object(self.push.app.logger, 'warning'):
            self.push.initialize({'iosAPNs': {'enabled': True, 'environment': 'attacker.invalid'}}, self.root / 'config.json')
        self.assertTrue(self.push.enabled)
        self.assertFalse(self.push.ready)
        self.assertEqual(self.client.get('/api/status').status_code, 200)

    def test_registration_is_owned_encrypted_and_never_accepts_client_topic(self):
        job = self.enqueue()
        self.assertEqual(self.register(job, self.bob).status_code, 404)
        self.assertEqual(self.register(job, bundleId='org.attacker.app').status_code, 400)
        response = self.register(job)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertNotIn('abab', response.get_data(as_text=True))
        row = self.rows()[0]
        self.assertEqual(row['uid'], 'firebase:alice')
        self.assertNotIn(('ab' * 32).encode(), bytes(row['token_cipher']))
        self.assertNotEqual(row['token_hash'], 'ab' * 32)
        self.assertLessEqual(row['expires_at'], time.time() + ios_live_activities.MAX_LIFETIME)

    def test_apns_uses_fixed_host_topic_es256_and_unix_content_state(self):
        job = self.enqueue()
        self.assertEqual(self.register(job).status_code, 200)
        self.assertEqual(self.push.process_once(), 1)
        args, kwargs = self.client_mock.post.call_args
        self.assertEqual(args[0], 'https://api.sandbox.push.apple.com/3/device/' + 'ab' * 32)
        headers, aps = kwargs['headers'], kwargs['json']['aps']
        self.assertEqual(headers['apns-topic'], 'com.jordan.vision.push-type.liveactivity')
        self.assertEqual(headers['apns-push-type'], 'liveactivity')
        self.assertEqual(headers['apns-priority'], '5')
        token = headers['authorization'].removeprefix('bearer ')
        claims = jwt.decode(token, self.key.public_key(), algorithms=['ES256'])
        self.assertEqual(claims['iss'], 'TEAM123456')
        self.assertEqual(jwt.get_unverified_header(token)['kid'], 'KEY1234567')
        self.assertEqual(aps['event'], 'update')
        self.assertEqual(set(aps['content-state']), {'title', 'phase', 'progress', 'status', 'updatedAt'})
        self.assertAlmostEqual(aps['content-state']['updatedAt'], time.time(), delta=3)
        self.assertGreater(aps['stale-date'], aps['timestamp'])
        self.assertEqual(aps['content-state']['status'], 'queued')

    def test_progress_is_throttled_and_unchanged_updates_only_heartbeat(self):
        job = self.enqueue()
        self.register(job)
        self.assertEqual(self.push.process_once(), 1)
        self.assertEqual(self.push.process_once(), 0)
        with server.connect_db() as db:
            db.execute("UPDATE vortex_jobs SET status='processing',phase='Downloading',progress=50 WHERE id=?", (job,))
        self.assertEqual(self.push.process_once(), 0)
        with server.connect_db() as db:
            db.execute('UPDATE ios_live_activities SET last_sent=?', (time.time() - 16,))
        self.assertEqual(self.push.process_once(), 1)
        self.assertEqual(self.client_mock.post.call_args.kwargs['json']['aps']['content-state']['progress'], .5)
        with server.connect_db() as db:
            db.execute('UPDATE ios_live_activities SET last_sent=?', (time.time() - 30,))
        self.assertEqual(self.push.process_once(), 0)
        with server.connect_db() as db:
            db.execute('UPDATE ios_live_activities SET last_sent=?', (time.time() - 61,))
        self.assertEqual(self.push.process_once(), 1)

    def test_server_complete_does_not_claim_saved_or_end_phone_activity(self):
        job = self.enqueue()
        self.register(job)
        self.complete(job)
        self.assertEqual(self.push.process_once(), 1)
        aps = self.client_mock.post.call_args.kwargs['json']['aps']
        self.assertEqual(aps['event'], 'update')
        self.assertEqual(aps['content-state']['phase'], 'Ready for iPhone')
        self.assertEqual(aps['content-state']['status'], 'processing')
        self.assertIsNone(aps['content-state']['progress'])
        self.assertEqual(self.rows()[0]['done'], 1)
        self.assertEqual(self.push.process_once(), 0)

    def test_failure_ends_activity_with_higher_priority(self):
        job = self.enqueue()
        self.register(job)
        with server.connect_db() as db:
            db.execute("UPDATE vortex_jobs SET status='error',error='Media unavailable' WHERE id=?", (job,))
        self.assertEqual(self.push.process_once(), 1)
        request = self.client_mock.post.call_args.kwargs
        self.assertEqual(request['headers']['apns-priority'], '10')
        self.assertEqual(request['json']['aps']['event'], 'end')
        self.assertEqual(request['json']['aps']['content-state']['status'], 'failed')

    def test_inactive_apple_token_is_removed_and_network_errors_are_bounded(self):
        job = self.enqueue()
        self.register(job)
        self.client_mock.post.return_value.status_code = 410
        self.assertEqual(self.push.process_once(), 0)
        self.assertFalse(self.rows())
        self.register(job)
        self.client_mock.post.side_effect = RuntimeError('https://api.push.apple.com/3/device/secret-token')
        for attempt in range(10):
            with server.connect_db() as db:
                db.execute('UPDATE ios_live_activities SET next_attempt=0')
            self.push.process_once()
        row = self.rows()[0]
        self.assertEqual(row['failures'], 10)
        self.assertEqual(row['done'], 1)
        self.assertEqual(self.push.process_once(), 0)

    def test_http_client_logs_drop_apns_destinations_and_authorization(self):
        filter_ = ios_live_activities._APNsLogFilter()
        for text in ('HTTP Request: POST https://api.push.apple.com/3/device/abcd',
                     'request path /3/device/abcd', 'authorization: bearer secret'):
            record = logging.LogRecord('httpx', logging.INFO, '', 1, text, (), None)
            self.assertFalse(filter_.filter(record))
        safe = logging.LogRecord('httpx', logging.INFO, '', 1, 'HTTP Request: GET https://example.com', (), None)
        self.assertTrue(filter_.filter(safe))

    def test_activity_capacity_token_rotation_and_owner_only_removal(self):
        job = self.enqueue()
        for index in range(10):
            result = self.register(job, activity='activity-' + str(index), token=f'{index + 1:064x}')
            self.assertEqual(result.status_code, 200)
        self.assertEqual(self.register(job, activity='overflow', token='ef' * 32).status_code, 429)
        before = self.rows()[0]['expires_at']
        self.assertEqual(self.register(job, activity='activity-0', token='cd' * 32).status_code, 200)
        self.assertEqual(len(self.rows()), 10)
        self.assertEqual(self.rows()[0]['expires_at'], before)
        self.client.delete('/api/ios/live-activities/activity-0', headers=self.bob)
        self.assertEqual(len(self.rows()), 10)
        self.client.delete('/api/ios/live-activities/activity-0', headers=self.alice)
        self.assertEqual(len(self.rows()), 9)

    def test_expired_and_removed_account_registrations_are_purged(self):
        job = self.enqueue()
        self.register(job)
        with server.connect_db() as db:
            db.execute('UPDATE ios_live_activities SET expires_at=?', (time.time() - 1,))
        self.push.process_once()
        self.assertFalse(self.rows())
        self.register(job)
        from activity_dashboard import public_id
        with server.connect_db() as db:
            db.execute("INSERT INTO account_deletions(id,uid,state,requested_at) VALUES(?,?,'pending',?)", (public_id('firebase:alice'), 'firebase:alice', time.time()))
        self.push.process_once()
        self.assertFalse(self.rows())
        self.client_mock.post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
