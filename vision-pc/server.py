"""Vision FUPCJ Server. Run through the installer; accepts defined Vision jobs only."""
from __future__ import annotations

import argparse
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import threading
import time
from urllib.parse import urlparse

from flask import Flask, Response, g, has_request_context, jsonify, request, stream_with_context
import requests
from werkzeug.exceptions import HTTPException

from media import normalize_url, process_job, MEDIA_LOCK
from sessions import Sessions, PROJECT_LIMIT, UPLOAD_LIMIT, model_output_token_limit
from transcription import LocalTranscription
from uploaded_media import BODY_LIMIT as VIDEO_BODY_LIMIT, UploadedMedia
from documents import DocumentJobs
from firebase_auth import FirebaseIdentity, InvalidIdentity, IdentityUnavailable
from audit_logs import AuditLogs
from trials import Trials
from gemini_access import GeminiAccess, GeminiUsage
from gemini_credentials import GeminiCredentials, GeminiCredentialUnavailable

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024
OPENAI = 'https://api.openai.com/v1'
WAKE = threading.Event()
STOP = threading.Event()
YOUTUBE_WORKER_LOCK = threading.Lock()
ORIGINS = {'https://jrdn-r.github.io', 'null'}
BASE_INSTRUCTIONS = '''Use the code_interpreter tool directly to open the supplied archive and read MAIN_PROMPT.txt, the module instructions, and the relevant evidence. Follow the module order and conditional paths. Treat the supplied content as the exclusive factual source for the requested task; do not browse or invent unavailable facts. The user's additional message can specify the requested task or output. Apply the package's content and module instructions while respecting higher-priority instructions. Answer naturally about the subject. Do not discuss archives, file layouts, extraction, delivery format, processing, or missing-material inventories unless the user explicitly asks about them. Briefly qualify uncertainty only when it materially affects the answer. Do not add unsolicited diagnoses, advice, risks, or next steps unless asked or necessary for an immediate serious risk. Use readable Markdown. When creating a deliverable, save it in the code interpreter container and provide its downloadable file citation. Do not expose hidden reasoning; concise progress or reasoning summaries are sufficient.'''


class APIError(Exception):
    def __init__(self, message, status=400, code=None):
        self.message, self.status, self.code = message, status, code


def configure(config_path, migrate_gemini=False):
    """Read installation settings without printing credentials."""
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    token = config.get('token', '')
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
        raise ValueError('The installation connection key is missing or invalid. Run setup again.')
    backend = str(config.get('backendUrl', '')).rstrip('/')
    if backend:
        parsed = urlparse(backend)
        if (parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.ts.net')
                or parsed.username or parsed.password or parsed.port or parsed.path or parsed.query or parsed.fragment):
            raise ValueError('backendUrl must be the HTTPS Tailscale address, with no path.')
    port = int(config.get('port', 8765))
    if not 1024 <= port <= 65535:
        raise ValueError('Choose a port from 1024 through 65535.')
    def local_path(key, default):
        path = Path(config.get(key) or default)
        return path if path.is_absolute() else config_path.parent / path
    data_dir = local_path('dataDir', 'data')
    data_dir.mkdir(parents=True, exist_ok=True)
    for child in ('results', 'temporary'):
        (data_dir / child).mkdir(exist_ok=True)
    app.config.update(CONNECTION_TOKEN=token, PORT=port, BACKEND_URL=backend,
                      PUBLIC_ACCESS=config.get('publicAccess') is True,
                      DATA_DIR=data_dir, DATABASE=data_dir / 'vision.sqlite3',
                      FFMPEG=str(local_path('ffmpeg', 'tools/ffmpeg.exe')),
                      DENO=str(local_path('deno', 'tools/deno.exe')))
    firebase = config.get('firebaseAuth') or {}
    if not isinstance(firebase, dict):
        raise ValueError('firebaseAuth must be a configuration object.')
    app.config['FIREBASE_IDENTITY'] = (FirebaseIdentity(firebase.get('projectId'))
                                       if firebase.get('enabled') is True else None)
    diagnostic = config.get('diagnosticToken', '')
    app.config['DIAGNOSTIC_TOKEN'] = diagnostic if isinstance(diagnostic, str) and re.fullmatch(r'[A-Za-z0-9_-]{32,256}', diagnostic) else ''
    os.environ['PATH'] = str(Path(app.config['FFMPEG']).parent) + os.pathsep + str(Path(app.config['DENO']).parent) + os.pathsep + os.environ.get('PATH', '')
    ORIGINS.clear()
    ORIGINS.update({'https://jrdn-r.github.io', 'null'})
    if backend:
        ORIGINS.add(backend)
    initialize_db()
    sessions.initialize()
    app.config['AUDIT_LOGS'] = AuditLogs(local_path('auditLogDir', 'data/audit-logs'), connect_db, app.logger)
    app.config['AUDIT_LOGS'].initialize()
    app.config['GEMINI_CREDENTIALS'] = GeminiCredentials(config_path, data_dir)
    try:
        app.config['GEMINI_CREDENTIALS'].initialize(import_legacy=migrate_gemini)
    except GeminiCredentialUnavailable:
        app.logger.warning('The existing Gemini credential could not be migrated; local processing remains available.')
    app.config['GEMINI_ACCESS'] = GeminiAccess(app, connect_db, wake=transcriptions.wake.set)
    app.config['GEMINI_ACCESS'].initialize()
    app.config['GEMINI_USAGE'] = GeminiUsage(app, connect_db)
    app.config['GEMINI_USAGE'].initialize()
    transcriptions.initialize(config, config_path)
    uploaded_media.initialize()
    documents.initialize(config)
    sessions.venture.documents = documents
    sessions.venture.transcription = transcriptions
    app.config['TRIAL_ENABLED'] = config.get('anonymousTrialEnabled', True) is True
    app.config['TRIALS'] = trials
    trials.initialize()
    return config


