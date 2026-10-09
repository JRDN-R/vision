"""Bounded parallel extraction inside ONE durable, account-owned Vortex job.

All compatible inspections start together. Downloads race in quality cohorts
(up to two at once); a failed download does not discard other candidates.
Only a verified final artifact wins. No engine output is sent to the UI.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import psutil

from vortex_adapters import ADAPTERS

PUBLIC_ERROR = 'Vortex could not retrieve this media. It may be unavailable or require sign-in.'
MAX_ENGINES = 5
MAX_DOWNLOADS = 2
MAX_MEMORY = 2 * 1024**3
MAX_EVENTS = 1024 * 1024
WORKER = Path(__file__).with_name('vortex_worker.py')
CATEGORIES = {'unavailable', 'timeout', 'resource_limit', 'invalid_media', 'identity_mismatch', 'cancelled', 'complete',
              'authentication', 'rate_limited', 'network', 'unsupported', 'duration_limit'}


def diagnostic(engine, category, started):
    from vortex_worker import emit
    emit(diagnostic=dict(engine=engine, category=category if category in CATEGORIES else 'unavailable',
                         elapsedMs=round((time.monotonic() - started) * 1000)))


def public_result(result):
    # Allowlist public metadata; strip internal evidence and extractor names.
    fields = {'title', 'url', 'source', 'thumbnail', 'mediaType', 'width', 'height', 'fps', 'vcodec',
              'acodec', 'abr', 'asr', 'audioChannels', 'duration', 'ext', 'quality', 'itemCount', 'note', 'sourceUrl', 'aspectRatio'}
    clean = dict(result)
    for key in ('media', 'results'):
        if key == 'media' and isinstance(clean.get(key), dict):
            clean[key] = {k: v for k, v in clean[key].items() if k in fields}
        elif key == 'results' and isinstance(clean.get(key), list):
            clean[key] = [{k: v for k, v in item.items() if k in fields} for item in clean[key] if isinstance(item, dict)]
    return {k: v for k, v in clean.items() if k in {'complete', 'media', 'results', 'filename', 'searchNextPage'}}


class Attempt:
    """A disposable process with isolated files and descendant cancellation."""
    def __init__(self, adapter, request, directory, kind):
        self.adapter, self.directory, self.kind = adapter, directory, kind
        self.started = time.monotonic()
        self.result = None
        self.category = None
        self.finished = False
        self.children = {}
        self.guard = None
        self.progress_event = None
        self.event_offset = 0
        self.partial = ''
        directory.mkdir(mode=0o700)
        path = directory / 'request.json'
        specification = dict(request, directory=str(directory), kind=kind)
        specification['services'] = {adapter.name: (request.get('services') or {}).get(adapter.name)} if getattr(adapter, 'service', False) else {}
        path.write_text(json.dumps(specification), encoding='utf-8')
        self.events = directory / 'events.jsonl'
        env = dict(os.environ, XDG_CACHE_HOME=str(directory / 'cache'), APPDATA=str(directory / 'cache'),
                   LOCALAPPDATA=str(directory / 'cache'), OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2')
        # Native attempts receive no service configuration or optional secrets
        # except the explicitly configured Spotify metadata credentials.
        if adapter.name != 'spotdl':
            for key in list(env):
                if key.startswith('VORTEX_SPOTIFY_'):
                    env.pop(key)
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        with self.events.open('wb') as output:
            self.process = subprocess.Popen([sys.executable, str(WORKER), '--request', str(path), '--adapter', adapter.name],
                                            cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                            stdout=output, stderr=subprocess.DEVNULL, **flags)
        try:
            from uploaded_media import WindowsJob
            self.guard = WindowsJob(self.process)
        except Exception:
            self.cancel()
            raise

    def poll(self):
        if self.finished:
            return True
        if self.events.stat().st_size > MAX_EVENTS:
            self.category = 'resource_limit'
            self.cancel()
        try:
            parent = psutil.Process(self.process.pid)
            for child in parent.children(recursive=True):
                self.children[child.pid] = child
            rss = sum(p.memory_info().rss for p in [parent, *self.children.values()] if p.is_running())
            if rss > MAX_MEMORY:
                self.category = 'resource_limit'
                self.cancel()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        if time.monotonic() - self.started > (120 if self.kind == 'inspect' else 40 * 60):
            self.category = 'timeout'
            self.cancel()
        with self.events.open('r', encoding='utf-8', errors='replace') as source:
            source.seek(self.event_offset)
            self.partial += source.read(MAX_EVENTS + 1)
            self.event_offset = source.tell()
        lines = self.partial.split('\n')
        self.partial = lines.pop()
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get('phase') in ('Downloading media', 'Converting to MP4', 'Converting to MOV', 'Converting to M4A', 'Converting to MP3', 'Converting to WAV'):
                self.progress_event = {'phase': event['phase'], 'progress': event.get('progress')}
            if event.get('category') in CATEGORIES:
                self.category = event['category']
        if self.process.poll() is None:
            return False
        self.finished = True
        if self.process.returncode == 0 and self.category not in ('timeout', 'resource_limit'):
            for line in self.events.read_text(encoding='utf-8', errors='replace').splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get('complete') is True:
                    self.result = event
        self.category = self.category or ('complete' if self.result else 'unavailable')
        diagnostic(self.adapter.name, self.category, self.started)
        return True

    def cancel(self):
        try:
            parent = psutil.Process(self.process.pid)
            self.children.update({p.pid: p for p in parent.children(recursive=True)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        for child in reversed(list(self.children.values())):
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        if self.guard:
            self.guard.close()
            self.guard = None
        psutil.wait_procs(list(self.children.values()), timeout=2)


def quality_rank(result, request):
    from vortex_worker import number
    if request.get('downloadMode') == 'audio' or (result.get('media') or {}).get('mediaType') in ('audio', 'image', 'gallery'):
        return 0
    height = number((result.get('media') or {}).get('height')) or 0
    cap = {'small': 480, 'balanced': 1080}.get(request.get('quality'))
    return min(height, cap) if cap else height


def race(request, *, adapters=None, attempt_factory=Attempt):
    from vortex_worker import DEFAULT_MAX_DURATION, DurationLimitError, WorkerError, emit
    directory = Path(request['directory']).resolve()
    selected = [a for a in (ADAPTERS if adapters is None else adapters)
                if a.compatible(request['input']) and a.configured(request)]
    if not selected:
        raise WorkerError(PUBLIC_ERROR)
    if len(selected) > MAX_ENGINES:
        raise WorkerError(PUBLIC_ERROR)
    race_dir = directory / 'race'
    race_dir.mkdir(mode=0o700)
    attempts, candidates = [], []
    started = time.monotonic()

    def start(adapter, kind, suffix, candidate=None):
        try:
            specification = dict(request)
            if candidate:
                specification['expectedDuration'] = (candidate.result.get('media') or {}).get('duration')
            attempt = attempt_factory(adapter, specification, race_dir / (adapter.name + suffix), kind)
            attempts.append(attempt)
            return attempt
        except Exception:
            diagnostic(adapter.name, 'unavailable', time.monotonic())
            return None

    def budget():
        # Same aggregate temporary-file allowance as the pre-race worker,
        # not a fresh allowance for each engine. Only one durable job runs.
        total = sum(p.stat().st_size for p in race_dir.rglob('*') if p.is_file() and not p.is_symlink())
        if total > 3 * int(request['maxBytes']) or time.monotonic() - started > (165 if request['kind'] == 'inspect' else 44 * 60):
            raise WorkerError(PUBLIC_ERROR)

    def promote(attempt):
        result = public_result(attempt.result)
        if request['kind'] == 'download':
            name = result.get('filename')
            if not name or Path(name).name != name:
                raise WorkerError(PUBLIC_ERROR)
            source = attempt.directory / name
            if source.is_symlink() or not source.is_file():
                raise WorkerError(PUBLIC_ERROR)
            final = directory / ('download' + source.suffix)
            os.replace(source, final)
            result['filename'] = final.name
        else:
            result.pop('filename', None)
        return result

    try:
        emit(phase='Finding media…', progress=None)
        pending = [a for adapter in selected if (a := start(adapter, 'inspect', '-inspect'))]
        while pending:
            budget()
            for attempt in list(pending):
                if attempt.poll():
                    pending.remove(attempt)
                    if attempt.result:
                        candidates.append(attempt)
                        # Max waits for available catalogs so a fast lower
                        # rendition cannot beat a known higher-quality source.
                        if request['kind'] == 'inspect' and request.get('quality') != 'max':
                            return promote(attempt)
                    else:
                        attempt.cancel()
                        shutil.rmtree(attempt.directory, ignore_errors=True)
            if pending:
                time.sleep(.05)
        candidates.sort(key=lambda a: quality_rank(a.result, request), reverse=True)
        if request['kind'] == 'inspect' and candidates:
            return promote(candidates[0])
        emit(phase='Preparing download…', progress=None)
        # Race equally suitable candidates first. Keep lower/unknown quality
        # alternatives until better candidates fail, rather than dropping them
        # after a successful metadata lookup.
        while candidates:
            rank = quality_rank(candidates[0].result, request)
            cohort = [a for a in candidates if quality_rank(a.result, request) == rank]
            candidates = [a for a in candidates if a not in cohort]
            pending = []
            while cohort or pending:
                budget()
                while cohort and len(pending) < MAX_DOWNLOADS:
                    candidate = cohort.pop(0)
                    if candidate.result.get('filename'):
                        return promote(candidate)
                    attempt = start(candidate.adapter, 'download', '-download', candidate)
                    if attempt:
                        attempt.expected_rank = rank
                        pending.append(attempt)
                for attempt in list(pending):
                    if attempt.poll():
                        pending.remove(attempt)
                        # A re-extraction must not silently downgrade a known
                        # resolution after inspection (one pixel for rounding).
                        if attempt.result and quality_rank(attempt.result, request) + 1 >= attempt.expected_rank:
                            return promote(attempt)
                        attempt.cancel()
                        shutil.rmtree(attempt.directory, ignore_errors=True)
                if pending:
                    # One live contender's actual stream/encode progress. The
                    # browser never sees which contender it belongs to.
                    progress = getattr(pending[0], 'progress_event', None)
                    if progress:
                        emit(**progress)
                        pending[0].progress_event = None
                    time.sleep(.05)
        if any(attempt.category == 'duration_limit' for attempt in attempts):
            # The request supplies the trusted server limit. Never forward an
            # adapter's error string or reject before alternatives have tried.
            raise DurationLimitError(request.get('maxDuration', DEFAULT_MAX_DURATION))
        raise WorkerError(PUBLIC_ERROR)
    finally:
        # Reap descendants before deleting files, including on Windows.
        for attempt in attempts:
            if not attempt.finished:
                diagnostic(attempt.adapter.name, 'cancelled', attempt.started)
            attempt.cancel()
        shutil.rmtree(race_dir, ignore_errors=True)
