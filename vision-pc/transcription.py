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
import signal
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
# A combined receipt also carries the untouched speech track and export SRT.
# Each text/worker result remains bounded at 4 MiB; allow their representations.
COMBINED_RESULT_LIMIT = 16 * 1024 * 1024
SOUND_WARNING = 'Sound recognition could not finish. The speech transcript is preserved; retry with sound recognition enabled after checking FUPCJ Server.'
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


def sound_caption(label, enhanced=False):
    if enhanced:
        return '**' + label.replace('*', '') + '**'
    aliases = {'Meow': 'cat meows', 'Bark': 'dog barks', 'Smash, crash': 'smash/crash', 'Wail, moan': 'wail/moan'}
    return '*' + aliases.get(label, label.lower()) + '*'


def combined_srt(cues):
    """Split overlaps into valid, non-overlapping SRT cues with both tracks."""
    boundaries = {}
    for number, cue in enumerate(cues):
        start, end = round(cue['start'] * 1000), round(cue['end'] * 1000)
        if end <= start:
            continue
        boundaries.setdefault(start, [[], []])[0].append(number)
        boundaries.setdefault(end, [[], []])[1].append(number)
    active, previous, blocks, text_size = set(), None, [], 0
    for moment in sorted(boundaries):
        if previous is not None and moment > previous and active:
            text = '\n'.join(dict.fromkeys(cues[index]['text'] for index in sorted(active)))
            if blocks and blocks[-1][1] == previous and blocks[-1][2] == text:
                blocks[-1][1] = moment
            else:
                blocks.append([previous, moment, text])
                text_size += len(text.encode('utf-8')) + 70
                if text_size > RESULT_LIMIT:
                    raise ValueError('combined-srt-too-large')
        begins, ends = boundaries[moment]
        active.difference_update(ends)
        active.update(begins)
        previous = moment
    return '\n\n'.join(f'{index}\n{timestamp(start / 1000).replace(".", ",")} --> '
                       f'{timestamp(end / 1000).replace(".", ",")}\n{text}'
                       for index, (start, end, text) in enumerate(blocks, 1)) + ('\n' if blocks else '')


