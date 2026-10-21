"""Title-eyes-only warm standby. The controller owns the shared model lock.

No model downloads or additional processes are launched alongside another local
AI worker. Ultra standby shares this lease and yields to pending title jobs.
"""
from datetime import datetime, timedelta
import json
from pathlib import Path
import threading
import time
from zoneinfo import ZoneInfo

from local_runtime import LocalFailure
from local_visual_runtime import VisualRuntime, manifest_path, regular
from owned_assets import exclusive_file

DAYS = (2, 6)  # Python weekdays: Wednesday and Sunday.
# One-time exception in the church PC's Eastern local clock. These absolute
# dates expire automatically; they do not make every Monday/Tuesday warm.
TEMPORARY_START = datetime(2026, 10, 19)
TEMPORARY_END = datetime(2026, 10, 21)
TIMEZONE = 'America/New_York'
OBS_NAMES = frozenset(('obs', 'obs.exe', 'obs64', 'obs64.exe', 'obs32.exe'))


def church_now():
    return datetime.now(ZoneInfo(TIMEZONE))


def observe_obs():
    """Observe the co-located OBS process, independently of its network session.

    None means process enumeration is temporarily unavailable. Never infer that
    OBS closed merely because an inaccessible process could not be identified.
    """
    import psutil
    unknown = False
    try:
        for process in psutil.process_iter(['name'], ad_value=None):
            try:
                name = process.info.get('name')
                if name is None:
                    unknown = True
                elif name.lower() in OBS_NAMES:
                    return True
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                unknown = True
    except (OSError, psutil.Error):
        return None
    return None if unknown else False


class ObsActivity:
    """One inexpensive process scan per five seconds, shared by model owners."""
    def __init__(self, probe=observe_obs, clock=time.monotonic):
        self.probe, self.clock = probe, clock
        self.lock = threading.Lock()
        self.checked_at = None
        self.last_seen = None
        self.present = False

    def active(self):
        with self.lock:
            now = self.clock()
            if self.checked_at is not None and now - self.checked_at < 5:
                return self.present
            observed = self.probe()
            self.checked_at = now
            if observed is True:
                self.last_seen = now
                self.present = True
            elif observed is False:
                self.present = False
            else:
                # Retain through short permission/enumeration failures without
                # permanently claiming RAM after OBS has actually gone away.
                self.present = self.last_seen is not None and now - self.last_seen <= 120
            return self.present


_obs_activity = ObsActivity()


def obs_active():
    return _obs_activity.active()


def temporary_warmup(now):
    return TEMPORARY_START.date() <= now.date() < TEMPORARY_END.date()


def scheduled(now):
    return now.weekday() in DAYS or temporary_warmup(now)


