"""Opt-in ownership on the real Flask/Vortex path; no public media or AI calls."""
import json
from pathlib import Path
import time
from unittest.mock import patch

import server
from activity_dashboard import public_id
import test_vortex_imports


class OwnedImportIntegrationTests(test_vortex_imports.ImportTests):
    def setUp(self):
        super().setUp()
        server.owned_assets.initialize({'durableAssets': {'enabled': True, 'diskReserveBytes': 0}})

    def finished_import(self):
        job = self.submit().json['id']
        self.advance(job)
        low = self.finish_low()
        prepared = dict(title='Fixture', duration=2, mediaType='video', snapshotInterval=1,
                        frames=[dict(name='frame.jpg', timestamp=0, mime='image/jpeg',
                                     data='data:image/jpeg;base64,ZmFrZSBmaXh0dXJl')], hasAudio=False)
        with patch('vortex_imports.prepare_source', return_value=(prepared, [])):
            self.advance(job)
        self.advance(job)
        self.assertEqual(self.advance(job)['status'], 'complete')
        return job, low

    def test_retained_source_and_result_survive_expiry_and_browser_receipt_delete(self):
        job, low = self.finished_import()
        result = self.client.get('/api/jobs/' + job + '/result', headers=self.alice).json
        asset = result['sourceAsset']['id']
        evidence = server.owned_assets.import_asset('firebase:alice', job, 'result')
        self.assertEqual(result['sourceAsset']['provenance']['representation'], 'acquired-analysis-proxy')
        self.assertFalse((server.vortex.root / low['id']).exists())
        self.assertEqual(self.client.delete('/api/jobs/' + job, headers=self.alice).status_code, 200)
        with server.connect_db() as db:
            db.execute('UPDATE jobs SET expires_at=? WHERE id=?', (time.time() - 7 * 86400, job))
            db.execute('UPDATE vortex_jobs SET expires_at=?', (time.time() - 7 * 86400,))
        server.vortex.sweep()
        server.prune_expired()
        server.recover_jobs()
        self.assertEqual(self.client.get('/api/jobs/' + job, headers=self.alice).status_code, 200)
        self.assertEqual(self.client.get('/api/jobs/' + job + '/result', headers=self.alice).json, result)
        self.assertEqual(self.client.get('/api/owned-assets/' + asset + '/file', headers=self.alice).data,
                         b'verified media fixture')
        self.assertTrue(server.owned_assets.ready_path(evidence).is_file())

    def test_source_lease_blocks_expiry_manual_deletion_and_restart_cleanup(self):
        job = self.submit().json['id']
        self.advance(job)
        low = self.finish_low()
        with server.connect_db() as db:
            db.execute('UPDATE vortex_jobs SET expires_at=? WHERE id=?', (time.time() - 7 * 86400, low['id']))
        server.vortex.sweep()
        self.assertEqual(server.vortex.row(low['id'])['status'], 'complete')
        self.assertEqual(self.client.delete('/api/vortex/jobs/' + low['id'], headers=self.alice).status_code, 409)
        server.vortex.recover()
        self.assertTrue(server.vortex.output_path(server.vortex.row(low['id'])).is_file())
        prepared = dict(title='Fixture', duration=2, mediaType='video', frames=[], hasAudio=False)
        with patch('vortex_imports.prepare_source', return_value=(prepared, [])):
            self.advance(job)
        self.assertIsNotNone(server.owned_assets.import_asset('firebase:alice', job, 'source'))
        self.assertFalse((server.vortex.root / low['id']).exists())

    def test_range_access_inventory_and_receipts_are_owner_scoped(self):
        job, _ = self.finished_import()
        source = server.owned_assets.import_asset('firebase:alice', job, 'source')
        route = '/api/owned-assets/' + source['id']
        self.assertEqual(self.client.get(route, headers=self.bob).status_code, 404)
        self.assertEqual(self.client.get(route + '/file', headers=self.bob).status_code, 404)
        self.assertNotEqual(self.client.get(route, headers=self.legacy).status_code, 200)
        ranged = self.client.get(route + '/file', headers={**self.alice, 'Range': 'bytes=0-7'})
        self.assertEqual(ranged.status_code, 206)
        self.assertEqual(ranged.data, b'verified')
        self.assertEqual(ranged.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(ranged.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(self.client.get('/api/owned-assets/imports', headers=self.bob).json['imports'], [])
        inventory = self.client.get('/api/owned-assets/imports', headers=self.alice).json['imports']
        self.assertEqual([r['importId'] for r in inventory], [job])
        self.assertNotIn(str(server.app.config['DATA_DIR']), json.dumps(inventory))

    def test_retention_contract_is_pinned_across_flag_changes_and_retries(self):
        accepted = self.submit().json['id']
        server.owned_assets.enabled = False
        self.assertEqual(self.submit().json['id'], accepted)
        with server.connect_db() as db:
            row = db.execute('SELECT import_options FROM jobs WHERE id=?', (accepted,)).fetchone()
        self.assertEqual(json.loads(row[0])['assetOwnership'], 1)
        other = self.submit(clientRequestId='legacy-request').json['id']
        with server.connect_db() as db:
            row = db.execute('SELECT import_options FROM jobs WHERE id=?', (other,)).fetchone()
        self.assertNotIn('assetOwnership', json.loads(row[0]))
        server.owned_assets.enabled = True
        self.assertEqual(self.submit(clientRequestId='legacy-request').json['id'], other)

    def test_adopted_source_is_not_redownloaded_after_preparation_failure(self):
        job = self.submit().json['id']
        self.advance(job)
        self.finish_low()
        with patch('vortex_imports.prepare_source', side_effect=ValueError('fixture preparation failure')):
            self.assertEqual(self.advance(job)['status'], 'error')
        source = server.owned_assets.import_asset('firebase:alice', job, 'source')
        self.assertIsNotNone(source)
        with server.connect_db() as db:
            db.execute("UPDATE jobs SET status='queued' WHERE id=?", (job,))
        prepared = dict(title='Fixture', duration=2, mediaType='video', frames=[], hasAudio=False)
        with patch.object(server.vortex, 'enqueue', side_effect=AssertionError('must reuse owned source')):
            with patch('vortex_imports.prepare_source', return_value=(prepared, [])):
                self.assertEqual(self.advance(job)['status'], 'queued')
        self.assertEqual(server.owned_assets.import_asset('firebase:alice', job, 'source')['id'], source['id'])

    def test_completed_duplicate_delivery_cache_expires_but_owned_result_does_not(self):
        job, _ = self.finished_import()
        row = server.get_job(job)
        delivery = Path(row['result_path'])
        self.assertTrue(delivery.is_file())
        with server.connect_db() as db:
            db.execute('UPDATE jobs SET expires_at=? WHERE id=?', (time.time() - 86401, job))
        server.prune_expired()
        self.assertFalse(delivery.exists())
        self.assertEqual(self.client.get('/api/jobs/' + job + '/result', headers=self.alice).status_code, 200)

    def test_deleted_project_does_not_delete_or_resurrect_owned_recovery_assets(self):
        job, _ = self.finished_import()
        source = server.owned_assets.import_asset('firebase:alice', job, 'source')
        with server.connect_db() as db:
            db.execute('DELETE FROM projects WHERE id=?', (self.project,))
        server.prune_expired()
        self.assertTrue(server.owned_assets.ready_path(source).is_file())
        with server.connect_db() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM projects WHERE id=?', (self.project,)).fetchone())
        self.assertEqual(len(self.client.get('/api/owned-assets/imports', headers=self.alice).json['imports']), 1)

    def test_explicit_account_purge_includes_assets_receipts_and_leases(self):
        job, _ = self.finished_import()
        source = server.owned_assets.import_asset('firebase:alice', job, 'source')
        path = server.owned_assets.ready_path(source)
        bob = server.owned_assets.root / public_id('firebase:bob')
        bob.mkdir()
        (bob / 'sentinel').write_bytes(b'other owner')
        with server.connect_db() as db:
            db.execute("INSERT INTO account_deletions(id,uid,email,state,requested_at) VALUES(?,?,?,'pending',?)",
                       (public_id('firebase:alice'), 'firebase:alice', 'fixture@example.com', time.time()))
        self.assertEqual(self.client.get('/api/owned-assets/' + source['id'], headers=self.alice).status_code, 403)
        owned = server.account_admin.owned('firebase:alice')
        server.account_admin.erase_files('firebase:alice', *owned)
        server.account_admin.erase_database('firebase:alice', owned[0])
        self.assertFalse(path.exists())
        self.assertEqual((bob / 'sentinel').read_bytes(), b'other owner')
        with server.connect_db() as db:
            for table in ('owned_assets', 'owned_import_receipts', 'owned_source_leases'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table + ' WHERE uid=?', ('firebase:alice',)).fetchone()[0], 0)

    def test_retained_inventory_is_paginated(self):
        with server.connect_db() as db:
            for i in range(56):
                db.execute('''INSERT INTO owned_import_receipts(job_id,uid,project_id,created_at,updated_at)
                              VALUES(?,?,?,?,?)''', (f'{i:024x}', 'firebase:alice', self.project, time.time(), time.time()))
        first = self.client.get('/api/owned-assets/imports', headers=self.alice).json
        self.assertEqual(len(first['imports']), 50)
        self.assertTrue(first['nextCursor'])
        second = self.client.get('/api/owned-assets/imports?after=' + first['nextCursor'], headers=self.alice).json
        self.assertEqual(len(second['imports']), 6)
        self.assertIsNone(second['nextCursor'])
        self.assertEqual(self.client.get('/api/owned-assets/imports?after=../../', headers=self.alice).status_code, 400)
