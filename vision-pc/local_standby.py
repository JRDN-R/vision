"""Retain one verified Ultra process under the existing cross-process AI lock.

The background controller and Venture borrow the same lease. Other workers
continue using that OS lock; queued titles take priority over idle Ultra.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
import threading
import time

import local_ultra
from local_runtime import LlamaRuntime, LocalFailure, local_run_seconds
from local_variants import checked_path
from owned_assets import exclusive_file

DAYS = (0, 1, 3, 4, 5)
REQUEST_FIELDS = {'thinking', 'sampling', 'longResponses', 'reasoningMode',
                  'maxOutputTokens', 'benchmark', 'ultraBenchmark'}


def scheduled(now):
    return now.weekday() in DAYS


def process_key(profile):
    return json.dumps({k: v for k, v in profile.items() if k not in REQUEST_FIELDS}, sort_keys=True)


def maintenance_active(directory):
    try:
        with exclusive_file(Path(directory) / 'local-maintenance.lock'):
            return False
    except BlockingIOError:
        return True


@contextmanager
def maintenance_window(directory, timeout=10):
    """Ask idle standbys to yield; never terminate an active inference job."""
    directory = Path(directory)
    with exclusive_file(directory / 'local-maintenance.lock'):
        end = time.monotonic() + timeout
        while True:
            lease = exclusive_file(directory / 'local-inference.lock')
            try:
                lease.__enter__()
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    raise
                time.sleep(.1)
        try:
            yield
        finally:
            lease.__exit__(None, None, None)


class StandbyRuntime(LlamaRuntime):
    """Persistent process; each request still has its own bounded deadline."""
    def __init__(self, settings, cancelled, stage):
        self.owner_cancelled = cancelled
        self.request_cancelled = lambda: False
        self.deadline = time.monotonic() + 600
        self.watched = []
        self.verified_stamp = None
        super().__init__(settings, self.is_cancelled, stage)

    def is_cancelled(self):
        return self.owner_cancelled() or self.request_cancelled()

    def processing_expired(self, deadline):
        return self.deadline is not None and time.monotonic() > self.deadline

    def verify(self):
        result = super().verify()
        manifest = json.loads(checked_path(self.settings['manifestPath']).read_text(encoding='utf-8-sig'))
        self.runtime_root = checked_path(manifest['runtimeRoot'])
        self.inventory = set(manifest['runtimeFiles'])
        self.watched = [Path(self.settings[k]) for k in ('modelPath', 'runtimePath', 'manifestPath')]
        self.watched += [Path(self.settings['manifestPath']).parent / 'ready.json']
        self.watched += [self.runtime_root / name for name in self.inventory]
        self.verified_stamp = self.fingerprint()
        return result

    def fingerprint(self):
        result = []
        for path in self.watched:
            info = checked_path(path).stat()
            result.append((str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        names = {str(p.relative_to(self.runtime_root)).replace('\\', '/')
                 for p in self.runtime_root.rglob('*') if p.is_file()}
        if names != self.inventory:
            raise LocalFailure('The installed runtime inventory changed. Reload Ultra before reuse.')
        return tuple(result)

    def reusable(self):
        try:
            local_ultra.check_ready(self.settings)
            return bool(self.process is not None and self.process.poll() is None and
                        not self.stop_reason and not self.done.is_set() and self.verified_stamp and
                        self.verified_stamp == self.fingerprint())
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def begin_request(self, profile, cancelled, stage):
        self.settings = dict(profile)
        self.thinking = profile.get('thinking', True)
        self.thinking_budget = None
        self.request_number = 0
        self.request_cancelled, self.stage = cancelled, stage
        self.deadline = time.monotonic() + local_run_seconds(profile)
        self.peak_rss = self.resident_bytes
        self.minimum_available_ram = None
        self.cpu_seconds = 0
        import psutil
        cpu = psutil.Process(self.process.pid).cpu_times()
        self.cpu_origin = cpu.user + cpu.system

    def finish_request(self):
        # No prompts, account callbacks, or per-request sampling survive as
        # Python-side state. llama.cpp requests always use cache_prompt=False.
        self.request_cancelled = lambda: False
        self.stage = lambda _: None
        self.deadline = None
        self.thinking_budget = None
        self.settings = {k: v for k, v in self.settings.items() if k not in REQUEST_FIELDS}


class UltraStandby:
    def __init__(self, local):
        self.local = local
        self.lock = threading.RLock()
        self.runtime = self.lease = None
        self.active = False
        self.now = datetime.now  # Same Windows-local clock as MediaShare standby.
        self.runtime_factory = StandbyRuntime
        self.phase = 'Off'
        self.loads = 0
        self.retry_at = 0

    @property
    def directory(self):
        return self.local.app.config['DATA_DIR']

    def titles_pending(self):
        with self.local.db() as db:
            return bool(db.execute("SELECT 1 FROM title_eyes_jobs WHERE status IN ('queued','processing') LIMIT 1").fetchone())

    def obs_title_priority(self):
        from title_eyes_standby import obs_active
        return bool(self.local.settings.get('enabled') and
                    self.local.settings.get('visualEnabled') and obs_active())

    def idle_reason(self):
        if self.local.sessions.stop.is_set():
            return 'Server stopping'
        if not self.local.settings.get('enabled') or self.local.settings.get('ultraStandby', True) is not True:
            return 'Ultra standby disabled'
        if not scheduled(self.now()):
            return 'Available on demand on Sunday and Wednesday'
        if maintenance_active(self.directory):
            return 'Waiting for model maintenance'
        if self.obs_title_priority():
            return 'Waiting for OBS to close · MediaShare model stays ready'
        # The date-limited title preload needs the same exclusive model slot.
        # Active answers still finish; only idle Ultra gives up its lease.
        from title_eyes_standby import temporary_warmup
        if self.local.settings.get('visualEnabled') and temporary_warmup(self.now()):
            return 'Yielding to temporary MediaShare warm standby'
        if self.titles_pending():
            return 'Yielding to MediaShare/title processing'
        with self.local.db() as db:
            first = db.execute("SELECT local_profile_json FROM project_runs WHERE provider='local_llama_cpp' "
                               "AND status IN ('queued','preparing','submitting','in_progress','saving') ORDER BY rowid LIMIT 1").fetchone()
            if first and json.loads(first['local_profile_json'] or '{}').get('modelVariant') != 'ultra':
                return 'Yielding to Qwen Local'
            if db.execute("SELECT 1 FROM frame_collections WHERE status IN ('queued','processing') AND retry_at<=? LIMIT 1", (time.time(),)).fetchone():
                return 'Yielding to video analysis'
            if db.execute("SELECT 1 FROM project_naming WHERE status IN ('queued','processing') LIMIT 1").fetchone():
                return 'Yielding to project naming'
        return None

    def cancelled(self):
        # Active jobs retain their normal stop/deadline/resource guards, but
        # midnight, queued slides, and maintenance never cancel their answers.
        try:
            return self.local.sessions.stop.is_set() or (not self.active and bool(self.idle_reason()))
        except Exception:
            return True  # Fail closed if queue/maintenance monitoring is lost.

    def drop_runtime(self, phase='Off'):
        runtime, self.runtime = self.runtime, None
        self.phase = phase
        if runtime is not None:
            runtime.__exit__(None, None, None)

    def close(self, phase='Off'):
        with self.lock:
            try:
                self.drop_runtime(phase)
            finally:
                lease, self.lease = self.lease, None
                if lease is not None:
                    lease.__exit__(None, None, None)

    def acquire(self):
        if self.lease is None:
            lease = exclusive_file(self.directory / 'local-inference.lock')
            lease.__enter__()
            self.lease = lease

    def set_phase(self, text):
        self.phase = text

    def tick(self):
        if not self.lock.acquire(blocking=False):
            return
        try:
            reason = self.idle_reason()
            if reason:
                self.close(reason)
                return
            if self.runtime is None and time.monotonic() < self.retry_at:
                return
            profile = local_ultra.select_profile(self.local.settings)
            if self.runtime is not None:
                if self.runtime.reusable() and process_key(profile) == process_key(self.runtime.settings):
                    return
                self.close('Reloading changed Ultra profile')
            if time.monotonic() < self.retry_at:
                return
            self.acquire()
            self.runtime = self.runtime_factory(dict(profile), self.cancelled, self.set_phase)
            self.runtime.__enter__()
            if self.cancelled():
                raise InterruptedError()
            self.runtime.finish_request()
            self.loads += 1
            self.phase = 'Ready'
        except BlockingIOError:
            self.phase = 'Waiting for other local AI work'
        except InterruptedError:
            self.close('Yielding to queued work')
        except Exception:
            self.close('Ultra unavailable or resource limited; retrying shortly')
            self.retry_at = time.monotonic() + 60
        finally:
            self.lock.release()

    @contextmanager
    def work_lease(self, candidate):
        if not self.lock.acquire(blocking=False):
            raise BlockingIOError('Ultra standby is yielding')
        try:
            if self.obs_title_priority():
                self.close('Waiting for OBS to close · MediaShare model stays ready')
                raise BlockingIOError('MediaShare readiness owns the model slot while OBS is open')
            if maintenance_active(self.directory) or self.titles_pending():
                self.close('Yielding to titles or maintenance')
                raise BlockingIOError('Priority work is waiting')
            self.acquire()
            self.active = True
            profile = json.loads(candidate.get('local_profile_json') or '{}')
            if profile.get('modelVariant') != 'ultra':
                self.drop_runtime('Qwen Local is active')
            yield
        finally:
            self.active = False
            try:
                reason = self.idle_reason()
                if self.runtime is None or reason or not self.runtime.reusable():
                    self.close(reason or 'Waiting to restore Ultra standby')
            finally:
                self.lock.release()

    @contextmanager
    def answering(self, profile, cancelled, stage):
        # Remote acceleration keeps its existing reconnect/fallback lifecycle.
        # Custom factories remain supported by the existing integration tests.
        if (profile.get('modelVariant') != 'ultra' or profile.get('overdriveRPC') or
                self.local.runtime_factory is not LlamaRuntime):
            self.drop_runtime('Loading the selected model')
            with self.local.runtime_factory(profile, cancelled, stage) as runtime:
                yield runtime
            return
        if self.runtime is not None and (not self.runtime.reusable() or
                process_key(profile) != process_key(self.runtime.settings)):
            self.drop_runtime('Loading the requested Ultra profile')
        try:
            if self.runtime is None:
                self.runtime = self.runtime_factory(dict(profile), self.cancelled, stage)
                self.runtime.request_cancelled = cancelled
                self.runtime.__enter__()
                self.loads += 1
            else:
                stage('Reusing ready Qwen Ultra')
            self.runtime.begin_request(profile, cancelled, stage)
            self.phase = 'Answering'
            yield self.runtime
            self.runtime.finish_request()
            self.phase = 'Ready'
        except BaseException:
            self.drop_runtime('Ultra request ended; preparing to reload')
            raise

    def status(self):
        # Do not block a status request behind a many-hour inference call.
        runtime = self.runtime
        process = getattr(runtime, 'process', None)
        loaded = runtime is not None and process is not None and process.poll() is None
        now = self.now()
        next_start = next((now + timedelta(days=n)).replace(hour=0, minute=0, second=0, microsecond=0)
                          for n in range(1, 8) if scheduled(now + timedelta(days=n)))
        return dict(scheduled=scheduled(now), weekdays=['Monday', 'Tuesday', 'Thursday', 'Friday', 'Saturday'],
                    clock='server-local', serverLocalTime=now.isoformat(), nextStartLocal=next_start.isoformat(),
                    loaded=loaded, active=self.active, phase=self.phase,
                    residentBytes=getattr(runtime, 'resident_bytes', 0) if loaded else 0,
                    memoryLimitBytes=30 * 1024**3, loadsSinceRestart=self.loads,
                    contextTokens=runtime.settings.get('contextTokens') if loaded else None)

    def start(self):
        def run():
            try:
                while not self.local.sessions.stop.is_set():
                    self.tick()
                    self.local.sessions.stop.wait(1)
            finally:
                self.close('Server stopping')
        threading.Thread(target=run, name='ultra-standby', daemon=True).start()