def combine_transcription(speech, sound_result):
    """Keep speech recoverable even if the optional sound worker fails."""
    events = []
    sections = speech.get('sections', [])
    if sound_result is not None:
        if not isinstance(sound_result, dict) or sound_result.get('status') != 'completed':
            raise ValueError('sound-recognition-incomplete')
        raw = sound_result.get('soundEvents')
        if not isinstance(raw, list) or len(raw) > 50000:
            raise ValueError('invalid-sound-events')
        for event in raw:
            if not isinstance(event, dict):
                raise ValueError('invalid-sound-event')
            start, end, score, label = (event.get(name) for name in ('start', 'end', 'score', 'label'))
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, end, score)):
                raise ValueError('invalid-sound-event-timing')
            if not (0 <= start < end <= MAX_DURATION and 0 <= score <= 1):
                raise ValueError('invalid-sound-event-bounds')
            if not isinstance(label, str) or not label.strip() or len(label) > 160:
                raise ValueError('invalid-sound-event-label')
            if not any(section['start'] <= start < end <= section['end'] + .001 for section in sections):
                raise ValueError('sound-event-outside-audio-section')
            label = ' '.join(label.split())
            events.append({'start': start, 'end': end, 'label': label, 'score': score,
                           **({'enhanced': True} if event.get('enhanced') is True else {})})
    events.sort(key=lambda value: (value['start'], value['end'], value['label']))
    segments = speech.get('speechSegments', [])
    cues = [dict(value) for value in segments]
    cues.extend({'start': event['start'], 'end': event['end'], 'text': sound_caption(event['label'], event.get('enhanced', False))} for event in events)
    cues.sort(key=lambda value: (value['start'], value['end'], value['text']))
    combined_sections = []
    for section in sections:
        section_cues = [cue for cue in cues if section['start'] <= cue['start'] < section['end']]
        text = '\n'.join(f'[{timestamp(cue["start"])}] {cue["text"]}' for cue in section_cues)
        combined_sections.append({**section, 'text': text or section.get('text', '')})
    if sum(len(section['text'].encode('utf-8')) for section in combined_sections) > RESULT_LIMIT:
        raise ValueError('combined-timeline-too-large')
    return {**speech, 'speechText': speech.get('text', ''), 'speechSegments': segments,
            'text': '\n\n'.join(section['text'] for section in combined_sections), 'sections': combined_sections,
            'soundEvents': events, 'soundEventStatus': 'completed' if sound_result is not None else 'failed',
            'warnings': [] if sound_result is not None else [SOUND_WARNING], 'combinedSrt': combined_srt(cues)}


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
        gemini = config.get('geminiProcessing') or {}
        if not isinstance(gemini, dict):
            raise ValueError('geminiProcessing must be a configuration object.')
        self.gemini_settings = {'speechModel': gemini.get('speechModel', 'gemini-3.5-transcribe'),
                                'soundModel': gemini.get('soundModel', 'gemini-3.8-flash')}
        if any(not isinstance(value, str) or not re.fullmatch(r'gemini-[A-Za-z0-9._-]{1,100}', value)
               for value in self.gemini_settings.values()):
            raise ValueError('Gemini model identifiers must be valid model names.')
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
        sound = config.get('localSoundEvents')
        sound = sound if isinstance(sound, dict) else {}
        self.sound_settings = {'enabled': sound.get('enabled') is True,
                               'device': sound.get('device') if sound.get('device') in ('cpu', 'cuda') else 'cpu'}
        for key in ('pythonPath', 'assetsPath'):
            path = sound.get(key)
            if isinstance(path, str) and path:
                path = Path(path)
                self.sound_settings[key] = str((path if path.is_absolute() else Path(config_path).parent / path).resolve())
        try:
            self.sound_settings['cpuThreads'] = max(1, min(4, int(sound.get('cpuThreads', 2))))
        except (TypeError, ValueError):
            self.sound_settings['cpuThreads'] = 2
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
            columns = {row[1] for row in db.execute('PRAGMA table_info(local_transcriptions)')}
            for name, definition in (('provider', "TEXT NOT NULL DEFAULT 'whisper'"), ('requester_uid', 'TEXT')):
                if name not in columns:
                    db.execute(f'ALTER TABLE local_transcriptions ADD COLUMN {name} {definition}')
            db.execute('CREATE INDEX IF NOT EXISTS transcriptions_access_queue ON local_transcriptions(provider,requester_uid,status)')

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

    def sound_capability(self, settings=None):
        settings = self.sound_settings if settings is None else settings
        # Import only the standard-library adapter here, never the model runtime.
        try:
            from sound_model import capability
            result = capability(settings)
        except (ImportError, OSError, ValueError):
            result = dict(available=False, ready=False, installed=False,
                          status='not-installed' if settings.get('enabled') else 'disabled')
        result = dict(result, engine='PretrainedSED-BEATs')
        ffmpeg = Path(self.app.config['FFMPEG'])
        tools_ready = ffmpeg.is_file() and ffmpeg.with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe').is_file()
        result['ready'] = result['available'] = bool(settings.get('enabled') is True and result.get('available', result.get('ready')) and tools_ready)
        if settings.get('enabled') is not True:
            result['status'] = 'disabled'
        if settings.get('enabled') is True and not tools_ready and result.get('installed'):
            result['status'] = 'missing-media-tools'
        return result

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
        if body.get('provider', 'whisper') not in ('whisper', 'local', 'gemini'):
            raise self.Error('Choose an existing transcription provider.')
        if not isinstance(body.get('includeSoundEvents', False), bool):
            raise self.Error('includeSoundEvents must be true or false.')
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
        if body.get('provider') == 'gemini':
            digest.update(b'\x00provider:gemini\x00')
        # Keep existing speech-only receipts idempotent across this upgrade.
        if body.get('includeSoundEvents'):
            digest.update(b'\x00includeSoundEvents:true\x00')
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

    def enqueue(self, project_id, body, requester_uid, auth_kind):
        """Shared durable queue entry; caller must authorize project ownership."""
        client, source, sections, duration, content_hash = self.validate(body)
        include_sounds = body.get('includeSoundEvents', False)
        provider = body.get('provider', 'whisper')
        if provider == 'local':
            provider = 'whisper'
        access = self.app.config.get('GEMINI_ACCESS')
        if provider == 'gemini' and (auth_kind != 'firebase-google' or not access):
            raise self.Error('Sign in with Google to request Gemini processing.', 403)
        directory = None
        try:
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                previous = db.execute('SELECT * FROM local_transcriptions WHERE project_id=? AND client_id=?', (project_id, client)).fetchone()
                if previous:
                    if previous['content_hash'] != content_hash:
                        raise self.Error('This request identifier belongs to different audio. Start a new transcription.', 409)
                    return dict(id=previous['id'], status=previous['status'])
                if provider == 'whisper' and not self.capability()['ready']:
                    raise self.Error('Server transcription is not ready. Enable the server transcription plugin on FUPCJ Server first.', 503)
                if include_sounds and not self.sound_capability()['ready']:
                    raise self.Error('Sound recognition is not ready. Run InstallSoundEvents on FUPCJ Server first, or turn off Include sound effects to transcribe speech only.', 503)
                pending = db.execute("SELECT COUNT(*) FROM local_transcriptions WHERE status IN ('queued','processing','approval_waiting')").fetchone()[0]
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
                access_status = access.request_access(requester_uid, db=db) if provider == 'gemini' else 'approved'
                status = 'queued' if access_status == 'approved' else 'approval_waiting'
                phase = ('Waiting for FUPCJ Server' if status == 'queued' else self.approval_phase(access_status))
                atomic_json(directory / 'manifest.json', {'sections': manifest, 'includeSoundEvents': include_sounds,
                    'provider': provider, 'geminiSettings': dict(self.gemini_settings) if provider == 'gemini' else None,
                    'whisperSettings': dict(self.settings), 'soundSettings': dict(self.sound_settings) if include_sounds else None})
                db.execute('''INSERT INTO local_transcriptions
                    (id,project_id,client_id,content_hash,status,phase,source_name,duration,created_at,updated_at,expires_at,provider,requester_uid)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (job_id, project_id, client, content_hash, status, phase, source, duration, now, now, now + RETAIN_SECONDS,
                     provider, requester_uid))
            directory = None  # The committed receipt now owns these files.
        finally:
            if directory:
                shutil.rmtree(directory, ignore_errors=True)
        self.app.config['AUDIT_LOGS'].project_event(project_id, 'audio_received', jobType=provider + ('_sound' if include_sounds else ''),
                                                    uploadedBytes=sum(len(s['data']) for s in sections))
        self.wake.set()
        return dict(id=job_id, status=status, phase=phase)

    def register_routes(self):
        from flask import g, jsonify, request

        @self.app.post('/api/projects/<project_id>/transcriptions')
        def local_transcription_create(project_id):
            self.sessions.project(project_id)
            request.max_content_length = BODY_LIMIT
            body = request.get_json(silent=True)
            return jsonify(self.enqueue(project_id, body, g.uid, g.auth_kind)), 202

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

    @staticmethod
    def approval_phase(status):
        return {'denied': 'Gemini access denied', 'revoked': 'Gemini access revoked'}.get(status, 'Waiting for Gemini approval')

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
            local_ready = self.capability()['ready']
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT * FROM local_transcriptions WHERE status='queued' AND (provider='gemini' OR ?=1) ORDER BY created_at LIMIT 1", (int(local_ready),)).fetchone()
                if row is None:
                    return False
                value = dict(row)
                if value.get('provider') == 'gemini':
                    access = self.app.config.get('GEMINI_ACCESS')
                    approval = access.status(value['requester_uid'], db=db) if access else 'pending'
                    if approval != 'approved':
                        db.execute("UPDATE local_transcriptions SET status='approval_waiting',phase=?,updated_at=? WHERE id=?",
                                   (self.approval_phase(approval), time.time(), value['id']))
                        return True
                trials = self.app.config.get('TRIALS')
                if trials and trials.expired(value['project_id']):
                    db.execute("UPDATE local_transcriptions SET status='cancelled',cancel_requested=1,phase='Trial ended' WHERE id=?", (value['id'],))
                    return True
                db.execute("UPDATE local_transcriptions SET status='processing',phase=?,attempts=attempts+1,updated_at=? WHERE id=?",
                           ('Preparing Gemini processing' if value.get('provider') == 'gemini' else 'Loading Whisper model', time.time(), value['id']))
            processing_started = time.monotonic()
            job_type, partial = value.get('provider') or 'whisper', False
            try:
                manifest = json.loads((self.root / value['id'] / 'manifest.json').read_text(encoding='utf-8'))
                if manifest.get('includeSoundEvents'):
                    job_type += '_sound'
                result = self.run_gemini(value, manifest) if value.get('provider') == 'gemini' else self.run_local(value)
                current = self.row(value['id'])
                if current['status'] == 'cancelled':
                    return True
                if self.stop.is_set():
                    self.update(value['id'], only_processing=True, status='queued', phase='Waiting for FUPCJ Server restart')
                elif result is not None:
                    partial = result.get('soundEventStatus') == 'failed'
                    result_json = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
                    if len(result_json.encode()) > (COMBINED_RESULT_LIMIT if 'soundEventStatus' in result else RESULT_LIMIT):
                        raise RuntimeError('result-too-large')
                    self.update(value['id'], only_processing=True, status='complete', phase='Speech ready; sound recognition failed' if partial else 'Ready', progress=100, result_json=result_json, error=None)
                else:
                    raise RuntimeError('no-result')
            except Exception as error:
                if self.row(value['id'])['status'] != 'cancelled':
                    if value.get('provider') == 'gemini':
                        from gemini_access import GeminiAccessDenied
                        from gemini_processing import GeminiInterrupted, GeminiProcessingError
                        if isinstance(error, GeminiAccessDenied):
                            self.update(value['id'], only_processing=True, status='approval_waiting',
                                        phase=self.approval_phase(error.status), error=None)
                        elif isinstance(error, GeminiInterrupted) and self.stop.is_set():
                            self.update(value['id'], only_processing=True, status='queued', phase='Waiting for FUPCJ Server restart')
                        else:
                            self.app.logger.error('Gemini transcription job %s failed (%s).', value['id'], type(error).__name__)
                            safe = str(error) if isinstance(error, GeminiProcessingError) else 'Gemini processing could not finish. Check the owner activity monitor.'
                            self.update(value['id'], only_processing=True, status='error', phase='Stopped', error=safe)
                    else:
                        self.app.logger.exception('Local transcription job %s failed.', value['id'])
                        safe = error if isinstance(error, LocalRunnerError) else LocalRunnerError(failure_code(error, 'startup'))
                        self.update(value['id'], only_processing=True, status='error', phase='Stopped', error=str(safe))
            finally:
                current = self.row(value['id'])
                self.app.config['AUDIT_LOGS'].project_event(value['project_id'], 'processing_finished', jobType=job_type,
                    outcome='speech_only' if partial and current['status'] == 'complete' else current['status'], processingWallSeconds=time.monotonic()-processing_started,
                    outputBytes=len((current.get('result_json') or '').encode('utf-8')))
            return True
        finally:
            self._worker_lock.release()

    def run_gemini(self, value, manifest):
        from gemini_processing import GeminiProcessor
        return GeminiProcessor(self, value, manifest).run()

    def run_local(self, value):
        directory = self.root / value['id']
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        settings = manifest.get('whisperSettings') or self.settings
        include_sounds = manifest.get('includeSoundEvents') is True
        command = [sys.executable, str(Path(__file__).resolve()), '--process', str(directory),
                   '--model', settings['modelPath'], '--threads', str(settings['cpuThreads']),
                   '--ffmpeg', self.app.config['FFMPEG']]
        if settings.get('packagesPath'):
            command += ['--packages', settings['packagesPath']]
        # Save speech before loading a separate sound model. A service restart
        # during sound recognition can reuse the completed speech transcript.
        speech_path = directory / 'result.json'
        if include_sounds and speech_path.is_file() and speech_path.stat().st_size <= RESULT_LIMIT:
            result = json.loads(speech_path.read_text(encoding='utf-8'))
        else:
            result = self.run_worker(value, command, settings['cpuThreads'], sound=False, combined=include_sounds)
        if result is None or not include_sounds:
            return result
        if self.stop.is_set() or self.row(value['id'])['cancel_requested']:
            return None
        try:
            sounds = self.run_sound_events(value, manifest.get('soundSettings') or {})
            if sounds is None:
                return None
            combined = combine_transcription(result, sounds)
            if len(json.dumps(combined, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > COMBINED_RESULT_LIMIT:
                raise ValueError('combined-result-too-large')
            return combined
        except Exception:
            self.app.logger.exception('Sound recognition job %s failed; preserving speech.', value['id'])
            return combine_transcription(result, None)

    def run_sound_events(self, value, settings, abort=None):
        if not self.sound_capability(settings)['ready']:
            raise RuntimeError('sound-recognition-not-ready')
        directory = self.root / value['id']
        command = [settings['pythonPath'], str(Path(__file__).with_name('sound_model.py').resolve()),
                   '--process', str(directory), '--assets', settings['assetsPath'],
                   '--ffmpeg', self.app.config['FFMPEG'], '--device', settings.get('device', 'cpu'),
                   '--threads', str(settings.get('cpuThreads', 2))]
        self.update(value['id'], only_processing=True, phase='Recognizing sound events', progress=50)
        return self.run_worker(value, command, settings.get('cpuThreads', 2), sound=True, combined=True, abort=abort)

    def run_worker(self, value, command, threads, sound=False, combined=False, abort=None):
        directory = self.root / value['id']
        prefix = 'sound-' if sound else ''
        env = os.environ.copy()
        env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', OMP_NUM_THREADS=str(threads),
                   OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS=str(threads))
        flags = ({'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS}
                 if os.name == 'nt' else {'start_new_session': True} if sound else {})
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
                if self.stop.wait(0.5) or self.row(value['id'])['cancel_requested'] or (abort is not None and abort.is_set()):
                    return None
                if time.monotonic() - started > min(12 * 3600, max(1800, value['duration'] * (8 if sound else 4) + 900)):
                    raise TimeoutError('local-transcription-timeout')
                checkpoint = directory / (prefix + 'progress.json')
                if checkpoint.is_file():
                    progress = json.loads(checkpoint.read_text(encoding='utf-8'))
                    last_stage = progress.get('stage', 'startup')
                    state = (progress.get('phase'), progress.get('progress'))
                    if state != previous:
                        percent = min(99, max(0, float(state[1] or 0)))
                        if combined:
                            percent = (50 if sound else 0) + percent / 2
                        self.update(value['id'], only_processing=True, phase=str(state[0] or 'Recognizing sound events')[:120], progress=percent)
                        previous = state
                if last_stage in ('startup', 'dependencies') and time.monotonic() - started > (max(300, DEPENDENCY_TIMEOUT) if sound else DEPENDENCY_TIMEOUT):
                    raise LocalRunnerError('dependency-timeout', last_stage)
            if child.returncode:
                reader.join(timeout=2)
                self.app.logger.error('%s job %s worker exited %s. Local diagnostic:\n%s',
                    'Sound recognition' if sound else 'Local transcription', value['id'], child.returncode, diagnostic.decode('utf-8', errors='replace'))
                failure, progress = {}, {}
                for name, target in ((prefix + 'failure.json', failure), (prefix + 'progress.json', progress)):
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
            path = directory / (prefix + 'result.json')
            if path.stat().st_size > RESULT_LIMIT:
                raise RuntimeError('result-too-large')
            return json.loads(path.read_text(encoding='utf-8'))
        finally:
            if child.stdin:
                child.stdin.close()
            if child.poll() is None:
                if sound and os.name == 'nt':
                    # The decoder belongs to this one worker; terminate its
                    # process tree so cancellation cannot leave FFmpeg running.
                    try:
                        subprocess.run(['taskkill.exe', '/PID', str(child.pid), '/T', '/F'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                    except (OSError, subprocess.TimeoutExpired):
                        child.terminate()
                elif sound:
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                else:
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
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    sections = manifest['sections']
    include_sounds = manifest.get('includeSoundEvents') is True
    checkpoint_path = directory / 'checkpoint.json'
    checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.is_file() else {'chunks': {}}
    if include_sounds:
        checkpoint.setdefault('speechChunks', {})
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
    completed, result_sections, speech_segments = 0, [], []
    for index, section in enumerate(sections):
        lines = []
        for offset in range(0, math.ceil(section['duration']), CHUNK_SECONDS):
            length = min(CHUNK_SECONDS, section['duration'] - offset)
            key = f'{index}:{offset}'
            if key not in checkpoint['chunks'] or (include_sounds and key not in checkpoint['speechChunks']):
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
                    chunk_lines, chunk_segments, text_size, last_progress = [], [], 0, 0
                    for segment in segments:
                        text = ' '.join(str(segment.text).split())
                        if text and math.isfinite(float(segment.start)):
                            when = min(section['end'], section['start'] + offset + max(0, float(segment.start)))
                            chunk_lines.append(f'[{timestamp(when)}] {text}')
                            text_size += len(chunk_lines[-1])
                            if include_sounds:
                                section_limit = min(section['end'], section['start'] + offset + length)
                                start = min(section_limit, when)
                                finish = float(getattr(segment, 'end', float(segment.start) + 1))
                                if math.isfinite(finish):
                                    finish = min(section_limit, section['start'] + offset + max(0, finish))
                                    if finish > start:
                                        chunk_segments.append({'start': start, 'end': finish, 'text': text})
                        if text_size > RESULT_LIMIT:
                            raise ValueError('transcript-too-large')
                        if time.monotonic() - last_progress > 1:
                            position = min(length, max(0, float(segment.start)))
                            stage('transcribe', phase, min(99, 100 * (completed + position) / actual_total))
                            last_progress = time.monotonic()
                    checkpoint['chunks'][key] = chunk_lines
                    if include_sounds:
                        checkpoint['speechChunks'][key] = chunk_segments
                    atomic_json(checkpoint_path, checkpoint)
                    if checkpoint_path.stat().st_size > RESULT_LIMIT:
                        raise ValueError('transcript-too-large')
                finally:
                    wav.unlink(missing_ok=True)
            lines.extend(checkpoint['chunks'][key])
            if include_sounds:
                speech_segments.extend(checkpoint['speechChunks'][key])
            completed += length
            stage('transcribe', f'Transcribing section {index+1} of {len(sections)}', min(99, 100 * completed / actual_total))
        result_sections.append({'start': section['start'], 'end': section['end'], 'text': '\n'.join(lines)})
    stage('save', progress=99)
    result = {'text': '\n\n'.join(s['text'] for s in result_sections), 'sections': result_sections}
    if include_sounds:
        result['speechSegments'] = speech_segments
    atomic_json(directory / 'result.json', result)


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
