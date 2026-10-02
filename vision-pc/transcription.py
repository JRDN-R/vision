"""Optional, offline CPU transcription with durable project-scoped receipts.

The HTTP process never imports Whisper. A single low-priority subprocess loads
the locally installed model, checkpoints every audio chunk and dies with its
parent. Closing a browser has no effect on an accepted job.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.machinery
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback

BODY_LIMIT = 100 * 1024 * 1024
MAX_DURATION = 8 * 3600
MAX_SECTIONS = 256
MAX_QUEUED = 10
DISK_RESERVE = 1024 * 1024 * 1024
RETAIN_SECONDS = 35 * 86400
CHUNK_SECONDS = 900
RESULT_LIMIT = 4 * 1024 * 1024
MIME_FORMATS = {'audio/mpeg': 'mp3', 'audio/mp3': 'mp3', 'audio/wav': 'wav', 'audio/x-wav': 'wav', 'audio/wave': 'wav'}
TERMINAL = ('complete', 'error', 'cancelled')
DIAGNOSTIC_LIMIT = 64 * 1024
DEPENDENCY_TIMEOUT = 90
STAGES = {
    'startup': 'starting the worker', 'dependencies': 'loading the local libraries',
    'model': 'loading the Whisper model', 'probe': 'reading the audio',
    'decode': 'decoding the audio', 'transcribe': 'transcribing the audio',
    'save': 'saving the transcript',
}
ERROR_HELP = {
    'dependency': 'A server transcription library could not load. Check FUPCJ Server diagnostic log for the missing library or Windows runtime.',
    'dependency-timeout': 'Loading the server transcription libraries stalled. Update FUPCJ Server processor, then run CheckLocalTranscription.',
    'model': 'The Whisper model could not load. Run the server transcription check on FUPCJ Server.',
    'audio': 'FUPCJ Server could not read this audio section. Retry the recording; if it repeats, prepare the audio again.',
    'duration': 'An audio section has an invalid duration. Prepare the recording again before retrying.',
    'disk': 'Free at least 1 GB on FUPCJ Server, then retry server transcription.',
    'memory': 'FUPCJ Server ran out of memory. Close other heavy workloads on the server, then retry.',
    'permission': 'Windows blocked access to a server transcription file. Check FUPCJ Server diagnostic log and retry.',
    'timeout': 'Server transcription exceeded its time limit. Retry with a shorter recording.',
    'speech-filter': 'The local speech filter could not load. Check FUPCJ Server diagnostic log for its ONNX runtime error.',
    'worker': 'The server transcription worker stopped. Run the server transcription check on FUPCJ Server.',
}


class LocalRunnerError(RuntimeError):
    """Only fixed, non-sensitive diagnostics can cross the HTTP boundary."""
    def __init__(self, code='worker', stage='startup', exception=None, exit_code=None):
        self.code = code if code in ERROR_HELP else 'worker'
        self.stage = stage if stage in STAGES else 'startup'
        details = [self.code, self.stage]
        if isinstance(exception, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', exception):
            details.append(exception)
        if isinstance(exit_code, int) and exit_code:
            details.append('exit 0x%08X' % (exit_code & 0xffffffff))
        super().__init__(ERROR_HELP[self.code] + ' [' + ' / '.join(details) + ']')


def failure_code(error, stage):
    if isinstance(error, MemoryError):
        return 'memory'
    if isinstance(error, PermissionError):
        return 'permission'
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return 'timeout'
    if str(error) == 'low-disk-space':
        return 'disk'
    if str(error) in ('invalid-audio-duration', 'audio-too-long'):
        return 'duration'
    if stage == 'dependencies':
        return 'dependency'
    if stage == 'model':
        return 'model'
    if stage in ('probe', 'decode'):
        return 'audio'
    if stage == 'transcribe' and any(word in str(error).lower() for word in ('onnx', 'vad filter', 'silero')):
        return 'speech-filter'
    return 'worker'


def atomic_json(path, value):
    temporary = Path(path).with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False, separators=(',', ':'))
        out.flush()
        os.fsync(out.fileno())
    # Windows readers can temporarily deny delete-sharing while polling progress.
    # Do not abort a long transcription for that brief sharing violation.
    for attempt in range(6):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.02 * (attempt + 1))


def timestamp(value):
    ms = max(0, round(value * 1000))
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}'


class LocalTranscription:
    def __init__(self, app, connect_db, api_error, sessions):
        self.app, self.db, self.Error, self.sessions = app, connect_db, api_error, sessions
        self.stop, self.wake = threading.Event(), threading.Event()
        self._worker_lock = threading.Lock()
        self.register_routes()

    @property
    def root(self):
        return self.app.config['DATA_DIR'] / 'transcriptions'

    def initialize(self, config, config_path):
        supplied = config.get('localTranscription')
        supplied = supplied if isinstance(supplied, dict) else {}
        self.settings = {'enabled': supplied.get('enabled') is True}
        for key in ('modelPath', 'packagesPath'):
            path = supplied.get(key)
            if isinstance(path, str) and path:
                path = Path(path)
                self.settings[key] = str((path if path.is_absolute() else Path(config_path).parent / path).resolve())
        try:
            self.settings['cpuThreads'] = max(1, min(4, int(supplied.get('cpuThreads', 4))))
        except (ValueError, TypeError):
            self.settings['cpuThreads'] = 4
        self.root.mkdir(exist_ok=True, mode=0o700)
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS local_transcriptions (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, client_id TEXT NOT NULL,
                content_hash TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0, source_name TEXT NOT NULL,
                duration REAL NOT NULL, error TEXT, result_json TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL NOT NULL,
                UNIQUE(project_id,client_id))''')

    def capability(self):
        model = Path(self.settings.get('modelPath') or '__not_installed__')
        packages = self.settings.get('packagesPath')
        try:
            package = (importlib.machinery.PathFinder.find_spec('faster_whisper', [packages])
                       if packages else importlib.util.find_spec('faster_whisper'))
        except (ValueError, ImportError, OSError):
            package = None
        model_ok = model.is_dir() and all((model / name).is_file() for name in ('config.json', 'model.bin', 'tokenizer.json')) and any((model / name).is_file() for name in ('vocabulary.json', 'vocabulary.txt'))
        installed = bool(model_ok and package)
        ffmpeg = Path(self.app.config['FFMPEG'])
        tools_ok = ffmpeg.is_file() and ffmpeg.with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe').is_file()
        ready = self.settings['enabled'] and installed and tools_ok
        status = 'ready' if ready else 'disabled' if not self.settings['enabled'] else 'not-installed' if not installed else 'missing-media-tools'
        return dict(installed=installed, ready=ready, available=ready, status=status, engine='faster-whisper',
                    model=model.name if model_ok else None, cpuThreads=self.settings['cpuThreads'], maxRequestBytes=BODY_LIMIT)

    def row(self, job_id, project_id=None):
        if not re.fullmatch(r'[0-9a-f]{24}', job_id):
            raise self.Error('This transcription is not part of the project.', 404)
        with self.db() as db:
            row = db.execute('SELECT * FROM local_transcriptions WHERE id=?', (job_id,)).fetchone()
        if row is None or (project_id is not None and row['project_id'] != project_id):
            raise self.Error('This transcription is not part of the project.', 404)
        return dict(row)

    def snapshot(self, value):
        result = {k: value[k] for k in ('id', 'status', 'phase', 'progress')}
        if value['error']:
            result['error'] = value['error']
        if value['status'] == 'complete' and value['result_json']:
            result['result'] = json.loads(value['result_json'])
        return result

    def validate(self, body):
        if not isinstance(body, dict):
            raise self.Error('Send audio sections for transcription.')
        client = body.get('clientRequestId')
        if not isinstance(client, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', client):
            raise self.Error('A valid transcription request identifier is required.')
        source = body.get('sourceName', 'Audio')
        if not isinstance(source, str) or len(source) > 500:
            raise self.Error('Choose a valid audio name.')
        source = re.sub(r'[\x00-\x1f\x7f]', '', source.replace('\\', '/').rsplit('/', 1)[-1])[:180] or 'Audio'
        sections = body.get('sections')
        if not isinstance(sections, list) or not 1 <= len(sections) <= MAX_SECTIONS:
            raise self.Error('Send from 1 to 256 audio sections.')
        validated, previous_end, duration, encoded_size = [], 0, 0, 0
        digest = hashlib.sha256()
        digest.update(source.encode('utf-8'))
        for section in sections:
            if not isinstance(section, dict):
                raise self.Error('Invalid audio section.')
            start, end = section.get('start'), section.get('end')
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, end)):
                raise self.Error('Audio sections need finite start and end timestamps.')
            if start < previous_end or end <= start or end > MAX_DURATION:
                raise self.Error('Audio sections must be ordered, non-overlapping, and within eight hours.')
            duration += end - start
            previous_end = end
            mime, audio = section.get('mimeType'), section.get('audioData')
            if mime not in MIME_FORMATS or not isinstance(audio, str):
                raise self.Error('Server transcription accepts MP3 or WAV audio sections.')
            match = re.fullmatch(r'data:([^;,]+);base64,([A-Za-z0-9+/]*={0,2})', audio)
            if not match or match[1] not in MIME_FORMATS or MIME_FORMATS[match[1]] != MIME_FORMATS[mime]:
                raise self.Error('Invalid audio data. Send base64 MP3 or WAV audio.')
            encoded_size += len(audio)
            if encoded_size > BODY_LIMIT:
                raise self.Error('Server transcription audio uploads exceed 100 MB.', 413)
            try:
                raw = base64.b64decode(match[2], validate=True)
            except ValueError:
                raise self.Error('Invalid base64 audio.')
            if not raw:
                raise self.Error('An audio section is empty.')
            details = dict(start=float(start), end=float(end), format=MIME_FORMATS[mime])
            digest.update(json.dumps(details, sort_keys=True).encode())
            digest.update(hashlib.sha256(raw).digest())
            validated.append({**details, 'data': raw})
        return client, source, validated, duration, digest.hexdigest()

    def register_routes(self):
        from flask import jsonify, request

        @self.app.post('/api/projects/<project_id>/transcriptions')
        def local_transcription_create(project_id):
            self.sessions.project(project_id)
            request.max_content_length = BODY_LIMIT
            client, source, sections, duration, content_hash = self.validate(request.get_json(silent=True))
            directory = None
            try:
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    previous = db.execute('SELECT * FROM local_transcriptions WHERE project_id=? AND client_id=?', (project_id, client)).fetchone()
                    if previous:
                        if previous['content_hash'] != content_hash:
                            raise self.Error('This request identifier belongs to different audio. Start a new transcription.', 409)
                        return jsonify(id=previous['id'], status=previous['status']), 202
                    if not self.capability()['ready']:
                        raise self.Error('Server transcription is not ready. Enable the server transcription plugin on FUPCJ Server first.', 503)
                    pending = db.execute("SELECT COUNT(*) FROM local_transcriptions WHERE status IN ('queued','processing')").fetchone()[0]
                    if pending >= MAX_QUEUED:
                        raise self.Error('The server transcription queue is full. Wait for a job to finish.', 429)
                    if shutil.disk_usage(self.root).free < DISK_RESERVE + sum(len(s['data']) for s in sections):
                        raise self.Error('Free at least 1 GB on FUPCJ Server before uploading more audio.', 507)
                    job_id, now = secrets.token_hex(12), time.time()
                    directory = self.root / job_id
                    directory.mkdir(mode=0o700)
                    manifest = []
                    for index, section in enumerate(sections):
                        path = directory / f'audio-{index:03d}.{section["format"]}'
                        with path.open('xb') as out:
                            out.write(section['data'])
                            out.flush()
                            os.fsync(out.fileno())
                        manifest.append({k: v for k, v in section.items() if k != 'data'} | {'file': path.name})
                    atomic_json(directory / 'manifest.json', {'sections': manifest})
                    db.execute('''INSERT INTO local_transcriptions
                        (id,project_id,client_id,content_hash,status,phase,source_name,duration,created_at,updated_at,expires_at)
                        VALUES(?,?,?,?,'queued','Waiting for FUPCJ Server',?,?,?,?,?)''',
                        (job_id, project_id, client, content_hash, source, duration, now, now, now + RETAIN_SECONDS))
                directory = None  # The committed receipt now owns these files.
            finally:
                if directory:
                    shutil.rmtree(directory, ignore_errors=True)
            self.app.config['AUDIT_LOGS'].project_event(project_id, 'audio_received', jobType='whisper',
                                                        uploadedBytes=sum(len(s['data']) for s in sections))
            self.wake.set()
            return jsonify(id=job_id, status='queued'), 202

        @self.app.get('/api/projects/<project_id>/transcriptions/<job_id>')
        def local_transcription_status(project_id, job_id):
            self.sessions.project(project_id)
            return jsonify(self.snapshot(self.row(job_id, project_id)))

        @self.app.delete('/api/projects/<project_id>/transcriptions/<job_id>')
        def local_transcription_cancel(project_id, job_id):
            self.sessions.project(project_id)
            value = self.row(job_id, project_id)
            if value['status'] not in TERMINAL:
                self.update(job_id, unless_terminal=True, status='cancelled', phase='Cancelled', cancel_requested=1)
            self.wake.set()
            return jsonify(self.snapshot(self.row(job_id, project_id)))

    def update(self, job_id, only_processing=False, unless_terminal=False, **fields):
        fields['updated_at'] = time.time()
        if fields.get('status') in TERMINAL:
            fields['expires_at'] = time.time() + RETAIN_SECONDS
        guard = " AND status='processing' AND cancel_requested=0" if only_processing else ''
        if unless_terminal:
            guard += " AND status NOT IN ('complete','error','cancelled')"
        with self.db() as db:
            return db.execute('UPDATE local_transcriptions SET ' + ','.join(k + '=?' for k in fields) + ' WHERE id=?' + guard, (*fields.values(), job_id)).rowcount

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE local_transcriptions SET status='error',phase='Stopped',error='Server transcription stopped repeatedly. Retry this audio.' WHERE status='processing' AND attempts>=3")
            db.execute("UPDATE local_transcriptions SET status='queued',phase='Resuming after FUPCJ Server restart',updated_at=? WHERE status='processing'", (time.time(),))
            known = {r[0] for r in db.execute('SELECT id FROM local_transcriptions')}
        for path in self.root.iterdir():
            if path.is_dir() and re.fullmatch(r'[0-9a-f]{24}', path.name) and path.name not in known:
                shutil.rmtree(path, ignore_errors=True)

    def prune(self):
        with self.db() as db:
            rows = db.execute("SELECT id,status FROM local_transcriptions WHERE status IN ('complete','error','cancelled')").fetchall()
            for row in rows:
                # Completed text remains in SQLite; audio is no longer needed.
                shutil.rmtree(self.root / row['id'], ignore_errors=True)
            db.execute("DELETE FROM local_transcriptions WHERE expires_at<? AND status IN ('complete','error','cancelled')", (time.time(),))

    def work_once(self):
        # Protect against accidentally starting two worker threads in one process.
        if not self._worker_lock.acquire(blocking=False):
            return False
        try:
            if not self.capability()['ready']:
                return False
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT * FROM local_transcriptions WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
                if row is None:
                    return False
                value = dict(row)
                db.execute("UPDATE local_transcriptions SET status='processing',phase='Loading Whisper model',attempts=attempts+1,updated_at=? WHERE id=?", (time.time(), value['id']))
            processing_started = time.monotonic()
            try:
                result = self.run_local(value)
                current = self.row(value['id'])
                if current['status'] == 'cancelled':
                    return True
                if self.stop.is_set():
                    self.update(value['id'], only_processing=True, status='queued', phase='Waiting for FUPCJ Server restart')
                elif result is not None:
                    result_json = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
                    if len(result_json.encode()) > RESULT_LIMIT:
                        raise RuntimeError('result-too-large')
                    self.update(value['id'], only_processing=True, status='complete', phase='Ready', progress=100, result_json=result_json, error=None)
                else:
                    raise RuntimeError('no-result')
            except Exception as error:
                if self.row(value['id'])['status'] != 'cancelled':
                    self.app.logger.exception('Local transcription job %s failed.', value['id'])
                    safe = error if isinstance(error, LocalRunnerError) else LocalRunnerError(failure_code(error, 'startup'))
                    self.update(value['id'], only_processing=True, status='error', phase='Stopped', error=str(safe))
            finally:
                current = self.row(value['id'])
                self.app.config['AUDIT_LOGS'].project_event(value['project_id'], 'processing_finished', jobType='whisper',
                    outcome=current['status'], processingWallSeconds=time.monotonic()-processing_started,
                    outputBytes=len((current.get('result_json') or '').encode('utf-8')))
            return True
        finally:
            self._worker_lock.release()

    def run_local(self, value):
        directory = self.root / value['id']
        command = [sys.executable, str(Path(__file__).resolve()), '--process', str(directory),
                   '--model', self.settings['modelPath'], '--threads', str(self.settings['cpuThreads']),
                   '--ffmpeg', self.app.config['FFMPEG']]
        if self.settings.get('packagesPath'):
            command += ['--packages', self.settings['packagesPath']]
        env = os.environ.copy()
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', OMP_NUM_THREADS=str(self.settings['cpuThreads']),
                   OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS=str(self.settings['cpuThreads']))
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS} if os.name == 'nt' else {}
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env, **flags)
        diagnostic = bytearray()
        def drain_errors():
            while chunk := child.stderr.read(4096):
                diagnostic.extend(chunk)
                del diagnostic[:-DIAGNOSTIC_LIMIT]
        reader = threading.Thread(target=drain_errors, name='vision-local-errors', daemon=True)
        reader.start()
        started, previous, last_stage = time.monotonic(), None, 'startup'
        try:
            while child.poll() is None:
                if self.stop.wait(0.5) or self.row(value['id'])['cancel_requested']:
                    return None
                if time.monotonic() - started > min(12 * 3600, max(1800, value['duration'] * 4 + 900)):
                    raise TimeoutError('local-transcription-timeout')
                checkpoint = directory / 'progress.json'
                if checkpoint.is_file():
                    progress = json.loads(checkpoint.read_text(encoding='utf-8'))
                    last_stage = progress.get('stage', 'startup')
                    state = (progress.get('phase'), progress.get('progress'))
                    if state != previous:
                        self.update(value['id'], only_processing=True, phase=str(state[0])[:120], progress=min(99, max(0, float(state[1]))))
                        previous = state
                if last_stage in ('startup', 'dependencies') and time.monotonic() - started > DEPENDENCY_TIMEOUT:
                    raise LocalRunnerError('dependency-timeout', last_stage)
            if child.returncode:
                reader.join(timeout=2)
                self.app.logger.error('Local transcription job %s worker exited %s. Local diagnostic:\n%s',
                    value['id'], child.returncode, diagnostic.decode('utf-8', errors='replace'))
                failure, progress = {}, {}
                for name, target in (('failure.json', failure), ('progress.json', progress)):
                    try:
                        path = directory / name
                        if path.stat().st_size <= 8192:
                            data = json.loads(path.read_text(encoding='utf-8'))
                            if isinstance(data, dict):
                                target.update(data)
                    except (OSError, ValueError):
                        pass
                raise LocalRunnerError(failure.get('code', 'worker'), progress.get('stage', 'startup'),
                                       failure.get('exception'), child.returncode)
            path = directory / 'result.json'
            if path.stat().st_size > RESULT_LIMIT:
                raise RuntimeError('result-too-large')
            return json.loads(path.read_text(encoding='utf-8'))
        finally:
            if child.stdin:
                child.stdin.close()
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            reader.join(timeout=2)
            if not reader.is_alive():
                child.stderr.close()

    def start(self):
        self.recover()
        self.stop.clear()
        def loop():
            while not self.stop.is_set():
                try:
                    self.prune()
                    if self.work_once():
                        continue
                except Exception:
                    self.app.logger.error('Local transcription worker encountered a local error.')
                self.wake.wait(30)
                self.wake.clear()
        threading.Thread(target=loop, name='vision-local-transcription', daemon=True).start()


def process_directory(directory, model_path, cpu_threads, ffmpeg, packages=None, model_factory=None):
    """Child-only implementation. All model and executable paths are trusted config."""
    directory = Path(directory)
    def stage(name, phase=None, progress=0):
        atomic_json(directory / 'progress.json', {'stage': name, 'phase': phase or STAGES[name].capitalize(), 'progress': progress})
    stage('dependencies')
    if packages:
        sys.path.insert(0, str(packages))
    if model_factory is None:
        from faster_whisper import WhisperModel
        model_factory = WhisperModel
    model_path, directory = Path(model_path), Path(directory)
    if not model_path.is_dir() or not (model_path / 'tokenizer.json').is_file():
        raise RuntimeError('local-model-missing')
    stage('model')
    model = model_factory(str(model_path), device='cpu', compute_type='int8', cpu_threads=max(1, min(4, cpu_threads)),
                          num_workers=1, local_files_only=True)
    sections = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))['sections']
    checkpoint_path = directory / 'checkpoint.json'
    checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.is_file() else {'chunks': {}}
    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    probe = str(Path(ffmpeg).with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe'))
    actual_total = 0
    for section in sections:
        stage('probe')
        source = directory / section['file']
        probe_result = subprocess.run([probe, '-v', 'error', '-protocol_whitelist', 'file,pipe', '-f', section['format'],
            '-i', str(source), '-show_entries', 'format=duration', '-of', 'json'], capture_output=True, timeout=60, check=True, **flags)
        actual = float(json.loads(probe_result.stdout)['format']['duration'])
        if not math.isfinite(actual) or actual <= 0 or actual > section['end'] - section['start'] + 5:
            raise ValueError('invalid-audio-duration')
        section['duration'] = actual
        actual_total += actual
    if actual_total > MAX_DURATION + 5:
        raise ValueError('audio-too-long')
    completed, result_sections = 0, []
    for index, section in enumerate(sections):
        lines = []
        for offset in range(0, math.ceil(section['duration']), CHUNK_SECONDS):
            length = min(CHUNK_SECONDS, section['duration'] - offset)
            key = f'{index}:{offset}'
            if key not in checkpoint['chunks']:
                if shutil.disk_usage(directory).free < DISK_RESERVE:
                    raise OSError('low-disk-space')
                wav = directory / 'working.wav'
                try:
                    phase = f'Transcribing section {index+1} of {len(sections)}'
                    stage('decode', f'Decoding section {index+1} of {len(sections)}', min(99, 100 * completed / actual_total))
                    subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-y', '-protocol_whitelist', 'file,pipe',
                        '-f', section['format'], '-ss', str(offset), '-i', str(directory / section['file']), '-t', str(length),
                        '-vn', '-threads', '1', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(wav)],
                        stdout=subprocess.DEVNULL, timeout=120, check=True, **flags)
                    stage('transcribe', phase, min(99, 100 * completed / actual_total))
                    segments, _info = model.transcribe(str(wav), beam_size=3, vad_filter=True,
                        condition_on_previous_text=False, word_timestamps=False)
                    chunk_lines, text_size, last_progress = [], 0, 0
                    for segment in segments:
                        text = ' '.join(str(segment.text).split())
                        if text and math.isfinite(float(segment.start)):
                            when = min(section['end'], section['start'] + offset + max(0, float(segment.start)))
                            chunk_lines.append(f'[{timestamp(when)}] {text}')
                            text_size += len(chunk_lines[-1])
                        if text_size > RESULT_LIMIT:
                            raise ValueError('transcript-too-large')
                        if time.monotonic() - last_progress > 1:
                            position = min(length, max(0, float(segment.start)))
                            stage('transcribe', phase, min(99, 100 * (completed + position) / actual_total))
                            last_progress = time.monotonic()
                    checkpoint['chunks'][key] = chunk_lines
                    atomic_json(checkpoint_path, checkpoint)
                    if checkpoint_path.stat().st_size > RESULT_LIMIT:
                        raise ValueError('transcript-too-large')
                finally:
                    wav.unlink(missing_ok=True)
            lines.extend(checkpoint['chunks'][key])
            completed += length
            stage('transcribe', f'Transcribing section {index+1} of {len(sections)}', min(99, 100 * completed / actual_total))
        result_sections.append({'start': section['start'], 'end': section['end'], 'text': '\n'.join(lines)})
    stage('save', progress=99)
    atomic_json(directory / 'result.json', {'text': '\n\n'.join(s['text'] for s in result_sections), 'sections': result_sections})


def start_parent_watch():
    """Stop this worker when its parent's otherwise unused input pipe closes."""
    descriptor = sys.stdin.fileno()
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        import msvcrt

        # A blocking CRT read (including os.read) holds a descriptor lock that
        # native DLL initialization can also need: numpy/numpy#24290. Inspect the
        # pipe through Win32 instead. No thread reads from this handle, and the
        # parent never writes to it; its only purpose is parent-lifetime tracking.
        peek = ctypes.WinDLL('kernel32', use_last_error=True).PeekNamedPipe
        peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                         wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPDWORD]
        peek.restype = wintypes.BOOL
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))

        def wait_for_close():
            while peek(handle, None, 0, None, None, None):
                time.sleep(0.5)
    else:
        def wait_for_close():
            # A raw read avoids holding Python's BufferedReader lock during
            # normal interpreter shutdown. Windows must not use this branch.
            os.read(descriptor, 1)

    def watch():
        try:
            wait_for_close()
        finally:
            os._exit(1)

    thread = threading.Thread(target=watch, name='vision-local-parent', daemon=True)
    thread.start()
    return thread


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--process', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--ffmpeg', required=True)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--packages')
    args = parser.parse_args()
    if os.name != 'nt':
        os.nice(10)
    # Model assets must already exist. Reject network access even if an optional
    # dependency unexpectedly tries to discover/download something at runtime.
    def offline_only(event, _args):
        if event in ('socket.connect', 'socket.getaddrinfo'):
            raise RuntimeError('Offline transcription cannot access the network.')
    sys.addaudithook(offline_only)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    try:
        start_parent_watch()
        process_directory(args.process, args.model, args.threads, args.ffmpeg, args.packages)
    except Exception as error:
        traceback.print_exc()
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            # FFprobe captures stderr; keep it in the private PC log only.
            print(error.stderr.decode('utf-8', errors='replace')[-8192:], file=sys.stderr)
        try:
            progress = json.loads((Path(args.process) / 'progress.json').read_text(encoding='utf-8'))
            atomic_json(Path(args.process) / 'failure.json', {
                'code': failure_code(error, progress.get('stage', 'startup')),
                'exception': type(error).__name__,
            })
        except (OSError, ValueError):
            pass
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
