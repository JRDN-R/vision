"""Account-directory, permanent deletion, token-revocation and retry tests.

Uses temporary SQLite/files, fake Firebase records and an intercepted Admin SDK.
No real user or Firebase project is modified.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import server
from activity_dashboard import public_id


class AccountAdministrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        config = root / 'config.json'
        config.write_text(json.dumps({
            'token': 'k'*48, 'publicAccess': False,
            'firebaseAuth': {'enabled': True, 'projectId': 'visionboard-api'},
        }), encoding='utf-8')
        server.configure(config)
        server.app.config['TESTING'] = True
        self.client = server.app.test_client()
        self.service = server.account_admin
        self.uid = 'firebase:alice'
        self.admin = 'firebase:owner'
        self.email = 'alice@example.test'
        self.fake_app = object()
        self.admin_headers = {'Authorization': 'Bearer owner', 'Origin': 'https://jrdn-r.github.io'}
        self.alice_headers = {'Authorization': 'Bearer alice', 'Origin': 'https://jrdn-r.github.io'}
        root = server.app.config['DATA_DIR']
        (root / 'activity-admins.json').write_text(json.dumps({'uids': [self.admin]}), encoding='utf-8')

        def verify(token):
            if token not in ('alice','owner'):
                raise AssertionError('Only test tokens are expected')
            return {'sub':token, 'name':'Alice Sample' if token=='alice' else 'Owner Example',
                    'email': self.email if token=='alice' else 'owner@example.test',
                    'auth_time':int(time.time())-3,'firebase':{'sign_in_provider':'google.com'}}
        identity = server.app.config['FIREBASE_IDENTITY']
        self.mock_verify = patch.object(identity, 'verify', side_effect=verify)
        self.mock_verify.start()
        self.addCleanup(self.mock_verify.stop)
        self.assertEqual(self.client.get('/api/health', headers=self.alice_headers).status_code, 200)
        self.assertEqual(self.client.get('/api/health', headers=self.admin_headers).status_code, 200)

    def fake_users(self):
        def item(uid, name, email):
            return SimpleNamespace(uid=uid, display_name=name, email=email,
                                   provider_data=[SimpleNamespace(provider_id='google.com')])
        return [item('owner','Owner Example','owner@example.test'),
                item('alice','Alice Sample',self.email),
                item('unseen','Unseen User','unseen@example.test')]

    def admin_sdk(self):
        app = patch.object(self.service, 'firebase', return_value=self.fake_app)
        users = patch.object(self.service, 'firebase_users', side_effect=self.fake_users)
        deletion = patch('firebase_admin.auth.delete_user')
        app.start()
        users.start()
        fake_delete = deletion.start()
        self.addCleanup(app.stop)
        self.addCleanup(users.stop)
        self.addCleanup(deletion.stop)
        return fake_delete

    def populate(self, active=False):
        data = server.app.config['DATA_DIR']
        project = 'project_alice_0000000001'
        vortex_id = 'f'*24
        now = time.time()
        with server.connect_db() as db:
            db.execute("""INSERT INTO projects
                          (id,key_hash,project_json,revision,created_at,updated_at,owner_uid,title)
                          VALUES(?,?,?,1,?,?,?,?)""",
                       (project, 'a'*64, '{}', now, now, self.uid, 'Sample project'))
            db.execute("""INSERT INTO venture_conversations
                          (id,uid,title,created_at,updated_at,native)
                          VALUES(?,?,?,?,?,0)""",
                       (project,self.uid,'Sample conversation',now,now))
            db.execute("""INSERT INTO vortex_jobs
                          (id,uid,request_id,input,kind,quality,engine,status,phase,created_at,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                       (vortex_id,self.uid,'first-request','https://example.test/item',
                        'download','balanced','yt-dlp','processing' if active else 'complete',
                        'Ready',now,now))
            db.execute("INSERT INTO venture_preferences(uid,settings_json,updated_at) VALUES(?,?,?)",
                       (self.uid,'{}',now))
        (data / 'projects' / project).mkdir(parents=True)
        (data / 'projects' / project / 'sample.txt').write_text('user data')
        (data / 'vortex' / vortex_id).mkdir(parents=True)
        (data / 'vortex' / vortex_id / 'media.mp4').write_bytes(b'user media')
        self.project,self.vortex_id=project,vortex_id

    def request_delete(self, email=None, uid=None):
        return self.client.post('/api/admin/accounts/delete', headers=self.admin_headers,
            json={'userId': public_id(uid or self.uid),
                  'confirmation':'DELETE '+(email or self.email)})

    def test_directory_includes_registered_but_unseen_firebase_user(self):
        self.admin_sdk()
        response = self.client.get('/api/admin/accounts', headers=self.admin_headers)
        self.assertEqual(response.status_code,200,response.json)
        users={r['email']:r for r in response.json['users']}
        self.assertEqual(users[self.email]['firstName'],'Alice')
        self.assertEqual(users[self.email]['lastName'],'Sample')
        self.assertEqual(users['unseen@example.test']['lastName'],'User')
        self.assertFalse(users['owner@example.test']['canDelete'])
        self.assertTrue(users[self.email]['canDelete'])
        self.assertNotIn('uid',users[self.email])

    def test_requires_setup_and_exact_confirmation(self):
        self.populate()
        missing = self.request_delete()
        self.assertEqual(missing.status_code,503)
        self.assertTrue((server.app.config['DATA_DIR']/'projects'/self.project).is_dir())
        with server.connect_db() as db:
            self.assertFalse(db.execute('SELECT 1 FROM account_deletions').fetchone())
        self.admin_sdk()
        bad = self.request_delete(email='wrong@example.test')
        self.assertEqual(bad.status_code,400)
        self.assertEqual(self.client.post('/api/admin/accounts/delete',
            headers=self.alice_headers, json={'userId':public_id(self.uid),
            'confirmation':'DELETE '+self.email}).status_code,403)
        self.assertEqual(self.request_delete(uid=self.admin).status_code,403)

    def test_complete_purge_blocks_stale_tokens(self):
        fake_delete = self.admin_sdk()
        self.populate()
        response = self.request_delete()
        self.assertEqual(response.status_code,200,response.json)
        self.assertTrue(response.json['deleted'])
        fake_delete.assert_called_once_with('alice',app=self.fake_app)
        with server.connect_db() as db:
            self.assertIsNone(db.execute('SELECT uid FROM audit_users WHERE uid=?',(self.uid,)).fetchone())
            self.assertIsNone(db.execute('SELECT id FROM projects WHERE owner_uid=?',(self.uid,)).fetchone())
            self.assertIsNone(db.execute('SELECT id FROM vortex_jobs WHERE uid=?',(self.uid,)).fetchone())
            self.assertIsNone(db.execute('SELECT uid FROM venture_preferences WHERE uid=?',(self.uid,)).fetchone())
            tombstone=db.execute('SELECT uid,email,state FROM account_deletions WHERE id=?',
                                 (public_id(self.uid),)).fetchone()
        self.assertIsNone(tombstone['uid'])
        self.assertIsNone(tombstone['email'])
        self.assertEqual(tombstone['state'],'complete')
        self.assertFalse((server.app.config['DATA_DIR']/'projects'/self.project).exists())
        self.assertFalse((server.app.config['DATA_DIR']/'vortex'/self.vortex_id).exists())
        response = self.client.get('/api/health',headers=self.alice_headers)
        self.assertEqual(response.status_code,403)
        self.assertEqual(response.json.get('code'),'account-removed')
        self.assertEqual(self.request_delete().status_code,200)  # idempotent

    def test_interrupted_disk_wipe_keeps_a_tombstone_for_retry(self):
        fake_delete = self.admin_sdk()
        self.populate()
        with patch.object(self.service,'erase_files',side_effect=OSError('locked')):
            response=self.request_delete()
        self.assertEqual(response.status_code,503)
        self.assertTrue(self.service.blocked(self.uid))
        self.assertTrue((server.app.config['DATA_DIR']/'projects'/self.project).is_dir())
        again=self.request_delete()
        self.assertEqual(again.status_code,200,again.json)
        self.assertEqual(fake_delete.call_count,2)
        self.assertFalse((server.app.config['DATA_DIR']/'projects'/self.project).exists())

    def test_live_download_rejected_before_any_account_changes(self):
        fake_delete=self.admin_sdk()
        self.populate(active=True)
        response=self.request_delete()
        self.assertEqual(response.status_code,409)
        fake_delete.assert_not_called()
        self.assertFalse(self.service.blocked(self.uid))
        self.assertTrue((server.app.config['DATA_DIR']/'vortex'/self.vortex_id).exists())


if __name__ == '__main__':
    unittest.main()