def next_start(now):
    for offset in range(1, 8):
        candidate = (now + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        if scheduled(candidate):
            return candidate.isoformat()


def resource_pause(allow_obs=False):
    import psutil
    if psutil.virtual_memory().available < 10 * 1024**3:
        return 'Waiting for available memory'
    if not allow_obs and any((p.info.get('name') or '').lower() in OBS_NAMES
           for p in psutil.process_iter(['name'])):
        return 'Waiting for OBS to close'
    return None


class StandbyRuntime(VisualRuntime):
    """Same verified visual process, with separate idle and per-slide lifetimes."""
    def __init__(self, settings, cancelled, stage):
        self.request_cancelled = lambda: False
        self.deadline = time.monotonic() + 300  # Bounded initial startup.
        super().__init__(settings, lambda: cancelled() or self.request_cancelled(), stage)
        self.resident_bytes = 0
        self._fingerprint = None
        self._watched = []
        self._runtime_root = None
        self._inventory = set()

    def retain(self):
        # Capture only after verification and loading succeeded. Replacements,
        # edits, missing files, new runtime files, or changed manifests invalidate
        # the live instance before another image is accepted.
        manifest = json.loads(regular(self.main_settings['manifestPath']).read_text(encoding='utf-8-sig'))
        visual = json.loads(regular(manifest_path(self.main_settings)).read_text(encoding='utf-8-sig'))
        root = Path(manifest['runtimeRoot'])
        self._runtime_root = root
        self._inventory = set(manifest['runtimeFiles'])
        paths = [self.main_settings[k] for k in ('manifestPath','modelPath','runtimePath')]
        paths += [manifest_path(self.main_settings), visual['languageModel']['path'], visual['projector']['path']]
        paths += [root / name for name in self._inventory]
        self._watched = list(dict.fromkeys(str(p) for p in paths))
        self._fingerprint = self.fingerprint()
        self.finish_request()

    def fingerprint(self):
        values = []
        for path in self._watched:
            p = regular(path)
            stat = p.stat()
            values.append((str(p.resolve()), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        names = {str(p.relative_to(self._runtime_root)).replace('\\','/')
                 for p in self._runtime_root.rglob('*') if p.is_file()}
        if names != self._inventory:
            raise LocalFailure('Installed runtime files changed. A fresh verification is required.')
        return tuple(values)

    def reusable(self):
        try:
            return (self.process is not None and self.process.poll() is None and not self.stop_reason
                    and self._fingerprint is not None and self.fingerprint() == self._fingerprint)
        except (OSError, ValueError, KeyError):
            return False

    def begin_request(self, cancelled, stage):
        self.request_cancelled, self.stage = cancelled, stage
        self.deadline = time.monotonic() + 600

    def finish_request(self):
        self.request_cancelled = lambda: False
        self.stage = lambda _: None
        self.deadline = None

    def monitor(self):
        import psutil
        while not self.done.wait(1):
            try:
                process = psutil.Process(self.process.pid)
                self.resident_bytes = process.memory_info().rss
                self.peak_rss = max(self.peak_rss, self.resident_bytes)
                available = psutil.virtual_memory().available
                if self.resident_bytes > 4 * 1024**3:
                    self.stop_reason = 'The title model exceeded its 4 GiB memory limit.'
                if available < max(10, float(self.settings.get('reserveRamGiB', 10))) * 1024**3:
                    self.stop_reason = 'The title model stopped to preserve system memory.'
                if self.deadline is not None and time.monotonic() > self.deadline:
                    self.stop_reason = 'The title request reached its processing time limit.'
                if self.cancelled() or self.stop_reason:
                    self.stop_child(self.process)
                    return
            except psutil.NoSuchProcess:
                return
            except Exception:
                self.stop_reason = 'Title model resource monitoring became unavailable.'
                self.stop_child(self.process)
                return


class Standby:
    def __init__(self, service):
        self.service = service
        self.runtime = self.lease = None
        self.settings_key = None
        self.phase = 'Off'
        self.active = False
        self.retry_at = 0
        self.loads = 0

    def off_reason(self):
        if self.service.stop.is_set():
            return 'Server stopping'
        from local_standby import maintenance_active
        if not self.active and maintenance_active(self.service.app.config['DATA_DIR']):
            return 'Waiting for model maintenance'
        settings = self.service.sessions.local.settings
        if not settings.get('enabled') or not settings.get('visualEnabled'):
            return 'Local visual worker is disabled'
        # The schedule controls preloading, not requests. Any title job can
        # cold-start the model outside warm-standby days.
        if not self.warm_required() and not self.service.pending_titles():
            return 'Available on demand · loads when a slide arrives'
        return None

    def warm_required(self):
        return obs_active() or scheduled(self.service.now())

    def competing_work(self):
        # Live readiness owns the existing single-model slot. Do not unload on
        # every queue check because a video/local job cannot run during OBS.
        if obs_active():
            return False
        with self.service.db() as db:
            # Accepted live-slide work gets the next free lock. A deferred
            # video job (for example while OBS is open) must not starve it.
            if db.execute("SELECT 1 FROM title_eyes_jobs WHERE uid LIKE 'mediashare:%' AND status IN ('queued','processing') LIMIT 1").fetchone():
                return False
            if db.execute("SELECT 1 FROM project_runs WHERE provider='local_llama_cpp' AND status IN ('queued','preparing','submitting','in_progress','saving') LIMIT 1").fetchone():
                return True
            if db.execute("SELECT 1 FROM frame_collections WHERE status IN ('queued','processing') AND retry_at<=? LIMIT 1", (time.time(),)).fetchone():
                return True
            return bool(db.execute("SELECT 1 FROM project_naming WHERE status IN ('queued','processing') LIMIT 1").fetchone())

    def cancelled(self):
        # Called during startup and from the runtime monitor. The model also
        # yields while idle outside OBS so normal local work can acquire the
        # global lock. Active slides finish before yielding.
        return bool(self.off_reason() or resource_pause(allow_obs=True) or (not self.active and self.competing_work()))

    def close(self, phase='Off'):
        runtime, lease = self.runtime, self.lease
        self.runtime = self.lease = None
        self.settings_key = None
        self.phase = phase
        try:
            if runtime is not None:
                runtime.__exit__(None, None, None)
        finally:
            if lease is not None:
                lease.__exit__(None, None, None)

    def ensure(self):
        reason = self.off_reason() or resource_pause(allow_obs=True)
        if not reason and self.competing_work():
            reason = 'Waiting for other local AI work'
        if reason:
            self.close(reason)
            return None
        settings = self.service.sessions.local.settings
        key = json.dumps(settings, sort_keys=True)
        if self.runtime is not None:
            reusable = getattr(self.runtime, 'reusable', lambda: True)
            if key == self.settings_key and reusable():
                self.phase = 'Ready'
                return self.runtime
            self.close('Reloading changed model files')
        if time.monotonic() < self.retry_at:
            return None
        lease = exclusive_file(self.service.app.config['DATA_DIR'] / 'local-inference.lock')
        try:
            lease.__enter__()
        except BlockingIOError:
            self.phase = 'Waiting for other local AI work'
            return None
        self.lease = lease
        try:
            self.settings_key = key
            self.runtime = self.service.runtime_factory(settings, self.cancelled, self.set_phase)
            self.runtime.__enter__()
            if self.cancelled():
                raise InterruptedError()
            retain = getattr(self.runtime, 'retain', None)
            if retain:retain()
            self.loads += 1
            self.phase = 'Ready'
            return self.runtime
        except BaseException:
            self.close('Could not load the title model; retrying shortly')
            self.retry_at = time.monotonic() + 60
            raise

    def set_phase(self, text):
        self.phase = text

    def status(self):
        now = self.service.now()
        obs = obs_active()
        runtime = self.runtime
        process = getattr(runtime, 'process', None)
        # A spawned process may still be verifying/loading weights. Report
        # readiness only after __enter__/retain have successfully completed.
        loaded = (runtime is not None and self.phase == 'Ready'
                  and (process is None or process.poll() is None)
                  and not getattr(runtime, 'stop_reason', None))
        return dict(scheduled=scheduled(now), weekdays=['Sunday','Wednesday'],
                    clock=TIMEZONE, timezone=TIMEZONE, serverLocalTime=now.isoformat(), nextStartLocal=next_start(now),
                    obsActive=obs, obsDetection='local-process',
                    warmReason='OBS is open' if obs else ('Scheduled standby' if scheduled(now) else 'On demand'),
                    temporaryWarmup=temporary_warmup(now),
                    temporaryWarmupEndsLocal=TEMPORARY_END.isoformat() if temporary_warmup(now) else None,
                    loaded=loaded, phase=self.off_reason() or self.phase,
                    residentBytes=getattr(runtime,'resident_bytes',None) if loaded else 0,
                    memoryLimitBytes=4*1024**3, loadsSinceRestart=self.loads)
