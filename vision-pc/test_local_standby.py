"""Real OS leases plus deterministic model fixtures; no weights/network required."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from local_runtime import LlamaRuntime, LocalFailure
from local_standby import UltraStandby, StandbyRuntime, scheduled, maintenance_window, maintenance_active
from owned_assets import exclusive_file


class FakeRuntime:
    instances = []

    def __init__(self, settings, cancelled, stage):
        self.settings, self.cancelled = settings, cancelled
        self.alive = False
        self.valid = True
        self.resident_bytes = 20 * 1024**3
        self.process = SimpleNamespace(poll=lambda: None if self.alive else 0)
        self.requests = []
        self.closed = False
        self.instances.append(self)

    def __enter__(self):
        self.alive = True
        return self

    def __exit__(self, *args):
        self.alive = False
        self.closed = True

    def reusable(self):
        return self.alive and self.valid

    def begin_request(self, profile, cancelled, stage):
        self.settings = dict(profile)
        self.requests.append(dict(profile))
        self.request_cancelled = cancelled

    def finish_request(self):
        self.request_cancelled = lambda: False


class StandbyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dbfile = self.root / 'test.db'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE title_eyes_jobs(status TEXT);
                CREATE TABLE project_runs(provider TEXT, status TEXT, local_profile_json TEXT);
                CREATE TABLE frame_collections(status TEXT, retry_at REAL);
                CREATE TABLE project_naming(status TEXT);
            ''')
        self.local = SimpleNamespace(settings={'enabled': True}, db=self.db,
            app=SimpleNamespace(config={'DATA_DIR': self.root}),
            sessions=SimpleNamespace(stop=threading.Event()), runtime_factory=LlamaRuntime)
        self.pool = UltraStandby(self.local)
        self.pool.now = lambda: datetime(2026, 10, 19, 12)
        self.pool.runtime_factory = FakeRuntime
        FakeRuntime.instances = []
        self.profile = dict(modelVariant='ultra', modelId='qwen-ultra-local', contextTokens=32768,
                            ultraReceipt='verified', threads=8, gpuLayers=0,
                            thinking=True, sampling={'temperature': .6}, maxResidentGiB=30,
                            reserveRamGiB=10, maxRunSeconds=18000)
        p = patch('local_standby.local_ultra.select_profile', side_effect=lambda _: dict(self.profile))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.pool.close)

    @contextmanager
    def db(self):
        con = sqlite3.connect(self.dbfile)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def candidate(self, profile=None):
        return {'local_profile_json': json.dumps(profile or self.profile)}

    def title(self):
        with self.db() as db:
            db.execute("INSERT INTO title_eyes_jobs VALUES('queued')")

    def assert_unlocked(self):
        with exclusive_file(self.root / 'local-inference.lock'):
            pass

    def test_weekdays_midnight_and_status(self):
        monday = datetime(2026, 10, 19)
        self.assertEqual([scheduled(monday + timedelta(days=n)) for n in range(7)],
                         [True, True, False, True, True, True, False])
        self.pool.tick()
        self.assertEqual(self.pool.status()['residentBytes'], 20 * 1024**3)
        self.assertEqual(self.pool.status()['contextTokens'], 32768)
        self.pool.now = lambda: datetime(2026, 10, 21)
        self.pool.tick()
        self.assertFalse(self.pool.status()['loaded'])
        self.assert_unlocked()
        self.pool.now = lambda: datetime(2026, 10, 22)
        self.pool.tick()
        self.assertEqual(self.pool.loads, 2)

    def test_reuses_process_and_applies_each_requests_controls(self):
        self.pool.tick()
        live = self.pool.runtime
        for thinking, temperature in [(False, .2), (True, .8)]:
            profile = dict(self.profile, thinking=thinking, sampling={'temperature': temperature})
            with self.pool.work_lease(self.candidate(profile)):
                with self.pool.answering(profile, lambda: False, lambda _: None) as used:
                    self.assertIs(used, live)
                    self.assertEqual(used.settings['thinking'], thinking)
                    self.assertEqual(used.settings['sampling']['temperature'], temperature)
                    with self.assertRaises(BlockingIOError):
                        with exclusive_file(self.root / 'local-inference.lock'):
                            pass
        self.pool.tick()
        self.assertEqual(self.pool.loads, 1)
        self.assertEqual(len(live.requests), 2)

    def test_local_releases_ultra_and_standby_returns(self):
        self.pool.tick()
        original = self.pool.runtime
        with self.pool.work_lease(self.candidate({'modelVariant': 'standard'})):
            self.assertTrue(original.closed)
            self.assertIsNone(self.pool.runtime)
        self.assert_unlocked()
        self.pool.tick()
        self.assertEqual(self.pool.loads, 2)

    def test_title_priority_yields_idle_and_never_cancels_active(self):
        self.pool.tick()
        original = self.pool.runtime
        with self.pool.work_lease(self.candidate()):
            with self.pool.answering(self.profile, lambda: False, lambda _: None):
                self.title()
                self.assertFalse(self.pool.cancelled())
                self.assertFalse(original.closed)
        self.assertTrue(original.closed)
        self.assert_unlocked()
        with self.assertRaises(BlockingIOError):
            with self.pool.work_lease(self.candidate()):
                self.fail('A new Venture answer must wait for queued titles')
        self.pool.tick()
        self.assertIsNone(self.pool.runtime)
        with self.db() as db:
            db.execute('DELETE FROM title_eyes_jobs')
        self.pool.tick()
        self.assertEqual(self.pool.loads, 2)

    def test_temporary_title_standby_releases_idle_ultra_but_allows_answers(self):
        self.local.settings['visualEnabled'] = True
        self.pool.now = lambda: datetime(2026,10,16,12)
        self.pool.tick()
        original = self.pool.runtime
        self.pool.now = lambda: datetime(2026,10,19)
        self.pool.tick()
        self.assertTrue(original.closed)
        self.assertIsNone(self.pool.runtime)
        self.assertIn('temporary MediaShare', self.pool.phase)
        self.assert_unlocked()
        # The exception controls idle residency, not accepted Venture answers.
        with self.pool.work_lease(self.candidate()):
            with self.pool.answering(self.profile, lambda:False, lambda _:None) as answer:
                self.assertFalse(self.pool.cancelled())
                self.pool.now = lambda:datetime(2026,10,20)
                self.assertFalse(self.pool.cancelled())
        self.assertTrue(answer.closed)
        self.assert_unlocked()
        self.pool.tick();self.assertIsNone(self.pool.runtime)
        self.pool.now = lambda:datetime(2026,10,21)
        self.pool.tick();self.assertIsNone(self.pool.runtime)
        self.pool.now = lambda:datetime(2026,10,22)
        self.pool.tick();self.assertTrue(self.pool.status()['loaded'])
        self.assertEqual(self.pool.loads,3)

    def test_obs_priority_yields_idle_ultra_but_finishes_active_answer(self):
        self.local.settings['visualEnabled']=True
        self.pool.now=lambda:datetime(2026,10,26,12)
        with patch('title_eyes_standby.obs_active',return_value=False):
            self.pool.tick()
            original=self.pool.runtime
            with self.pool.work_lease(self.candidate()):
                with self.pool.answering(self.profile,lambda:False,lambda _:None):
                    with patch('title_eyes_standby.obs_active',return_value=True):
                        self.assertFalse(self.pool.cancelled())
                        self.assertFalse(original.closed)
        with patch('title_eyes_standby.obs_active',return_value=True):
            self.pool.tick();self.assertTrue(original.closed)
            self.assertIsNone(self.pool.runtime)
            self.assertIn('OBS',self.pool.phase)
            with self.assertRaises(BlockingIOError):
                with self.pool.work_lease(self.candidate()):
                    self.fail('New local work must wait for OBS readiness')
            self.assert_unlocked()
        with patch('title_eyes_standby.obs_active',return_value=False):
            self.pool.tick();self.assertIsNotNone(self.pool.runtime)

    def test_temporary_title_priority_requires_enabled_visual_worker(self):
        self.local.settings['visualEnabled'] = False
        self.pool.tick()
        self.assertTrue(self.pool.status()['loaded'])

    def test_off_day_on_demand_and_midnight_finish(self):
        self.pool.now = lambda: datetime(2026, 10, 21)
        self.pool.tick()
        self.assertEqual(self.pool.loads, 0)
        with self.pool.work_lease(self.candidate()):
            with self.pool.answering(self.profile, lambda: False, lambda _: None) as runtime:
                self.assertFalse(self.pool.cancelled())
        self.assertTrue(runtime.closed)
        self.assert_unlocked()
        self.pool.now = lambda: datetime(2026, 10, 20, 23, 59)
        self.pool.tick()
        with self.pool.work_lease(self.candidate()):
            with self.pool.answering(self.profile, lambda: False, lambda _: None) as runtime:
                self.pool.now = lambda: datetime(2026, 10, 21)
                self.assertFalse(self.pool.cancelled())
        self.assertTrue(runtime.closed)

    def test_changed_profile_dead_process_or_files_force_reload(self):
        self.pool.tick()
        first = self.pool.runtime
        first.valid = False
        with self.pool.work_lease(self.candidate()):
            with self.pool.answering(self.profile, lambda: False, lambda _: None) as second:
                self.assertIsNot(first, second)
        changed = dict(self.profile, contextTokens=8192)
        with self.pool.work_lease(self.candidate(changed)):
            with self.pool.answering(changed, lambda: False, lambda _: None) as third:
                self.assertIsNot(second, third)
                self.assertTrue(second.closed)
        third.alive = False
        self.pool.tick()
        self.assertEqual(self.pool.loads, 4)

    def test_visual_stage_drops_ultra_but_keeps_lease(self):
        self.pool.tick()
        with self.pool.work_lease(self.candidate()):
            old = self.pool.runtime
            self.pool.drop_runtime('Inspecting pixels')
            self.assertTrue(old.closed)
            with self.assertRaises(BlockingIOError):
                with exclusive_file(self.root / 'local-inference.lock'):
                    pass
            with self.pool.answering(self.profile, lambda: False, lambda _: None):
                self.assertEqual(self.pool.loads, 2)

    def test_failed_start_releases_lock_and_backs_off(self):
        class Failed(FakeRuntime):
            def __enter__(self):
                super().__enter__()
                raise LocalFailure('Insufficient memory')
        self.pool.runtime_factory = Failed
        self.pool.tick()
        self.assertTrue(FakeRuntime.instances[0].closed)
        self.assert_unlocked()
        self.pool.tick()
        self.assertEqual(len(FakeRuntime.instances), 1)
        self.pool.runtime_factory = FakeRuntime
        self.pool.retry_at = 0
        self.pool.tick()
        self.assertEqual(self.pool.loads, 1)

    def test_priority_arrives_during_preload(self):
        owner = self
        class Interrupted(FakeRuntime):
            def __enter__(self):
                super().__enter__()
                owner.title()
                return self
        self.pool.runtime_factory = Interrupted
        self.pool.tick()
        self.assertIsNone(self.pool.runtime)
        self.assert_unlocked()

    def test_request_error_shutdown_and_external_lock(self):
        with exclusive_file(self.root / 'local-inference.lock'):
            self.pool.tick()
            self.assertEqual(self.pool.loads, 0)
        self.pool.tick()
        with self.assertRaises(LocalFailure):
            with self.pool.work_lease(self.candidate()):
                with self.pool.answering(self.profile, lambda: False, lambda _: None):
                    raise LocalFailure('stream disconnected')
        self.assert_unlocked()
        self.pool.tick()
        self.local.sessions.stop.set()
        self.assertTrue(self.pool.cancelled())
        self.pool.tick()
        self.assert_unlocked()

    def test_maintenance_yields_idle_and_respects_active_lock(self):
        self.pool.tick()
        with exclusive_file(self.root / 'local-maintenance.lock'):
            self.assertTrue(maintenance_active(self.root))
            self.pool.tick()
            self.assert_unlocked()
        with maintenance_window(self.root, timeout=0):
            self.pool.tick()
            self.assertIsNone(self.pool.runtime)
        self.pool.tick()
        with self.pool.work_lease(self.candidate()):
            with self.assertRaises(BlockingIOError):
                with maintenance_window(self.root, timeout=0):
                    self.fail('Maintenance cannot steal an active lease')
            self.assertFalse(self.pool.cancelled())
        self.assertFalse(maintenance_active(self.root))

    def test_background_jobs_yield_and_disabled_standby_still_answers(self):
        self.pool.tick()
        with self.db() as db:
            db.execute("INSERT INTO frame_collections VALUES('queued',?)", (time.time() - 1,))
        self.pool.tick()
        self.assert_unlocked()
        with self.db() as db:
            db.execute('DELETE FROM frame_collections')
        self.local.settings['ultraStandby'] = False
        self.pool.tick()
        self.assertIsNone(self.pool.runtime)
        with self.pool.work_lease(self.candidate()):
            with self.pool.answering(self.profile, lambda: False, lambda _: None) as live:
                self.assertFalse(self.pool.cancelled())
        self.assertTrue(live.closed)


class PersistentRuntimeTests(unittest.TestCase):
    def test_idle_deadline_and_request_state_reset(self):
        live = StandbyRuntime({'modelVariant': 'ultra'}, lambda: False, lambda _: None)
        self.addCleanup(live.http.close)
        live.process = SimpleNamespace(pid=123, poll=lambda: None)
        profile = {'modelVariant': 'ultra', 'thinking': False, 'maxRunSeconds': 18000,
                   'sampling': {'temperature': .2}}
        with patch('psutil.Process', return_value=SimpleNamespace(cpu_times=lambda: SimpleNamespace(user=12, system=3))):
            live.begin_request(profile, lambda: True, lambda _: None)
        self.assertTrue(live.cancelled())
        self.assertFalse(live.thinking)
        self.assertEqual(live.cpu_origin, 15)
        self.assertFalse(live.processing_expired(time.monotonic()-1))
        live.deadline = time.monotonic()-1
        self.assertTrue(live.processing_expired(time.monotonic()+10))
        live.finish_request()
        self.assertFalse(live.cancelled())
        self.assertFalse(live.processing_expired(time.monotonic()-1))
        self.assertNotIn('sampling', live.settings)

    def test_fingerprint_detects_receipt_runtime_inventory_and_model_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, receipt, dll = (root/n for n in ('model', 'ready.json', 'runtime.dll'))
            for p in (model, receipt, dll):
                p.write_text('verified')
            live = StandbyRuntime({}, lambda: False, lambda _: None)
            self.addCleanup(live.http.close)
            live.runtime_root = root
            live.inventory = {'model', 'ready.json', 'runtime.dll'}
            live.watched = [model, receipt, dll]
            live.process = SimpleNamespace(poll=lambda: None)
            with patch('local_standby.local_ultra.check_ready'):
                for p in (model, receipt, dll):
                    live.verified_stamp = live.fingerprint()
                    self.assertTrue(live.reusable())
                    p.write_text(p.read_text() + 'changed')
                    self.assertFalse(live.reusable())
                live.verified_stamp = live.fingerprint()
                (root/'new.dll').write_text('unexpected')
                self.assertFalse(live.reusable())

    def test_idle_monitor_preserves_ram_guards(self):
        for rss, available in [(31*1024**3, 40*1024**3), (20*1024**3, 9*1024**3)]:
            live = StandbyRuntime({'modelVariant':'ultra', 'maxResidentGiB':30, 'reserveRamGiB':10}, lambda:False, lambda _:None)
            self.addCleanup(live.http.close)
            live.finish_request()
            live.process = SimpleNamespace(pid=123)
            fake = SimpleNamespace(memory_info=lambda:SimpleNamespace(rss=rss),
                                   cpu_times=lambda:SimpleNamespace(user=0,system=0))
            with patch('psutil.Process', return_value=fake), \
                    patch('psutil.virtual_memory', return_value=SimpleNamespace(available=available)), \
                    patch.object(live, 'stop_child') as stop, patch.object(live.done, 'wait', return_value=False):
                live.monitor()
            stop.assert_called_once()
            self.assertTrue(live.stop_reason)


if __name__ == '__main__':
    unittest.main()
