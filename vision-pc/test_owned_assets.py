"""Real filesystem/SQLite durability tests. No Flask, AI or network is required."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from owned_assets import OwnedAssets, exclusive_file, owner_key


class APIError(Exception):
    def __init__(self, message, status=400, code=None):
        self.message, self.status, self.code = message, status, code
        super().__init__(message)


class SimulatedCrash(BaseException):
    pass


def hold_lock(path, ready, release):
    with exclusive_file(Path(path)):
        ready.set()
        release.wait(15)


class OwnedAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / 'vision.sqlite3'
        self.uid, self.project = 'firebase:alice', 'retained_project_01'
        self.job, self.low = 'a' * 24, 'b' * 24
        self.content = b'verbatim source: RH 0.380; not 0.382\x00\xff'
        self.app = SimpleNamespace(config={'DATA_DIR': self.root})
        self.assets = OwnedAssets(self.app, self.db, APIError)
        self.assets.initialize({'durableAssets': {'enabled': True, 'diskReserveBytes': 0}})
        with self.db() as db:
            db.executescript('''
                CREATE TABLE projects(id TEXT PRIMARY KEY,owner_uid TEXT);
                CREATE TABLE account_deletions(id TEXT PRIMARY KEY);
                CREATE TABLE jobs(id TEXT PRIMARY KEY,uid TEXT,source_kind TEXT,import_options TEXT);
                CREATE TABLE vortex_jobs(id TEXT PRIMARY KEY,uid TEXT,purpose TEXT,status TEXT,delete_requested INTEGER);
            ''')
            db.execute('INSERT INTO projects VALUES(?,?)', (self.project, self.uid))
            db.execute('INSERT INTO jobs VALUES(?,?,?,?)', (self.job, self.uid, 'vortex',
                       json.dumps({'projectId': self.project, 'assetOwnership': 1})))
            db.execute("INSERT INTO vortex_jobs VALUES(?,?,'vision','complete',0)", (self.low, self.uid))
            self.assets.lease_vortex(db, self.low, self.job, self.uid)
        source_dir = self.root / 'vortex' / self.low
        source_dir.mkdir(parents=True)
        self.source = source_dir / 'export.mp4'
        self.source.write_bytes(self.content)
        self.value = dict(id=self.low, uid=self.uid, status='complete', purpose='vision', output_size=len(self.content),
                          input='https://example.com/public.mp4', quality='small',
                          media_json=json.dumps({'title': 'fixture', 'mediaType': 'video'}))
        self.vortex = SimpleNamespace(root=self.root / 'vortex', row=lambda _id, _uid: dict(self.value),
                                      output_path=lambda row: self.source, download_name=lambda row: 'fixture.mp4')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.database, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def tearDown(self):
        self.temp.cleanup()

    def adopt(self):
        return self.assets.adopt_vortex(self.vortex, self.uid, self.job, self.project, self.low)

    def leased(self):
        with self.db() as db:
            return self.assets.source_leased(db, self.low)

    def test_verified_immutable_copy_and_atomic_receipt(self):
        row = self.adopt()
        self.assertEqual(row['state'], 'ready')
        self.assertEqual(row['sha256'], hashlib.sha256(self.content).hexdigest())
        self.assertEqual(self.assets.ready_path(row).read_bytes(), self.content)
        self.assertFalse(self.leased())
        self.source.unlink()
        self.assertEqual(self.adopt()['id'], row['id'])
        self.assertEqual(self.assets.import_asset(self.uid, self.job, 'source')['id'], row['id'])
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM owned_assets').fetchone()[0], 1)
        snapshot = self.assets.snapshot(row)
        self.assertFalse(snapshot['provenance']['platformOriginal'])
        self.assertEqual(snapshot['provenance']['representation'], 'acquired-analysis-proxy')
        self.assertNotIn(str(self.root), json.dumps(snapshot))

    def test_every_copy_publish_and_receipt_boundary_recovers(self):
        for boundary in ('reserved', 'copied', 'verified', 'published', 'committed'):
            with self.subTest(boundary=boundary):
                self.tearDown()
                self.setUp()
                def crash(stage):
                    if stage == boundary:
                        raise SimulatedCrash()
                with patch.object(self.assets, '_boundary', side_effect=crash):
                    with self.assertRaises(SimulatedCrash):
                        self.adopt()
                self.assertTrue(self.source.is_file())
                self.assertEqual(self.leased(), boundary != 'committed')
                restarted = OwnedAssets(self.app, self.db, APIError)
                restarted.initialize({'durableAssets': {'enabled': False, 'diskReserveBytes': 0}})
                self.assets = restarted  # Accepted contracts survive disabling new acceptance.
                row = self.adopt()
                self.assertEqual(self.assets.ready_path(row).read_bytes(), self.content)
                self.assertFalse(self.leased())
                with self.db() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM owned_assets').fetchone()[0], 1)

    def test_no_write_transaction_is_held_during_copy_or_hash(self):
        observed = []
        original_digest = self.assets._digest
        def check_digest(*args):
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('CREATE TABLE IF NOT EXISTS unrelated_write(value TEXT)')
                db.execute("INSERT INTO unrelated_write VALUES('allowed')")
            observed.append(True)
            return original_digest(*args)
        with patch.object(self.assets, '_digest', side_effect=check_digest):
            self.adopt()
        self.assertGreaterEqual(len(observed), 2)

    def test_disk_full_keeps_source_and_lease_then_recovers(self):
        with patch('owned_assets.shutil.disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaises(APIError) as caught:
                self.adopt()
        self.assertEqual(caught.exception.status, 507)
        self.assertTrue(self.leased())
        self.assertEqual(self.source.read_bytes(), self.content)
        self.assertEqual(self.adopt()['state'], 'ready')

    def test_mid_copy_disk_full_is_not_acknowledged(self):
        calls = []
        def disk(_):
            calls.append(True)
            return SimpleNamespace(free=0 if len(calls) >= 3 else 100 * 1024**3)
        with patch('owned_assets.COPY_CHUNK', 8), patch('owned_assets.shutil.disk_usage', side_effect=disk):
            with self.assertRaises(APIError) as caught:
                self.adopt()
        self.assertEqual(caught.exception.status, 507)
        self.assertIsNone(self.assets.import_asset(self.uid, self.job, 'source'))
        self.assertTrue(self.leased())
        self.assertEqual(self.adopt()['state'], 'ready')

    def test_owner_and_total_reservations_fail_without_deleting(self):
        for attr in ('owner_limit', 'total_limit'):
            with self.subTest(limit=attr), patch.object(self.assets, attr, 1):
                with self.assertRaises(APIError) as caught:
                    self.adopt()
                self.assertEqual(caught.exception.status, 507)
                self.assertEqual(self.source.read_bytes(), self.content)
        self.assertTrue(self.leased())

    def test_changed_source_never_publishes(self):
        self.source.write_bytes(self.content + b'changed')
        with self.assertRaises(APIError) as caught:
            self.adopt()
        self.assertEqual(caught.exception.status, 409)
        self.assertTrue(self.leased())
        self.assertIsNone(self.assets.import_asset(self.uid, self.job, 'source'))

    def test_corrupt_staging_preserves_lease_and_refuses_false_success(self):
        with patch.object(self.assets, '_boundary', side_effect=lambda s: (_ for _ in ()).throw(SimulatedCrash()) if s == 'verified' else None):
            with self.assertRaises(SimulatedCrash):
                self.adopt()
        stage = next(self.assets.root.rglob('*.part'))
        stage.write_bytes(b'x' * len(self.content))
        with self.assertRaises(APIError) as caught:
            self.adopt()
        self.assertEqual(caught.exception.status, 409)
        self.assertTrue(self.leased())
        self.assertEqual(self.source.read_bytes(), self.content)

    def test_source_and_evidence_survive_source_cleanup_and_flag_disable(self):
        source = self.adopt()
        result = {'frames': [], 'transcription': {'text': 'not 0.382; RH 0.380'},
                  'sourceAsset': self.assets.snapshot(source)}
        saved = self.assets.retain_result(self.uid, self.job, self.project, result)
        self.source.unlink()
        self.assets.initialize({'durableAssets': {'enabled': False}})
        self.assertEqual(json.loads(self.assets.ready_path(saved).read_text()), result)
        self.assertEqual(self.assets.ready_path(source).read_bytes(), self.content)
        self.assertEqual(self.assets.retain_result(self.uid, self.job, self.project, result)['id'], saved['id'])
        changed = json.loads(json.dumps(result))
        changed['transcription']['text'] = 'not 0.380; RH 0.382'
        with self.assertRaises(APIError):
            self.assets.retain_result(self.uid, self.job, self.project, changed)

    def test_result_can_be_retained_in_recovery_after_project_removal(self):
        self.adopt()
        with self.db() as db:
            db.execute('DELETE FROM projects')
        result = self.assets.retain_result(self.uid, self.job, self.project, {'text': 'already acquired'})
        self.assertEqual(result['state'], 'ready')
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 0)

    def test_missing_project_cannot_begin_adoption(self):
        with self.db() as db:
            db.execute('DELETE FROM projects')
        with self.assertRaises(APIError) as caught:
            self.adopt()
        self.assertEqual(caught.exception.status, 404)
        self.assertTrue(self.source.is_file())

    def test_owner_isolation_and_untrusted_identifiers(self):
        row = self.adopt()
        for owner, asset_id in (('firebase:bob', row['id']), (self.uid, '../' + row['id']), (self.uid, ''),
                                (self.uid, 'c' * 24)):
            with self.subTest(owner=owner, asset_id=asset_id), self.assertRaises(APIError) as caught:
                self.assets.row(asset_id, owner)
            self.assertEqual(caught.exception.status, 404)
        self.assertIsNone(self.assets.import_asset('firebase:bob', self.job, 'source'))

    def test_tombstoned_account_cannot_publish_or_recover(self):
        def remove(stage):
            if stage == 'published':
                with self.db() as db:
                    db.execute('INSERT INTO account_deletions VALUES(?)', (owner_key(self.uid),))
        with patch.object(self.assets, '_boundary', side_effect=remove):
            with self.assertRaises(APIError) as caught:
                self.adopt()
        self.assertEqual(caught.exception.status, 403)
        with self.db() as db:
            self.assertIsNone(db.execute('SELECT source_asset_id FROM owned_import_receipts').fetchone()[0])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM owned_assets WHERE state='ready'").fetchone()[0], 0)
        with self.assertRaises(APIError):
            self.adopt()

    def test_lease_rejects_retired_deleted_or_foreign_source(self):
        for field, value in (('status', 'expired'), ('delete_requested', 1), ('uid', 'firebase:bob')):
            with self.subTest(field=field), self.db() as db:
                db.execute('UPDATE vortex_jobs SET ' + field + '=?', (value,))
                with self.assertRaises(APIError):
                    self.assets.lease_vortex(db, self.low, self.job, self.uid)
                db.execute("UPDATE vortex_jobs SET status='complete',delete_requested=0,uid=?", (self.uid,))

    def test_symlink_source_and_asset_directory_are_rejected(self):
        outside = self.root / 'outside.mp4'
        outside.write_bytes(self.content)
        self.source.unlink()
        try:
            self.source.symlink_to(outside)
        except OSError:
            self.skipTest('Creating symlinks requires additional Windows privileges')
        with self.assertRaises(ValueError):
            self.adopt()
        self.source.unlink()
        self.source.write_bytes(self.content)
        owner = self.assets.root / owner_key(self.uid)
        owner.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.adopt()
        self.assertEqual(outside.read_bytes(), self.content)

    def test_existing_acknowledged_bytes_are_never_overwritten(self):
        row = self.adopt()
        path = self.assets.ready_path(row)
        path.write_bytes(b'corrupt')
        with self.assertRaises(APIError) as caught:
            self.adopt()
        self.assertEqual(caught.exception.status, 410)
        self.assertEqual(path.read_bytes(), b'corrupt')

    def test_second_process_cannot_copy_under_existing_file_lock(self):
        row = self.assets._reserve_asset(self.uid, self.job, self.project, 'source', len(self.content),
                                         'fixture.mp4', 'video/mp4', {})
        ctx = multiprocessing.get_context('spawn')
        ready, release = ctx.Event(), ctx.Event()
        worker = ctx.Process(target=hold_lock, args=(str(self.assets._path(row, '.lock')), ready, release))
        worker.start()
        try:
            self.assertTrue(ready.wait(10), 'child process did not acquire its lock')
            with self.assertRaises(APIError) as caught:
                self.adopt()
            self.assertEqual(caught.exception.status, 503)
            self.assertTrue(self.leased())
        finally:
            release.set()
            worker.join(10)
            if worker.is_alive():
                worker.terminate()
                worker.join()
        self.assertEqual(worker.exitcode, 0)
        self.assertEqual(self.adopt()['state'], 'ready')

    @unittest.skipUnless(Path('/dev/shm').is_dir(), 'Separate-volume fixture requires /dev/shm')
    def test_actual_cross_volume_copy_renames_only_within_owned_storage(self):
        with tempfile.TemporaryDirectory(dir='/dev/shm') as temp:
            other = Path(temp)
            if other.stat().st_dev == self.root.stat().st_dev:
                self.skipTest('No distinct source filesystem available')
            self.source = other / 'source.mp4'
            self.source.write_bytes(self.content)
            self.vortex.root = other
            original = os.replace
            renames = []
            def rename(source, target):
                self.assertEqual(Path(source).parent, Path(target).parent)
                renames.append((source, target))
                return original(source, target)
            with patch('owned_assets.os.replace', side_effect=rename):
                row = self.adopt()
            self.assertEqual(len(renames), 1)
            self.assertEqual(self.assets.ready_path(row).read_bytes(), self.content)
            self.assertTrue(self.source.is_file())

    def test_bad_configuration_does_not_activate(self):
        for config in ({'enabled': 'true'}, {'diskReserveBytes': -1}, {'ownerLimitBytes': True}, {'totalLimitBytes': 0}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                self.assets.initialize({'durableAssets': config})


if __name__ == '__main__':
    unittest.main()
