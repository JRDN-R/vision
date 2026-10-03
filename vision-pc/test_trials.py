"""Offline guest-lease authorization, processing, expiry and cleanup regressions."""
import base64
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import server
import sessions


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'token': 'x' * 48, 'backendUrl': 'https://vision.example.ts.net',
            'publicAccess': True, 'firebaseAuth': {'enabled': True, 'projectId': 'visionboard-api'}}))
        server.configure(self.config)
        server.app.config['TESTING'] = True
        server.trials.inflight.clear()
        self.client = server.app.test_client()
        self.origin = {'Origin': 'https://jrdn-r.github.io'}

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, path, **kwargs):
        response = self.client.open(path, method=method, **kwargs)
        status, data = response.status_code, response.get_json(silent=True)
        response.close()
        return status, data

    def start(self, device='a', ip='198.51.100.1', **kwargs):
        return self.call('POST', '/api/trial/start', json={'deviceId': device * 64},
            headers=kwargs.pop('headers', self.origin), environ_base={'REMOTE_ADDR': ip}, **kwargs)

    def headers(self, lease):
        return {**self.origin, 'Authorization': 'Bearer ' + lease['token'], 'X-Vision-Project-Key': lease['project']['key']}

    def expire(self, lease):
        with server.connect_db() as db:
            db.execute('UPDATE trial_visits SET expires_at=? WHERE id=?', (time.time() - 1, lease['id']))

    def test_server_clock_resume_restart_and_hashed_receipt(self):
        status, lease = self.start()
        self.assertEqual(status, 201)
        self.assertAlmostEqual(lease['expiresAt'] - lease['serverNow'], 300, delta=1)
        status, resumed = self.start()
        self.assertEqual(status, 200)
        self.assertEqual(resumed['expiresAt'], lease['expiresAt'])
        self.assertEqual(resumed['token'], lease['token'])
        server.configure(self.config)
        self.assertEqual(self.start()[1]['token'], lease['token'])
        with server.connect_db() as db:
            row = dict(db.execute('SELECT * FROM trial_visits').fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 0)
        self.assertNotIn('198.51.100.1', str(row))
        self.assertNotIn('a' * 64, str(row))
        self.assertNotIn(lease['token'], str(row))

    def test_repeat_network_device_forgery_and_proxy_spoof_rejected(self):
        _, lease = self.start()
        self.assertEqual(self.start('b')[0], 403)
        self.assertEqual(self.start('b', headers={**self.origin, 'X-Forwarded-For': '203.0.113.55'})[0], 403)
        self.expire(lease)
        self.assertEqual(self.start('a', '203.0.113.1')[0], 403)
        self.assertEqual(self.call('GET', '/api/health', headers=self.headers(lease))[0], 403)
        forged = {**lease, 'token': lease['token'][:-1] + ('a' if lease['token'][-1] != 'a' else 'b')}
        self.assertEqual(self.call('GET', '/api/health', headers=self.headers(forged))[0], 401)
        self.assertEqual(self.start('c', '203.0.113.3')[0], 201)
        self.assertEqual(self.start('d', '127.0.0.1')[0], 503)
        self.assertEqual(self.start('d', headers={'Origin': 'https://evil.example'})[0], 403)

    def test_guest_cannot_save_project_keys_or_read_other_jobs(self):
        _, one = self.start()
        _, two = self.start('b', '198.51.100.2')
        h1, h2 = self.headers(one), self.headers(two)
        pid = one['project']['id']
        for method, path in [('PUT', '/api/projects/' + pid), ('GET', '/api/projects'),
                             ('GET', '/api/account/openai-key'), ('PUT', '/api/account/openai-key'),
                             ('POST', '/api/projects/' + pid + '/claim'), ('GET', '/api/openai/models')]:
            self.assertEqual(self.call(method, path, headers=h1, json={})[0], 403)
        self.assertEqual(self.call('GET', '/api/projects/' + pid + '/runs', headers=h2)[0], 403)
        status, job = self.call('POST', '/api/youtube', headers=h1,
            json={'url': 'https://youtu.be/abcdefghijk', 'clientRequestId': 'same'})
        self.assertEqual(status, 202)
        self.assertEqual(self.call('GET', '/api/jobs/' + job['id'], headers=h2)[0], 404)
        status, second = self.call('POST', '/api/youtube', headers=h2,
            json={'url': 'https://youtu.be/abcdefghijk', 'clientRequestId': 'same'})
        self.assertEqual(status, 202)
        self.assertNotEqual(job['id'], second['id'])
        self.assertEqual(self.call('GET', '/api/health')[0], 401)

    def test_whisper_sound_video_and_own_key_runs_without_saved_project(self):
        _, lease = self.start()
        base, headers = '/api/projects/' + lease['project']['id'], self.headers(lease)
        audio = {'clientRequestId': 'sound-1', 'sourceName': 'trial.mp3', 'includeSoundEvents': True,
                 'sections': [{'start': 0, 'end': 1, 'mimeType': 'audio/mpeg',
                     'audioData': 'data:audio/mpeg;base64,' + base64.b64encode(b'ID3audio').decode()}]}
        with patch.object(server.transcriptions, 'capability', return_value={'ready': True}), \
             patch.object(server.transcriptions, 'sound_capability', return_value={'ready': True}):
            status, speech = self.call('POST', base + '/transcriptions', headers=headers, json=audio)
        self.assertEqual(status, 202, speech)
        with patch.object(server.uploaded_media, 'capability', return_value={'ready': True}):
            status, video = self.call('POST', base + '/media', headers=headers,
                data={'requestId': 'video-1', 'file': (io.BytesIO(b'fake video'), 'trial.mp4')})
        self.assertEqual(status, 202, video)
        with patch.object(sessions, 'protect_secret', return_value=b'test-only-cipher'):
            status, run = self.call('POST', base + '/runs', headers={**headers, 'X-OpenAI-Key': 'sk-test-not-real'},
                data={'options': json.dumps({'clientRequestId': 'run-1', 'message': 'Test only'})})
        self.assertEqual(status, 202, run)
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM account_credentials').fetchone()[0], 0)
        self.expire(lease)
        with patch.object(server.sessions, 'call') as upstream:
            server.sessions.work_once()
            upstream.assert_not_called()
        server.trials.sweep()
        for root in (server.transcriptions.root, server.uploaded_media.root):
            self.assertEqual(list(root.iterdir()), [])
        with server.connect_db() as db:
            for table in ('local_transcriptions', 'uploaded_media', 'project_runs', 'run_events', 'run_artifacts'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0, table)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM trial_visits').fetchone()[0], 1)

    def test_cleanup_waits_for_writers_and_cancels_first(self):
        _, lease = self.start()
        status, job = self.call('POST', '/api/youtube', headers=self.headers(lease),
            json={'url': 'https://youtu.be/abcdefghijk'})
        result = server.app.config['DATA_DIR'] / 'results' / (job['id'] + '.json')
        result.write_text('temporary content')
        self.expire(lease)
        with server.YOUTUBE_WORKER_LOCK:
            server.trials.sweep()
            self.assertTrue(result.exists())
            with server.connect_db() as db:
                self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()[0], 'error')
        server.trials.sweep()
        self.assertFalse(result.exists())
        self.assertEqual(self.start()[0], 403)
        server.configure(self.config)
        self.assertEqual(self.start()[0], 403)

    def test_early_signin_end_revokes_and_does_not_reset(self):
        _, lease = self.start()
        self.assertEqual(self.call('POST', '/api/trial/end', headers=self.headers(lease))[0], 200)
        self.assertEqual(self.call('GET', '/api/trial/status', headers=self.headers(lease))[0], 403)
        self.assertEqual(self.start()[0], 403)
        server.trials.sweep()
        self.assertEqual(server.trials.inflight, {})

    def test_ipv6_prefix_and_max_concurrent_guest_slots(self):
        self.assertEqual(self.start('a', '2001:db8:1234::1')[0], 201)
        self.assertEqual(self.start('b', '2001:db8:1234::99')[0], 403)
        for digit in range(2, 6):
            self.assertEqual(self.start(str(digit), '198.51.100.' + str(digit))[0], 201)
        self.assertEqual(self.start('f', '198.51.100.20')[0], 429)
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM trial_visits').fetchone()[0], 5)


if __name__ == '__main__':
    unittest.main()
