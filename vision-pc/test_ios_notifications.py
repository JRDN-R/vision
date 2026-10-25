"""Account approval alerts with real auth/SQLite and mocked Apple transport."""
import time
import unittest
from unittest.mock import patch

import server
from gemini_access import public_id
import test_ios_live_activities as live_tests


class IOSNotificationsTests(unittest.TestCase):
    setUpClass = live_tests.IOSLiveActivitiesTests.__dict__['setUpClass']
    headers = live_tests.IOSLiveActivitiesTests.headers
    setUp = live_tests.IOSLiveActivitiesTests.setUp
    tearDown = live_tests.IOSLiveActivitiesTests.tearDown
    installation = '12345678-1234-1234-1234-123456789abc'

    def register(self, owner=None, token='ab' * 32, installation=None, method='put', **extra):
        return getattr(self.client, method)('/api/ios/notification-device', headers=owner or self.alice,
            json=dict(token=token, installation=installation or self.installation, **extra))

    def rows(self):
        with server.connect_db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM ios_notification_devices')]

    def decide(self, decision='approved', uid='firebase:alice'):
        policy = server.app.config['GEMINI_ACCESS']
        policy.request_access(uid)
        policy.decide(public_id(uid), decision, 'firebase:owner')

    def test_disabled_free_build_keeps_approval_polling(self):
        self.push.initialize({}, self.root / 'config.json')
        self.assertEqual(self.register().status_code, 503)
        self.decide()
        result = self.client.get('/api/account/upgrade', headers=self.alice)
        self.assertTrue(result.json['approved'])
        self.assertGreater(result.json['decisionID'], 0)
        self.assertEqual(server.ios_notifications.process_once(), 0)
        self.client_mock.post.assert_not_called()

    def test_registration_auth_validation_and_encrypted_storage(self):
        self.assertEqual(self.client.put('/api/ios/notification-device', json={}).status_code, 401)
        evil = dict(self.alice, Origin='https://evil.invalid')
        self.assertEqual(self.register(evil).status_code, 403)
        for extra in [dict(token='wrong'), dict(installation='-' * 36), dict(bundleId='attacker')]:
            self.assertEqual(self.register(**extra).status_code, 400)
        self.assertEqual(self.register().status_code, 200)
        row = self.rows()[0]
        self.assertEqual(row['uid'], 'firebase:alice')
        self.assertNotIn(('ab' * 32).encode(), bytes(row['token_cipher']))
        self.assertNotEqual(row['token_hash'], 'ab' * 32)
        self.assertLessEqual(row['expires'], time.time() + 90 * 86400)
        self.register(self.bob, method='delete')
        self.assertEqual(len(self.rows()), 1)
        self.register(method='delete')
        self.assertFalse(self.rows())

    def test_user_approval_sends_one_alert_with_normal_app_topic(self):
        self.register()
        self.decide(uid='firebase:bob')
        self.assertEqual(server.ios_notifications.process_once(), 0)
        self.decide()
        self.assertEqual(server.ios_notifications.process_once(), 1)
        args, kwargs = self.client_mock.post.call_args
        self.assertEqual(args[0], 'https://api.sandbox.push.apple.com/3/device/' + 'ab' * 32)
        self.assertEqual(kwargs['headers']['apns-topic'], 'com.jordan.vision')
        self.assertEqual(kwargs['headers']['apns-push-type'], 'alert')
        self.assertEqual(kwargs['json']['uid'], 'alice')
        self.assertEqual(kwargs['json']['kind'], 'upgrade')
        self.assertEqual(kwargs['json']['aps']['alert']['title'], 'Account upgrade approved')
        decision = self.client.get('/api/account/upgrade', headers=self.alice).json['decisionID']
        self.assertEqual(kwargs['json']['decisionID'], decision)
        self.assertEqual(server.ios_notifications.process_once(), 0)
        self.register()  # Routine renewal must not replay an alert.
        self.assertEqual(server.ios_notifications.process_once(), 0)
        self.assertEqual(self.client_mock.post.call_count, 1)

    def test_retry_survives_restart_but_revocation_suppresses_pending_alert(self):
        self.register(); self.decide()
        self.client_mock.post.return_value.status_code = 503
        self.assertEqual(server.ios_notifications.process_once(), 0)
        self.assertEqual(self.rows()[0]['failures'], 1)
        self.assertGreater(self.rows()[0]['next_attempt'], time.time())
        server.ios_notifications.initialize()
        self.assertEqual(server.ios_notifications.process_once(), 0)
        with server.connect_db() as db:
            db.execute('UPDATE ios_notification_devices SET next_attempt=0')
        self.client_mock.post.return_value.status_code = 200
        self.assertEqual(server.ios_notifications.process_once(), 1)
        self.decide(); self.decide('revoked')
        self.client_mock.post.reset_mock()
        self.assertEqual(server.ios_notifications.process_once(), 0)
        self.client_mock.post.assert_not_called()
        self.decide()
        self.assertEqual(server.ios_notifications.process_once(), 1)

    def test_token_rotation_account_switch_and_conflicting_installation(self):
        self.register()
        self.assertEqual(self.register(self.bob, installation='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa').status_code, 409)
        self.decide()
        self.register(token='cd' * 32)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(server.ios_notifications.process_once(), 1)
        self.assertTrue(self.client_mock.post.call_args.args[0].endswith('cd' * 32))
        self.register(self.bob, token='cd' * 32)
        self.decide()
        self.assertEqual(server.ios_notifications.process_once(), 0)
        self.decide(uid='firebase:bob')
        self.assertEqual(server.ios_notifications.process_once(), 1)
        self.assertEqual(self.client_mock.post.call_args.kwargs['json']['uid'], 'bob')

    def test_expired_invalid_and_deleted_account_registrations(self):
        self.register(); self.decide()
        with server.connect_db() as db:
            db.execute('UPDATE ios_notification_devices SET expires=0')
        self.assertEqual(server.ios_notifications.process_once(), 0)
        self.assertFalse(self.rows())
        self.register(); self.decide()
        self.client_mock.post.return_value.status_code = 410
        self.assertEqual(server.ios_notifications.process_once(), 0)
        self.assertFalse(self.rows())
        self.register(); self.decide()
        self.client_mock.post.reset_mock()
        with patch.object(self.push.accounts, 'blocked', return_value=True):
            self.assertEqual(server.ios_notifications.process_once(), 0)
        self.assertFalse(self.rows())
        self.client_mock.post.assert_not_called()

    def test_idle_devices_do_not_starve_new_approvals(self):
        self.register()
        with server.connect_db() as db:
            row = self.rows()[0]
            for index in range(101):
                db.execute('INSERT INTO ios_notification_devices VALUES(?,?,?,?,?,?,?,?)',
                    (str(index), 'firebase:idle' + str(index), self.installation, row['token_cipher'], 0, row['expires'], 0, 0))
        self.decide()
        self.assertEqual(server.ios_notifications.process_once(), 1)


if __name__ == '__main__':
    unittest.main()
