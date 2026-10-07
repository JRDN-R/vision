"""Narrow, authenticated dictation with allowlisted local Whisper fallback. Never grants project Gemini access.

Only microphone audio and an allowlisted provider are accepted; the model, transcription mode and transport
are fixed on the server. No client prompt, URL, model, tools or project ID is
forwarded. Audio is decoded/capped locally and removed after each request.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave

from flask import jsonify, request
import requests

MAX_SECONDS = 180
MAX_BYTES = 12 * 1024 * 1024
MAX_REPLY = 1024 * 1024
RECEIPT_SECONDS = 15 * 60
MODEL = 'gemini-3.5-transcribe'
API_URL = 'https://generativelanguage.googleapis.com/v1beta/interactions'
FORMATS = {'audio/webm': 'matroska,webm', 'audio/mp4': 'mov', 'audio/m4a': 'mov',
           'audio/x-m4a': 'mov', 'audio/ogg': 'ogg', 'audio/wav': 'wav',
           'audio/x-wav': 'wav', 'audio/mpeg': 'mp3'}


def plain_transcript(data: dict) -> str:
    """Extract text only, never annotations, SRT timestamps or reasoning."""
    from gemini_processing import response_content
    text = data.get('output_text')
    if not isinstance(text, str):
        text = ''.join(c['text'] for c in response_content(data)
                       if c.get('type') == 'text' and isinstance(c.get('text'), str))
    # Defensive cleanup if a provider unexpectedly wraps a transcript as subtitles.
    if re.search(r'\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->', text):
        text = re.sub(r'(?m)^\s*\d+\s*\n(?=\s*\d{1,2}:\d{2}:\d{2}[,.])', '', text)
        text = re.sub(r'(?m)^.*\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->.*$', '', text)
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text).strip()[:50000]


class Dictation:
    def __init__(self, venture):
        self.v = venture
        self.app, self.db, self.Error = venture.app, venture.db, venture.Error
        self.slots = threading.BoundedSemaphore(4)
        self.register_routes()

    def initialize(self):
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS venture_dictation_requests (
                id TEXT PRIMARY KEY,uid TEXT NOT NULL,client_id TEXT NOT NULL,
                digest TEXT NOT NULL,status TEXT NOT NULL,text TEXT NOT NULL DEFAULT '',
                error TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL,
                UNIQUE(uid,client_id));
                CREATE INDEX IF NOT EXISTS venture_dictation_owner_time
                ON venture_dictation_requests(uid,created_at);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(venture_dictation_requests)')}
            for column, definition in (('provider', "TEXT NOT NULL DEFAULT 'gemini'"), ('error_code', 'TEXT')):
                if column not in columns:
                    db.execute(f'ALTER TABLE venture_dictation_requests ADD COLUMN {column} {definition}')
            db.execute("UPDATE venture_dictation_requests SET status='interrupted',error=?,updated_at=? WHERE status='processing'",
                       ('Dictation was interrupted. It was not sent to Gemini again automatically.', time.time()))
            self.prune(db)
        root = self.app.config['DATA_DIR'] / 'temporary'
        for directory in root.glob('venture-dictation-*'):
            if directory.is_dir() and not directory.is_symlink():
                shutil.rmtree(directory, ignore_errors=True)

    def prune(self, db):
        now = time.time()
        # Keep ID/digest-only receipts for a day for replay protection and throttling.
        db.execute("UPDATE venture_dictation_requests SET text='',error=NULL,status='expired' "
                   "WHERE status!='processing' AND updated_at<? AND status!='expired'", (now-RECEIPT_SECONDS,))
        db.execute("DELETE FROM venture_dictation_requests WHERE created_at<? AND status!='processing'", (now-86400,))

    def capability(self):
        credentials = self.app.config.get('GEMINI_CREDENTIALS')
        ffmpeg = self.app.config.get('FFMPEG', '')
        ready = bool(credentials and credentials.available() and (Path(ffmpeg).is_file() or shutil.which(ffmpeg)))
        local = self.v.transcription.capability() if self.v.transcription else {'ready': False, 'status': 'not-installed'}
        return dict(allowed=True, ready=ready, provider='gemini', model=MODEL,
                    maxSeconds=MAX_SECONDS, format='text', projectAccessGranted=False, recoveryV2=True, automaticFallbackV1=True,
                    providers={'gemini': {'ready': ready}, 'whisper': {'ready': bool(local.get('ready')), 'status': local.get('status')}})

    def normalize(self, raw: bytes, mime: str, directory: Path) -> tuple[bytes, float]:
        source, target = directory / 'microphone.input', directory / 'microphone.wav'
        source.write_bytes(raw)
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        try:
            subprocess.run([self.app.config['FFMPEG'], '-v', 'error', '-nostdin', '-y',
                '-protocol_whitelist', 'file,pipe', '-f', FORMATS[mime], '-threads', '1', '-i', str(source),
                '-map', '0:a:0', '-vn', '-sn', '-dn', '-t', str(MAX_SECONDS), '-threads', '1',
                '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', '-map_metadata', '-1', str(target)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=45, **flags)
            if not target.is_file() or not 44 <= target.stat().st_size <= 16000*2*MAX_SECONDS+65536:
                raise ValueError()
            with wave.open(str(target), 'rb') as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
                    raise ValueError()
                frames = audio.readframes(16000*MAX_SECONDS)
            # Re-encode a canonical bounded WAV, even if the input lied about duration.
            result = io.BytesIO()
            with wave.open(result, 'wb') as audio:
                audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
                audio.writeframes(frames)
            if not frames:
                raise ValueError()
            return result.getvalue(), len(frames)/32000
        except (OSError, ValueError, wave.Error, subprocess.SubprocessError):
            raise self.Error('The microphone recording could not be read. Try recording again.', 422) from None

    def snapshot(self, row):
        return dict(requestId=row['client_id'], status=row['status'], text=row['text'],
                    error=row['error'], errorCode=row['error_code'], provider=row['provider'], format='text')

    def transcribe(self, uid, ident, audio, duration):
        # Deliberately does NOT call require_approved() or modify gemini_access.
        # This dedicated ASR model cannot run project prompts or use tools.
        key = self.app.config['GEMINI_CREDENTIALS'].get()
        usage_service = self.app.config['GEMINI_USAGE']
        receipt = usage_service.begin_request(uid, 'venture-dictation', 'dictation-'+ident,
            MODEL, kind='speech', event_start=0, event_end=duration, clip_duration=duration,
            request_key='dictation-v1')
        body = dict(model=MODEL, store=False,
                    input=[dict(type='audio', data=base64.b64encode(audio).decode('ascii'), mime_type='audio/wav')],
                    generation_config=dict(transcription_config=dict(mode=dict(type='verbatim'))))
        usage, status, http = {}, 'failed', None
        try:
            with requests.post(API_URL, headers={'x-goog-api-key': key, 'Content-Type': 'application/json'},
                               json=body, timeout=(15, 120), stream=True, allow_redirects=False) as response:
                http = response.status_code
                if not 200 <= http < 300:
                    raise self.provider_error(http)
                chunks = bytearray()
                for chunk in response.iter_content(65536):
                    chunks.extend(chunk)
                    if len(chunks) > MAX_REPLY:
                        raise self.Error('Gemini returned an oversized dictation response.', 502)
                data = json.loads(chunks)
                if not isinstance(data, dict) or data.get('status') not in (None, 'completed'):
                    raise self.Error('Gemini did not finish this dictation.', 502)
                usage = data.get('usage') or data.get('usageMetadata') or {}
                text = plain_transcript(data)
                status = 'succeeded'
                return text
        except (requests.RequestException, ValueError, TypeError):
            raise self.Error('Gemini could not return a result. The provider may have processed this audio. A new Gemini attempt may be billed; PC Whisper uses no transcription API credits.', 502, 'gemini_connection') from None
        finally:
            usage_service.finish_request(receipt, status=status, usage=usage, http_status=http)

    def provider_error(self, status):
        messages = {
            400: ('gemini_request', 'Gemini rejected the transcription request. Check the server model and request configuration.'),
            401: ('gemini_credentials', 'Gemini rejected the saved credential. Reconnect the server Gemini key.'),
            402: ('gemini_billing', 'Gemini reports a billing or prepaid-credit requirement. Check the Gemini project billing.'),
            403: ('gemini_access', 'Gemini denied access. Check the saved key, key restrictions and project permissions.'),
            404: ('gemini_model', 'The Gemini transcription model or endpoint was not found for this connection.'),
            429: ('gemini_quota', 'Gemini reports a quota or rate limit. This alone does not establish that funds are depleted.'),
        }
        code, message = messages.get(status, ('gemini_service', 'Gemini could not complete this request (provider HTTP ' + str(status) + ').'))
        return self.Error(message + ' Your recording is still available in this tab.', 502, code)

    def transcribe_whisper(self, audio, duration, directory):
        """Reuse installed offline Whisper, sharing the board worker's CPU lock.

        No account project, Gemini approval, network download or provider secret
        is involved. Keep the parent's pipe open: the child exits when it closes.
        """
        local = self.v.transcription
        if not local or not local.capability().get('ready'):
            raise self.Error('PC Whisper is disabled or not installed. Enable the installed local transcription service on FUPCJ Server.', 503, 'whisper_unavailable')
        if not local._worker_lock.acquire(blocking=False):
            raise self.Error('PC Whisper is processing another recording. Your audio is retained; try PC Whisper again after that job.', 429, 'whisper_busy')
        child = None
        try:
            settings = local.settings
            threads = max(1, min(4, int(settings.get('cpuThreads', 4))))
            (directory / 'speech.wav').write_bytes(audio)
            (directory / 'manifest.json').write_text(json.dumps({'sections': [
                {'file': 'speech.wav', 'format': 'wav', 'start': 0, 'end': duration}]}), encoding='utf-8')
            command = [sys.executable, str(Path(__file__).with_name('transcription.py').resolve()),
                       '--process', str(directory), '--model', settings['modelPath'],
                       '--threads', str(threads), '--ffmpeg', self.app.config['FFMPEG']]
            if settings.get('packagesPath'):
                command += ['--packages', settings['packagesPath']]
            env = os.environ.copy()
            env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', OMP_NUM_THREADS=str(threads),
                       OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS=str(threads))
            flags = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS} if os.name == 'nt' else {}
            child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, env=env, **flags)
            child.wait(timeout=600)
            result = directory / 'result.json'
            if child.returncode or not result.is_file() or result.stat().st_size > MAX_REPLY:
                raise ValueError('invalid-local-result')
            data = json.loads(result.read_text(encoding='utf-8'))
            text = data.get('text')
            if not isinstance(text, str):
                raise ValueError('invalid-local-text')
            # Remove only the worker's line-prefix timestamps, not spoken dates.
            text = re.sub(r'^\[\d{2,}:\d{2}:\d{2}\]\s*', '', text, flags=re.M)
            return plain_transcript({'output_text': text})
        except (OSError, ValueError, subprocess.SubprocessError):
            raise self.Error('PC Whisper could not complete this recording. Check the installed local transcription service.', 502, 'whisper_worker') from None
        finally:
            if child is not None:
                if child.stdin:
                    child.stdin.close()
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill(); child.wait(timeout=5)
            local._worker_lock.release()

    def register_routes(self):
        @self.app.route('/api/venture/dictation', methods=['GET', 'POST'])
        def venture_dictation():
            uid = self.v.account()
            if request.method == 'GET':
                return jsonify(self.capability())
            request.max_content_length = MAX_BYTES + 65536
            if set(request.form) - {'requestId', 'provider'} or 'requestId' not in request.form or any(len(request.form.getlist(k)) != 1 for k in request.form) or set(request.files) != {'audio'} or len(request.files.getlist('audio')) != 1:
                raise self.Error('Dictation accepts one microphone recording, its request ID and an optional provider only.')
            provider = request.form.get('provider', 'gemini')
            if provider not in ('gemini', 'whisper'):
                raise self.Error('Choose Gemini or PC Whisper for dictation.')
            client_id = request.form['requestId']
            if not re.fullmatch(r'[A-Za-z0-9_-]{16,120}', client_id):
                raise self.Error('Invalid dictation request ID.')
            upload = request.files['audio']
            mime = upload.mimetype.lower()
            if mime not in FORMATS:
                raise self.Error('Use a supported microphone audio recording.', 415)
            raw = upload.stream.read(MAX_BYTES+1)
            if not raw or len(raw)>MAX_BYTES:
                raise self.Error('The microphone recording is empty or too large.', 413)
            digest = hashlib.sha256(mime.encode()+b'\0'+raw).hexdigest()
            ident = hashlib.sha256((uid+'\0'+client_id).encode()).hexdigest()
            if not self.slots.acquire(blocking=False):
                raise self.Error('Dictation is busy. Try again in a moment.', 429)
            try:
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    self.prune(db)
                    old = db.execute('SELECT * FROM venture_dictation_requests WHERE id=?', (ident,)).fetchone()
                    if old:
                        if old['digest'] != digest or old['provider'] != provider:
                            raise self.Error('This dictation ID belongs to a different recording.', 409)
                        return jsonify(self.snapshot(old)), 202 if old['status']=='processing' else 200
                    if db.execute("SELECT 1 FROM venture_dictation_requests WHERE uid=? AND status='processing'", (uid,)).fetchone():
                        raise self.Error('Finish the current dictation before starting another.', 409)
                    count = db.execute('SELECT COUNT(*) FROM venture_dictation_requests WHERE uid=? AND created_at>?',
                                       (uid, time.time()-3600)).fetchone()[0]
                    if count >= 60:
                        raise self.Error('Dictation is temporarily rate limited for this account. Try again later.', 429)
                    now = time.time()
                    db.execute('INSERT INTO venture_dictation_requests(id,uid,client_id,digest,status,created_at,updated_at,provider) VALUES(?,?,?,?,?,?,?,?)',
                               (ident,uid,client_id,digest,'processing',now,now,provider))
                try:
                    with tempfile.TemporaryDirectory(prefix='venture-dictation-', dir=self.app.config['DATA_DIR']/'temporary') as name:
                        audio, duration = self.normalize(raw, mime, Path(name))
                        text = self.transcribe_whisper(audio, duration, Path(name)) if provider == 'whisper' else self.transcribe(uid, ident, audio, duration)
                    with self.db() as db:
                        db.execute("UPDATE venture_dictation_requests SET status='completed',text=?,updated_at=? WHERE id=?", (text,time.time(),ident))
                except Exception as error:
                    # Fixed messages only; a provider/credential error cannot leak keys.
                    message = error.message if isinstance(error,self.Error) else 'The dictation connection needs attention on FUPCJ Server. Your recording is still available in this tab.'
                    code = getattr(error, 'code', None) if isinstance(error, self.Error) else 'dictation_configuration'
                    with self.db() as db:
                        db.execute("UPDATE venture_dictation_requests SET status='error',error=?,error_code=?,updated_at=? WHERE id=?", (message,code,time.time(),ident))
                with self.db() as db:
                    row = db.execute('SELECT * FROM venture_dictation_requests WHERE id=?',(ident,)).fetchone()
                return jsonify(self.snapshot(row))
            finally:
                self.slots.release()

        @self.app.get('/api/venture/dictation/<client_id>')
        def venture_dictation_receipt(client_id):
            uid = self.v.account()
            if not re.fullmatch(r'[A-Za-z0-9_-]{16,120}', client_id):
                raise self.Error('This dictation is unavailable.',404)
            with self.db() as db:
                self.prune(db)
                row=db.execute('SELECT * FROM venture_dictation_requests WHERE uid=? AND client_id=?',(uid,client_id)).fetchone()
            if not row:raise self.Error('This dictation is unavailable.',404)
            return jsonify(self.snapshot(row))
