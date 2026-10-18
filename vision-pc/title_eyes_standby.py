"""Title-eyes-only warm standby. The controller owns the shared model lock.

No model downloads or additional processes are launched alongside another local
AI worker. All other visual/answering runtimes keep their existing lifecycle.
"""
from datetime import datetime, timedelta
import json
from pathlib import Path
import time

from local_runtime import LocalFailure
from local_visual_runtime import VisualRuntime, manifest_path, regular
from owned_assets import exclusive_file

DAYS = (2, 6)  # Python weekdays: Wednesday and Sunday.


def scheduled(now):
    return now.weekday() in DAYS


def next_start(now):
    for offset in range(1, 8):
        candidate = (now + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        if scheduled(candidate):
            return candidate.isoformat()


def resource_pause():
    import psutil
    if psutil.virtual_memory().available < 10 * 1024**3:
        return 'Waiting for available memory'
    if any((p.info.get('name') or '').lower() in ('obs', 'obs.exe', 'obs64', 'obs64.exe', 'obs32.exe')
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
        if not scheduled(self.service.now()):
            return 'Off today · available Sundays and Wednesdays'
        settings = self.service.sessions.local.settings
        if not settings.get('enabled') or not settings.get('visualEnabled'):
            return 'Local visual worker is disabled'
        return None

    def competing_work(self):
        with self.service.db() as db:
            if db.execute("SELECT 1 FROM project_runs WHERE provider='local_llama_cpp' AND status IN ('queued','preparing','submitting','in_progress','saving') LIMIT 1").fetchone():
                return True
            if db.execute("SELECT 1 FROM frame_collections WHERE status IN ('queued','processing') AND retry_at<=? LIMIT 1", (time.time(),)).fetchone():
                return True
            return bool(db.execute("SELECT 1 FROM project_naming WHERE status IN ('queued','processing') LIMIT 1").fetchone())

    def cancelled(self):
        # Called during startup and from the runtime monitor. The model also
        # yields while idle so it never holds the global lock all day over a
        # pending Venture/video task. Active slides finish before yielding.
        return bool(self.off_reason() or resource_pause() or (not self.active and self.competing_work()))

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
        reason = self.off_reason() or resource_pause()
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
        runtime = self.runtime
        process = getattr(runtime, 'process', None)
        loaded = runtime is not None and (process is None or process.poll() is None)
        return dict(scheduled=scheduled(now), weekdays=['Sunday','Wednesday'],
                    clock='server-local', serverLocalTime=now.isoformat(), nextStartLocal=next_start(now),
                    loaded=loaded, phase=self.off_reason() or self.phase,
                    residentBytes=getattr(runtime,'resident_bytes',None) if loaded else 0,
                    memoryLimitBytes=4*1024**3, loadsSinceRestart=self.loads)