def connect_db():
    db = sqlite3.connect(app.config['DATABASE'], timeout=30)
    db.row_factory = sqlite3.Row
    return db


def initialize_db():
    with connect_db() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, client_request_id TEXT UNIQUE, url TEXT NOT NULL,
            status TEXT NOT NULL, phase TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
            title TEXT, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            expires_at REAL NOT NULL, result_path TEXT, attempts INTEGER NOT NULL DEFAULT 0)''')
        db.execute('''CREATE TABLE IF NOT EXISTS responses (
            id TEXT PRIMARY KEY, uid TEXT NOT NULL, status TEXT, updated_at REAL, expires_at REAL)''')
        if 'uid' not in {r[1] for r in db.execute('PRAGMA table_info(jobs)')}:
            db.execute("ALTER TABLE jobs ADD COLUMN uid TEXT NOT NULL DEFAULT 'installation-owner'")
        if 'include_sound_events' not in {r[1] for r in db.execute('PRAGMA table_info(jobs)')}:
            db.execute('ALTER TABLE jobs ADD COLUMN include_sound_events INTEGER NOT NULL DEFAULT 0')
        if 'provider' not in {r[1] for r in db.execute('PRAGMA table_info(jobs)')}:
            db.execute("ALTER TABLE jobs ADD COLUMN provider TEXT NOT NULL DEFAULT 'local'")


def recover_jobs():
    """Only called once at startup, never while another worker is alive."""
    with connect_db() as db:
        db.execute("UPDATE jobs SET status='error',phase='Stopped',error='Processing stopped repeatedly. Start this import again.' WHERE status='processing' AND attempts>=3")
        db.execute("UPDATE jobs SET status='queued',phase='Resuming after restart',progress=0,updated_at=? WHERE status='processing'", (time.time(),))
    # Interrupted source downloads are private temporary data, never project files.
    for directory in (app.config['DATA_DIR'] / 'temporary').glob('vision-youtube-*'):
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)


@app.errorhandler(APIError)
def api_error(error):
    body = {'error': error.message}
    if error.code:
        body['code'] = error.code
    return jsonify(body), error.status


@app.errorhandler(HTTPException)
def http_error(error):
    if error.code == 413:
        message = ('Dictation recording exceeds 12 MB.' if request.path.startswith('/api/venture/dictation')
                   else 'Profile image exceeds 6 MB.' if request.path=='/api/venture/profile'
                   else 'Document uploads exceed 50 MB.' if '/documents' in request.path
                   else 'Video uploads exceed 5 GB. Choose a smaller video.' if '/media' in request.path
                   else 'Server transcription audio uploads exceed 100 MB.' if '/transcriptions' in request.path
                   else 'The project exceeds 150 MB.' if request.method == 'PUT' and request.path.startswith('/api/projects/')
                   else 'Attachments exceed 25 MB. Export fewer or smaller images.')
    else:
        message = error.description
    return jsonify(error=message), error.code


@app.errorhandler(Exception)
def unexpected_error(error):
    # Never log headers or third-party exception payloads; they may contain keys.
    app.logger.error('Request failed: %s', type(error).__name__)
    return jsonify(error='The processing server could not finish this request. Try again.'), 500


@app.before_request
def authorize():
    g.request_started = time.monotonic()
    origin = request.headers.get('Origin')
    if origin and not app.config.get('PUBLIC_ACCESS', False) and origin not in ORIGINS:
        raise APIError('This page is not an allowed Vision origin.', 403)
    if request.method == 'OPTIONS':
        return Response(status=204)
    if request.method == 'GET' and request.path == '/api/status':
        return
    header = request.headers.get('Authorization', '')
    token = header[7:] if header.startswith('Bearer ') else ''
    if request.path == '/api/trial/start' and request.method == 'POST':
        return
    if token.startswith('trial_'):
        trials.authorize(token)
        return
    expected = app.config.get('CONNECTION_TOKEN', '')
    identity = app.config.get('FIREBASE_IDENTITY')
    if expected and hmac.compare_digest(token.encode('utf-8'), expected.encode('utf-8')):
        if identity:
            secret = app.config.get('DIAGNOSTIC_TOKEN', '')
            supplied = request.headers.get('X-Vision-Diagnostic-Token', '')
            local = (request.remote_addr in ('127.0.0.1', '::1') and not request.headers.get('Origin') and
                     not any(name.lower() == 'forwarded' or name.lower().startswith('x-forwarded-') for name in request.headers.keys()))
            diagnostic_project = re.fullmatch(r'/api/projects/vision_check_[0-9a-f]{32}(/transcriptions(?:/[0-9a-f]{24})?)?', request.path)
            diagnostic_route = ((request.path == '/api/health' and request.method == 'GET') or
                                (diagnostic_project and ((not diagnostic_project.group(1) and request.method in ('GET', 'PUT')) or
                                 (diagnostic_project.group(1) == '/transcriptions' and request.method == 'POST') or
                                 (re.fullmatch(r'/transcriptions/[0-9a-f]{24}', diagnostic_project.group(1) or '') and request.method in ('GET', 'DELETE')))))
            if not (secret and local and diagnostic_route and hmac.compare_digest(secret.encode(), supplied.encode())):
                raise APIError('Sign in to use FUPCJ Server.', 401)
        g.uid, g.auth_kind = 'installation-owner', 'private-pc'
        return
    if identity and token:
        try:
            claims = identity.verify(token)
        except IdentityUnavailable:
            raise APIError('Account sign-in verification is temporarily unavailable on FUPCJ Server. Try again.', 503)
        except InvalidIdentity:
            raise APIError('Your sign-in has expired or is invalid. Sign in again.', 401)
        g.uid, g.auth_kind = 'firebase:' + claims['sub'], 'firebase-google'
        g.identity_claims = claims
        app.config['AUDIT_LOGS'].identity(g.uid, claims)
        return
    raise APIError('Sign in or connect this device to your Vision processing server first.', 401)


@app.after_request
def cors(response):
    if getattr(g, 'auth_kind', None) == 'trial':
        ident, deadline = g.trial['id'], g.trial['expires_at']
        released = [False]
        def release_trial():
            if not released[0]:
                released[0] = True
                with trials.lock:
                    count = trials.inflight.get(ident, 1) - 1
                    if count > 0:
                        trials.inflight[ident] = count
                    else:
                        trials.inflight.pop(ident, None)
        if time.time() >= deadline:
            response.close()
            response = jsonify(error='Your five-minute trial has ended. Sign in.', code='trial-expired')
            response.status_code = 403
        elif response.is_streamed:
            original = response.response
            def trial_chunks():
                try:
                    for chunk in original:
                        if time.time() >= deadline or trials.expired('trial-' + ident):
                            break
                        yield chunk
                finally:
                    if hasattr(original, 'close'):
                        original.close()
                    release_trial()
            response.response = trial_chunks()
        response.call_on_close(release_trial)
    if getattr(g, 'auth_kind', None) == 'firebase-google' and request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        app.config['AUDIT_LOGS'].request_event(g.uid, request, response.status_code,
                                              time.monotonic() - g.request_started)
    origin = request.headers.get('Origin')
    public_access = app.config.get('PUBLIC_ACCESS', False)
    if public_access or origin in ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = '*' if public_access else origin
        response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type, X-OpenAI-Key, X-Vision-Project-Key'
        response.headers['Access-Control-Allow-Methods'] = 'GET, PUT, POST, PATCH, DELETE, OPTIONS'
        response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition, Content-Type'
        if request.headers.get('Access-Control-Request-Private-Network') == 'true':
            response.headers['Access-Control-Allow-Private-Network'] = 'true'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.get('/api/status')
def public_status():
    return jsonify(service='vision', mode='private-pc', googleSignInRequired=bool(app.config.get('FIREBASE_IDENTITY')))


@app.get('/api/health')
def health():
    with connect_db() as db:
        queued = db.execute("SELECT COUNT(*) FROM jobs WHERE uid=? AND status IN ('queued','processing')", (g.uid,)).fetchone()[0]
    local = transcriptions.capability()
    sounds = transcriptions.sound_capability()
    video = uploaded_media.capability()
    document = documents.capability()
    gemini = {'available': True, 'approvalRequired': True,
              'speechModel': 'gemini-3.5-transcribe', 'soundModel': 'gemini-3.8-flash'}
    return jsonify(ok=True, mode='private-pc', service='vision-pc', version='1.0', authRequired=True, queued=queued,
                   serverName='FUPCJ Server',
                   publicAccess=app.config.get('PUBLIC_ACCESS', False),
                   firebaseAuth={'enabled': bool(app.config.get('FIREBASE_IDENTITY')),
                                 'projectId': app.config['FIREBASE_IDENTITY'].project_id if app.config.get('FIREBASE_IDENTITY') else None},
                   maxArchiveBytes=UPLOAD_LIMIT, maxProjectBytes=PROJECT_LIMIT,
                   localTranscription=local, localSoundEvents=sounds, videoMedia=video, documentProcessing=document,
                   geminiTranscription=gemini,
                   ventureDictation=sessions.venture.dictation.capability(),
                   capabilities={'ventureV2': True, 'ventureV1': True, 'ventureMaxUploadBytes': 128*1024*1024, 'runParametersV1': True, 'persistentProjects': True, 'persistentRuns': True, 'projectRevision': True, 'accountProjects': bool(app.config.get('FIREBASE_IDENTITY')), 'localTranscription': local['ready'], 'geminiTranscription': True, 'soundEvents': sounds['ready'], 'uploadedMedia': video['ready'], 'documentProcessing': document['ready'], 'temporarySessions': trials.enabled()})


def get_job(job_id):
    if not re.fullmatch(r'[0-9a-f]{24}', job_id):
        raise APIError('Invalid job identifier.')
    with connect_db() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if row is None or (has_request_context() and row['uid'] != g.uid):
        raise APIError('This import is unavailable.', 404)
    value = dict(row)
    if value['expires_at'] < time.time() and value['status'] not in ('queued', 'processing'):
        raise APIError('This import has expired. Start it again.', 410)
    return value


@app.post('/api/youtube')
def youtube():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise APIError('Send a YouTube link.')
    try:
        url = normalize_url(payload.get('url', ''))
    except (ValueError, AttributeError):
        raise APIError('Paste a direct YouTube video, Shorts, or youtu.be link.')
    client_id = payload.get('clientRequestId')
    if client_id is not None and (not isinstance(client_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', client_id)):
        raise APIError('Invalid request identifier.')
    include_sound_events = payload.get('includeSoundEvents', False)
    provider = payload.get('provider', 'local')
    if provider not in ('local', 'gemini'):
        raise APIError('Choose a supported transcription provider.')
    if provider == 'gemini' and g.auth_kind != 'firebase-google':
        raise APIError('Sign in with Google to request Gemini processing.', 403)
    if not isinstance(include_sound_events, bool):
        raise APIError('Choose whether to include sound effects.')
    # Existing installation receipts retain their IDs. New accounts cannot collide
    # with those receipts or infer another account's video URL/status.
    if client_id and g.auth_kind in ('firebase-google', 'trial'):
        import hashlib
        client_id = 'account:' + hashlib.sha256((g.uid + '\0' + client_id).encode()).hexdigest()
    with connect_db() as db:
        db.execute('BEGIN IMMEDIATE')
        if client_id:
            old = db.execute('SELECT id,status,url,include_sound_events,provider FROM jobs WHERE client_request_id=? AND uid=?', (client_id, g.uid)).fetchone()
            if old:
                if old['url'] != url or bool(old['include_sound_events']) != include_sound_events or old['provider'] != provider:
                    raise APIError('This request identifier belongs to another video or sound setting.', 409)
                return jsonify(id=old['id'], status=old['status']), 202
        if include_sound_events and not transcriptions.sound_capability()['ready']:
            raise APIError('Sound detection is not ready on FUPCJ Server. Run Setup-Vision-PC.ps1 -Action InstallSoundEvents, or turn off Include sound effects.', 503)
        pending = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','processing')").fetchone()[0]
        if pending >= 25:
            raise APIError('The server queue is full. Wait for an import to finish.', 429)
        job_id, now = secrets.token_hex(12), time.time()
        db.execute('''INSERT INTO jobs(id,client_request_id,url,status,phase,created_at,updated_at,expires_at,uid,include_sound_events,provider)
            VALUES(?,?,?,'queued','Waiting',?,?,?,?,?,?)''', (job_id, client_id, url, now, now, now + 86400, g.uid, int(include_sound_events), provider))
        if provider == 'gemini':
            app.config['GEMINI_ACCESS'].request_access(g.uid, db=db)
    WAKE.set()
    return jsonify(id=job_id, status='queued'), 202


def prune_expired():
    with connect_db() as db:
        expired = db.execute("SELECT id,result_path FROM jobs WHERE expires_at<? AND status NOT IN ('queued','processing')", (time.time(),)).fetchall()
        for row in expired:
            if row['result_path']:
                Path(row['result_path']).unlink(missing_ok=True)
            db.execute('DELETE FROM jobs WHERE id=?', (row['id'],))
        db.execute('DELETE FROM responses WHERE expires_at<?', (time.time(),))
        sessions.venture.dictation.prune(db)


def process_next_job():
    with YOUTUBE_WORKER_LOCK:
        return _process_next_job()


def _process_next_job():
    with connect_db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if row is None:
            return False
        value = dict(row)
        db.execute("UPDATE jobs SET status='processing',attempts=attempts+1,updated_at=? WHERE id=?", (time.time(), value['id']))
    trial_id = 'trial-' + value['uid'][6:] if value['uid'].startswith('trial:') else ''
    deadline = None
    if trial_id:
        with connect_db() as db:
            lease = db.execute('SELECT expires_at FROM trial_visits WHERE id=?', (value['uid'][6:],)).fetchone()
        deadline = lease['expires_at'] if lease else 0
    last_write = [0.0]
    def update(job_id, **fields):
        if trial_id and trials.expired(trial_id):
            raise RuntimeError('trial-expired')
        result = fields.pop('result', None)
        if result is not None:
            path = app.config['DATA_DIR'] / 'results' / (job_id + '.json')
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(result)
            os.replace(temporary, path)
            fields['result_path'] = str(path)
        terminal = fields.get('status') in ('complete', 'error')
        if not terminal and time.monotonic() - last_write[0] < 0.5:
            return
        last_write[0] = time.monotonic()
        fields['updated_at'] = time.time()
        if terminal:
            fields['expires_at'] = time.time() + 86400
        allowed = {'status', 'phase', 'progress', 'title', 'error', 'result_path', 'updated_at', 'expires_at'}
        fields = {key: val for key, val in fields.items() if key in allowed}
        with connect_db() as db:
            db.execute('UPDATE jobs SET ' + ','.join(key + '=?' for key in fields) + ' WHERE id=?', [*fields.values(), job_id])
    processing_started = time.monotonic()
    try:
        with MEDIA_LOCK:
            if trial_id and trials.expired(trial_id):
                with connect_db() as db:
                    db.execute("UPDATE jobs SET status='error',phase='Trial ended' WHERE id=?", (value['id'],))
                return True
            process_job(value['id'], value['url'], app.config['FFMPEG'], update,
                        deno=app.config['DENO'], temp_root=str(app.config['DATA_DIR'] / 'temporary'),
                        include_sound_events=bool(value['include_sound_events']),
                        provider=value['provider'],
                        **({'deadline': deadline} if deadline is not None else {}))
    except Exception:
        if trial_id and trials.expired(trial_id):
            with connect_db() as db:
                db.execute("UPDATE jobs SET status='error',phase='Trial ended' WHERE id=?", (value['id'],))
        else:
            update(value['id'], status='error', phase='Stopped', error='The import stopped unexpectedly. Start it again.')
    finally:
        with connect_db() as db:
            final = db.execute('SELECT status,result_path FROM jobs WHERE id=?', (value['id'],)).fetchone()
        size = Path(final['result_path']).stat().st_size if final and final['result_path'] and Path(final['result_path']).exists() else 0
        app.config['AUDIT_LOGS'].event(value['uid'], 'processing_finished', jobType='youtube',
            outcome=final['status'] if final else 'removed', processingWallSeconds=time.monotonic()-processing_started,
            outputBytes=size)
    return True


def worker_loop():
    while not STOP.is_set():
        try:
            prune_expired()
            if process_next_job():
                continue
        except Exception:
            app.logger.error('Processing worker encountered a temporary local error.')
        WAKE.wait(30)
        WAKE.clear()


@app.get('/api/jobs/<job_id>')
def job_status(job_id):
    value = get_job(job_id)
    return jsonify({key: value[key] for key in ('id', 'status', 'phase', 'progress', 'title', 'error') if value.get(key) is not None})


@app.get('/api/jobs/<job_id>/result')
def job_result(job_id):
    value = get_job(job_id)
    if value['status'] != 'complete':
        raise APIError('This import is not finished yet.', 409)
    if not value['result_path'] or not Path(value['result_path']).is_file():
        raise APIError('This result has already been received or has expired.', 410)
    def chunks():
        with open(value['result_path'], 'rb') as source:
            while chunk := source.read(65536):
                yield chunk
    return Response(stream_with_context(chunks()), content_type='application/json')


@app.delete('/api/jobs/<job_id>')
def delete_job(job_id):
    value = get_job(job_id)
    if value['status'] not in ('complete', 'error'):
        raise APIError('Wait for this import to finish.', 409)
    if value['result_path']:
        Path(value['result_path']).unlink(missing_ok=True)
    # Keep a small receipt for a day so a retry never starts the same job twice.
    with connect_db() as db:
        db.execute('UPDATE jobs SET result_path=NULL WHERE id=?', (job_id,))
    return jsonify(deleted=True)


def own_document(collection, document_id):
    if collection != 'visionResponses' or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', document_id):
        raise APIError('Invalid session identifier.')
    with connect_db() as db:
        row = db.execute('SELECT * FROM responses WHERE id=? AND uid=?', (document_id, g.uid)).fetchone()
    if row is None:
        raise APIError('This session is unavailable on this processing server.', 404)
    if row['expires_at'] < time.time():
        raise APIError('This session has expired.', 410)
    return None, dict(row)


def use_quota(_kind):
    # Billing credentials are supplied for each OpenAI request by the private owner.
    return


def openai_headers():
    key = request.headers.get('X-OpenAI-Key', '').strip()
    if not key or len(key) > 512 or '\n' in key or '\r' in key:
        raise APIError('Enter your OpenAI API key.')
    return {'Authorization': 'Bearer ' + key}

def upstream(method, path, **kwargs):
    try:
        response = requests.request(method, OPENAI + path, headers=openai_headers(), timeout=(15, 180), **kwargs)
    except requests.RequestException:
        raise APIError('OpenAI could not be reached. Try reconnecting.', 502)
    if response.status_code >= 400:
        try:
            message = response.json().get('error', {}).get('message', 'OpenAI rejected the request.')
        except ValueError:
            message = 'OpenAI rejected the request.'
        key = request.headers.get('X-OpenAI-Key', '')
        message = str(message).replace(key, '[redacted]') if key else str(message)
        response.close()
        raise APIError(message[:700], response.status_code)
    return response

def response_payload(file_id, options):
    model = str(options.get('model') or 'gpt-6-astra')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', model):
        raise APIError('Choose a valid OpenAI model.')
    message = str(options.get('message', options.get('prompt', '')))[:50000]
    try:
        limit = int(options.get('max_output_tokens', options.get('maxOutputTokens', 16000)))
        if not 512 <= limit <= model_output_token_limit(model):
            raise ValueError('output limit out of range')
    except (ValueError, TypeError):
        raise APIError('Choose a valid output length.')
    payload = {'model': model, 'instructions': BASE_INSTRUCTIONS,
            'input': message or 'Carry out the task specified in the attached project instructions.',
            'tools': [{'type': 'code_interpreter', 'container': {'type': 'auto', 'memory_limit': '1g', 'file_ids': [file_id]}}],
            'tool_choice': 'required', 'background': True, 'stream': True, 'store': True, 'max_output_tokens': limit}
    if re.match(r'^(gpt-[56](?:[.-]|$)|o[134](?:[.-]|$))', model):
        payload['reasoning'] = {'summary': 'auto'}
    return payload

def track_event(event, uid):
    response = event.get('response') or {}
    response_id = response.get('id')
    if not response_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', response_id):
        return
    if event.get('type') in ('response.created', 'response.completed', 'response.failed', 'response.cancelled', 'response.incomplete'):
        with connect_db() as db:
            db.execute('INSERT INTO responses(id,uid,status,updated_at,expires_at) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at,expires_at=excluded.expires_at',
                       (response_id, uid, response.get('status', ''), time.time(), time.time()+7*86400))

def stream_openai(response, uid):
    def events():
        try:
            for line in response.iter_lines(chunk_size=1):
                if line.startswith(b'data: '):
                    try:
                        event = json.loads(line[6:])
                        track_event(event, uid)
                    except (ValueError, TypeError):
                        pass
                yield line + b'\n'
        except requests.RequestException:
            yield b'event: vision.connection_lost\ndata: {"type":"vision.connection_lost"}\n\n'
        finally:
            response.close()
    return Response(stream_with_context(events()), content_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

@app.post('/api/openai/run')
def openai_run():
    archive = request.files.get('file') or request.files.get('archive')
    if not archive:
        raise APIError('Attach the project archive.')
    signature = archive.stream.read(4)
    archive.stream.seek(0)
    if signature not in (b'PK\x03\x04', b'PK\x05\x06'):
        raise APIError('The project must be a ZIP archive.')
    try:
        options = json.loads(request.form.get('options', '{}'))
        if not isinstance(options, dict):
            raise ValueError()
    except ValueError:
        raise APIError('Invalid session options.')
    payload = response_payload('pending', options)
    use_quota('openai')
    uploaded = upstream('POST', '/files', data={'purpose': 'user_data', 'expires_after[anchor]': 'created_at', 'expires_after[seconds]': '86400'},
                        files={'file': ('vision-project.zip', archive.stream, 'application/zip')})
    file_id = uploaded.json()['id']
    uploaded.close()
    payload['tools'][0]['container']['file_ids'] = [file_id]
    try:
        response = upstream('POST', '/responses', json=payload, stream=True)
    except APIError:
        try:
            upstream('DELETE', '/files/' + file_id).close()
        except APIError:
            pass
        raise
    return stream_openai(response, g.uid)

@app.get('/api/openai/responses/<response_id>')
@app.get('/api/openai/responses/<response_id>/stream')
def openai_response(response_id):
    own_document('visionResponses', response_id)
    streaming = request.path.endswith('/stream') or request.args.get('stream') == 'true'
    params = {}
    if streaming:
        params['stream'] = 'true'
        cursor = request.args.get('after', request.args.get('starting_after'))
        if cursor is not None:
            if not cursor.isdigit():
                raise APIError('Invalid stream cursor.')
            params['starting_after'] = cursor
    response = upstream('GET', '/responses/' + response_id, params=params, stream=streaming)
    if streaming:
        return stream_openai(response, g.uid)
    value = response.json()
    response.close()
    return jsonify(value)

@app.post('/api/openai/responses/<response_id>/cancel')
def openai_cancel(response_id):
    own_document('visionResponses', response_id)
    response = upstream('POST', '/responses/' + response_id + '/cancel')
    value = response.json()
    response.close()
    return jsonify(value)

@app.get('/api/openai/containers/<container_id>/files/<file_id>/content')
@app.get('/api/openai/files/<container_id>/<file_id>')
def openai_artifact(container_id, file_id):
    response_id = request.args.get('response_id', '')
    own_document('visionResponses', response_id)
    for value in (container_id, file_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', value):
            raise APIError('Invalid file identifier.')
    check = upstream('GET', '/responses/' + response_id)
    value = check.json()
    check.close()
    allowed = any(item.get('type') == 'code_interpreter_call' and item.get('container_id') == container_id for item in value.get('output', []))
    if not allowed:
        raise APIError('This file is not part of the selected session.', 403)
    response = upstream('GET', f'/containers/{container_id}/files/{file_id}/content', stream=True)
    def chunks():
        try:
            yield from response.iter_content(65536)
        finally:
            response.close()
    return Response(stream_with_context(chunks()), content_type=response.headers.get('Content-Type', 'application/octet-stream'))


sessions = Sessions(app, connect_db, APIError, BASE_INSTRUCTIONS)
transcriptions = LocalTranscription(app, connect_db, APIError, sessions)
uploaded_media = UploadedMedia(app, connect_db, APIError, sessions)
documents = DocumentJobs(app, connect_db, APIError, sessions)
trials = Trials(app, connect_db, APIError, sessions, transcriptions, uploaded_media, YOUTUBE_WORKER_LOCK, documents=documents)

def main():
    parser = argparse.ArgumentParser(description='Vision private FUPCJ Server')
    parser.add_argument('--config', default=str(Path(__file__).with_name('config.json')))
    parser.add_argument('--check', action='store_true', help='Check configuration and installed components, then exit')
    parser.add_argument('--health', action='store_true', help='Check the already-running local processor, then exit')
    args = parser.parse_args()
    try:
        configure(args.config, migrate_gemini=not (args.check or args.health))
        if args.health:
            response = requests.get('http://127.0.0.1:%s/api/health' % app.config['PORT'],
                                    headers={'Authorization': 'Bearer ' + app.config['CONNECTION_TOKEN'],
                                             'X-Vision-Diagnostic-Token': app.config['DIAGNOSTIC_TOKEN']}, timeout=10)
            response.raise_for_status()
            print(json.dumps(response.json()))
            return 0
        missing = [name for name in ('waitress', 'yt_dlp', 'PIL') if importlib.util.find_spec(name) is None]
        missing += [name for name in ('FFMPEG', 'DENO') if not Path(app.config[name]).is_file()]
        if not Path(app.config['FFMPEG']).with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe').is_file():
            missing.append('ffprobe')
        if missing:
            print('Setup is incomplete: ' + ', '.join(missing) + '. Run the installer again.')
            return 1
        if args.check:
            print('FUPCJ Server configuration and processing components are ready.')
            return 0
        import logging
        from logging.handlers import RotatingFileHandler
        handler = RotatingFileHandler(app.config['DATA_DIR'] / 'server.log', maxBytes=1024 * 1024, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        app.logger.addHandler(handler)
        app.logger.setLevel(logging.INFO)
        from waitress import serve
        trials.sweep()
        recover_jobs()
        trials.start()
        worker = threading.Thread(target=worker_loop, name='vision-media', daemon=True)
        worker.start()
        sessions.start()
        transcriptions.start()
        uploaded_media.start()
        documents.start()
        app.logger.info('Vision PC processor started on loopback port %s.', app.config['PORT'])
        try:
            serve(app, host='127.0.0.1', port=app.config['PORT'], threads=8,
                  max_request_body_size=max(PROJECT_LIMIT, VIDEO_BODY_LIMIT), channel_timeout=300,
                  expose_tracebacks=False, trusted_proxy='127.0.0.1',
                  trusted_proxy_count=1, trusted_proxy_headers={'x-forwarded-for', 'x-forwarded-proto'},
                  clear_untrusted_proxy_headers=True)
        finally:
            trials.stop.set()
            STOP.set()
            WAKE.set()
            sessions.stop.set()
            sessions.wake.set()
            transcriptions.stop.set()
            transcriptions.wake.set()
            documents.stop.set()
            documents.wake.set()
            uploaded_media.stop.set()
            uploaded_media.wake.set()
        return 0
    except Exception as error:
        # Values and payloads are intentionally excluded from setup diagnostics.
        print('FUPCJ Server could not start (' + type(error).__name__ + '). Check the installation or run setup again.')
        return 1


# Optional private activity dashboard, enabled on the PC by Enable-Vision-Activity.ps1.
if Path(__file__).with_name('activity_dashboard.py').is_file():
    from activity_dashboard import register as register_activity_dashboard
    register_activity_dashboard(app, connect_db)


if __name__ == '__main__':
    raise SystemExit(main())
