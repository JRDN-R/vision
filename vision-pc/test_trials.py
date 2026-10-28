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

    def test_guest_start_is_closed_even_if_old_configuration_enables_it(self):
        for enabled in (True, False):
            config=json.loads(self.config.read_text());config['anonymousTrialEnabled']=enabled
            self.config.write_text(json.dumps(config));server.configure(self.config)
            status,body=self.start()
            self.assertEqual(status,403)
            self.assertEqual(body['code'],'membership-required')
            self.assertFalse(server.app.config['TRIAL_ENABLED'])

    def test_previous_guest_token_cannot_access_projects_or_processing(self):
        headers={**self.origin,'Authorization':'Bearer trial_'+'a'*24+'_'+'b'*64}
        for method,path in [('GET','/api/projects'),('POST','/api/youtube'),('POST','/api/trial/end')]:
            status,body=self.call(method,path,headers=headers)
            self.assertEqual(status,403)
            self.assertEqual(body['code'],'membership-required')

if __name__=='__main__':unittest.main()
