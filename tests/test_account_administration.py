"""Account-removal regressions against the current shared administration API.

All Firebase calls are fake. The previous per-account API has been superseded
by the shared service; these fixtures retain its access, purge and retry checks.
"""
import importlib.util
import hashlib
import sqlite3
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vision-pc'))
_spec = importlib.util.spec_from_file_location('_current_account_tests', ROOT / 'vision-pc/test_account_administration.py')
_current = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_current)

class AccountAdminTests(_current.AccountAdministrationTests):
    def test_directory_counts_are_read_only(self):
        deleted = self.admin_sdk()
        self.populate()
        response = self.client.get('/api/admin/accounts', headers=self.admin_headers)
        self.assertEqual(response.status_code, 200, response.json)
        person = next(item for item in response.json['users'] if item['email'] == self.email)
        self.assertEqual((person['projects'], person['conversations'], person['vortex']), (1, 1, 1))
        self.assertFalse(self.service.blocked(self.uid))
        deleted.assert_not_called()

    def test_disabled_context_engine_still_purges_only_the_removed_owner(self):
        self.admin_sdk()
        self.populate()
        root = _current.server.app.config['DATA_DIR'] / 'context'
        root.mkdir(exist_ok=True)
        path = root / 'context.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE context_chunks(id INTEGER PRIMARY KEY,owner TEXT)')
            db.execute('CREATE VIRTUAL TABLE context_fts USING fts5(content)')
            for table in ('context_sources','context_cache','context_vectors','context_heads','context_generations'):
                db.execute('CREATE TABLE '+table+'(owner TEXT)')
                db.executemany('INSERT INTO '+table+' VALUES(?)', [(self.uid,), (self.admin,)])
            db.execute('CREATE TABLE context_jobs(owner TEXT,status TEXT)')
            db.executemany('INSERT INTO context_jobs VALUES(?,?)', [(self.uid,'complete'), (self.admin,'complete')])
            db.executemany('INSERT INTO context_chunks VALUES(?,?)', [(1,self.uid), (2,self.admin)])
            db.executemany('INSERT INTO context_fts(rowid,content) VALUES(?,?)', [(1,'private'), (2,'owner')])
        sources = root/'sources'/hashlib.sha256((self.uid+'\0'+self.project).encode()).hexdigest()
        sources.mkdir(parents=True)
        (sources/'original.txt').write_text('private')
        with patch.object(self.service.sessions.context, 'engine', None):
            response = self.request_delete()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(sources.exists())
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM context_heads WHERE owner=?',(self.uid,)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM context_heads WHERE owner=?',(self.admin,)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT content FROM context_fts').fetchone()[0], 'owner')

    def test_owner_required_for_directory_and_removal(self):
        deleted = self.admin_sdk()
        self.populate()
        response = self.client.get('/api/admin/accounts', headers=self.alice_headers)
        self.assertEqual(response.status_code, 403)
        response = self.client.post('/api/admin/accounts/delete', headers=self.alice_headers,
            json={'userId': _current.public_id(self.uid), 'confirmation': 'DELETE '+self.email})
        self.assertEqual(response.status_code, 403)
        deleted.assert_not_called()
        self.assertFalse(self.service.blocked(self.uid))

    def test_other_administrators_are_protected(self):
        deleted = self.admin_sdk()
        directory = _current.server.app.config['DATA_DIR'] / 'activity-admins.json'
        directory.write_text(_current.json.dumps({'uids':[self.admin, 'firebase:unseen']}), encoding='utf-8')
        response = self.request_delete(email='unseen@example.test', uid='firebase:unseen')
        self.assertEqual(response.status_code, 403)
        deleted.assert_not_called()
        self.assertFalse(self.service.blocked('firebase:unseen'))

    def test_remote_failure_preserves_data_and_replay_blocker_for_retry(self):
        deleted = self.admin_sdk()
        self.populate()
        deleted.side_effect = RuntimeError('offline fixture')
        response = self.request_delete()
        self.assertEqual(response.status_code, 503)
        self.assertTrue(self.service.blocked(self.uid))
        self.assertTrue((_current.server.app.config['DATA_DIR']/'projects'/self.project).exists())
        deleted.side_effect = None
        response = self.request_delete()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse((_current.server.app.config['DATA_DIR']/'projects'/self.project).exists())

    def test_both_credential_configuration_forms_are_supported(self):
        self.service.initialize({'firebaseAuth':{'adminServiceAccountPath':'legacy-private.json'}})
        self.assertEqual(self.service.credential_path, 'legacy-private.json')
        self.service.initialize({'firebaseAdminServiceAccount':'current-private.json',
                                 'firebaseAuth':{'adminServiceAccountPath':'legacy-private.json'}})
        self.assertEqual(self.service.credential_path, 'current-private.json')

if __name__ == '__main__':
    unittest.main()

