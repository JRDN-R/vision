"""Durable project sessions. Browser connections never own the OpenAI worker."""
from __future__ import annotations

import ctypes
from datetime import datetime, timezone
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
import time

from flask import Response, g, jsonify, request, send_file, stream_with_context
import requests

OPENAI = 'https://api.openai.com/v1'
PROJECT_LIMIT = 150 * 1024 * 1024
UPLOAD_LIMIT = 25 * 1024 * 1024
ARTIFACT_LIMIT = 100 * 1024 * 1024
ACTIVE = ('queued', 'preparing', 'submitting', 'in_progress', 'saving')
TERMINAL = ('completed', 'incomplete', 'cancelled', 'error')
ID = re.compile(r'^[A-Za-z0-9_-]{1,200}$')


class UpstreamFailure(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def protect_secret(value):
    """DPAPI user scope: the installed SYSTEM task can recover its own secrets."""
    return _dpapi(value.encode('utf-8'), False)


def unprotect_secret(value):
    return _dpapi(bytes(value), True).decode('utf-8')


def _dpapi(value, decrypt):
    if os.name != 'nt':
        raise RuntimeError('Durable API sessions require Windows DPAPI on the processor.')
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buf = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    crypt.CryptProtectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_wchar_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
    crypt.CryptProtectData.restype = ctypes.c_int
    crypt.CryptUnprotectData.restype = ctypes.c_int
    if decrypt:
        result = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        result = crypt.CryptProtectData(ctypes.byref(source), 'Vision PC run', None, None, None, 1, ctypes.byref(target))
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(target.data)


def safe_name(value):
    return re.sub(r'[\x00-\x1f\x7f]', '', str(value).replace('\\', '/').rsplit('/', 1)[-1])[:180] or 'file'


class Sessions:
    def __init__(self, app, connect_db, api_error, instructions):
        self.app, self.db, self.Error, self.instructions = app, connect_db, api_error, instructions
        self.wake, self.stop = threading.Event(), threading.Event()
        self.register_routes()

    def initialize(self):
        (self.app.config['DATA_DIR'] / 'projects').mkdir(exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, key_hash TEXT NOT NULL, project_json TEXT NOT NULL,
                    revision INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS project_runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, client_id TEXT NOT NULL,
                    status TEXT NOT NULL, phase TEXT NOT NULL, message TEXT NOT NULL,
                    model TEXT NOT NULL, max_tokens INTEGER NOT NULL, previous_id TEXT,
                    response_id TEXT, container_id TEXT, text TEXT NOT NULL DEFAULT '',
                    error TEXT, key_cipher BLOB, response_json TEXT, inputs_json TEXT NOT NULL,
                    project_prompt TEXT NOT NULL DEFAULT '',
                    sequence INTEGER NOT NULL DEFAULT 0, upstream_sequence INTEGER NOT NULL DEFAULT -1,
                    cancel_requested INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    UNIQUE(project_id, client_id));
                CREATE TABLE IF NOT EXISTS run_events (
                    run_id TEXT NOT NULL, sequence INTEGER NOT NULL, snapshot_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, sequence));
                CREATE TABLE IF NOT EXISTS run_artifacts (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, container_id TEXT NOT NULL,
                    file_id TEXT NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL,
                    size INTEGER NOT NULL DEFAULT 0, path TEXT, error TEXT,
                    UNIQUE(run_id,container_id,file_id));
                CREATE TABLE IF NOT EXISTS account_credentials (
                    uid TEXT PRIMARY KEY, openai_key_cipher BLOB NOT NULL,
                    updated_at REAL NOT NULL);
            ''')
            columns = {r[1] for r in db.execute('PRAGMA table_info(project_runs)')}
            if 'project_prompt' not in columns:
                db.execute("ALTER TABLE project_runs ADD COLUMN project_prompt TEXT NOT NULL DEFAULT ''")
            columns = {r[1] for r in db.execute('PRAGMA table_info(projects)')}
            if 'owner_uid' not in columns:
                db.execute('ALTER TABLE projects ADD COLUMN owner_uid TEXT')
            if 'title' not in columns:
                db.execute("ALTER TABLE projects ADD COLUMN title TEXT NOT NULL DEFAULT 'Untitled project'")
            db.execute('CREATE INDEX IF NOT EXISTS projects_owner_updated ON projects(owner_uid,updated_at)')

    def project(self, project_id, create=False, db=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,120}', project_id):
            raise self.Error('Invalid project identifier.')
        key = request.headers.get('X-Vision-Project-Key', '')
        valid_key = bool(re.fullmatch(r'[A-Za-z0-9_-]{32,256}', key))
        digest = hashlib.sha256(key.encode()).hexdigest() if valid_key else None
        if db is None:
            with self.db() as conn:
                row = conn.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        else:
            row = db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        account = g.auth_kind == 'firebase-google'
        if row is not None and row['owner_uid']:
            if not account or row['owner_uid'] != g.uid:
                raise self.Error('This project is unavailable for this account.', 404)
            # A project key is a legacy migration credential, never an override
            # for account ownership. The same account can reopen on a new device.
            digest = row['key_hash']
        elif row is not None and account:
            raise self.Error('Add this existing project to your Google account before syncing it.',
                             409, 'project-claim-required')
        elif not account:
            if not valid_key:
                raise self.Error('Open the saved Vision project to unlock this project.', 403)
            if row is not None and not hmac.compare_digest(row['key_hash'], digest):
                raise self.Error('This project key does not match the saved project.', 403)
        if row is None and not create:
            raise self.Error('This project has not been saved to FUPCJ Server yet.', 404)
        return row, digest or hashlib.sha256(secrets.token_bytes(32)).hexdigest()

    def row(self, run_id, project_id=None):
        if not ID.fullmatch(run_id):
            raise self.Error('Invalid run identifier.')
        with self.db() as db:
            row = db.execute('SELECT * FROM project_runs WHERE id=?', (run_id,)).fetchone()
        if row is None or (project_id is not None and row['project_id'] != project_id):
            raise self.Error('This run is not part of the project.', 404)
        return dict(row)

    def snapshot(self, value):
        if isinstance(value, str):
            value = self.row(value)
        with self.db() as db:
            files = db.execute('SELECT * FROM run_artifacts WHERE run_id=? ORDER BY rowid', (value['id'],)).fetchall()
        status = value['status']
        return dict(runId=value['id'], projectId=value['project_id'], clientRequestId=value['client_id'],
                    status=status if status in TERMINAL or status == 'queued' else 'in_progress', phase=value['phase'],
                    text=value['text'], message=value['message'], model=value['model'], projectPrompt=value['project_prompt'],
                    responseId=value['response_id'], previousRunId=value['previous_id'], sequence=value['sequence'],
                    error=value['error'], createdAt=datetime.fromtimestamp(value['created_at'], timezone.utc).isoformat(),
                    updatedAt=datetime.fromtimestamp(value['updated_at'], timezone.utc).isoformat(),
                    attachments=[{k: v[k] for k in ('name', 'mime', 'size')} for v in json.loads(value['inputs_json'])],
                    artifacts=[dict(id=f['id'], name=f['name'], mime=f['mime'], size=f['size'],
                                    ready=bool(f['path']), error=f['error']) for f in files])

    def update(self, run_id, **fields):
        fields['updated_at'] = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous_status = db.execute('SELECT status FROM project_runs WHERE id=?', (run_id,)).fetchone()
            db.execute('UPDATE project_runs SET ' + ','.join(k+'=?' for k in fields) + ',sequence=sequence+1 WHERE id=?', [*fields.values(), run_id])
        snap = self.snapshot(run_id)
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO run_events VALUES(?,?,?)', (run_id, snap['sequence'], json.dumps(snap)))
            # Snapshots are self-contained, so bounded replay preserves every current fact.
            db.execute('DELETE FROM run_events WHERE run_id=? AND sequence<?', (run_id, snap['sequence'] - 150))
        if fields.get('status') in TERMINAL and previous_status and previous_status['status'] not in TERMINAL:
            row = self.row(run_id)
            self.app.config['AUDIT_LOGS'].project_event(row['project_id'], 'run_finished', jobType='openai',
                outcome=fields['status'], runElapsedSeconds=time.time()-row['created_at'],
                uploadedBytes=sum(v.get('size', 0) for v in json.loads(row['inputs_json'])),
                outputBytes=sum(v['size'] for v in snap['artifacts']))
        return snap

    def register_routes(self):
        app = self.app
        @app.route('/api/account/openai-key', methods=['GET', 'PUT', 'DELETE'])
        def account_openai_key():
            if g.auth_kind != 'firebase-google':
                raise self.Error('Sign in with Google to use your saved API key.', 403)
            if request.method == 'GET':
                with self.db() as db:
                    row = db.execute('SELECT openai_key_cipher FROM account_credentials WHERE uid=?', (g.uid,)).fetchone()
                if row is None:
                    return jsonify(saved=False, apiKey='')
                try:
                    key = unprotect_secret(row['openai_key_cipher'])
                except Exception:
                    raise self.Error('FUPCJ Server could not unlock your saved API key. Check the processor installation.', 503)
                return jsonify(saved=True, apiKey=key)
            if request.method == 'DELETE':
                with self.db() as db:
                    db.execute('DELETE FROM account_credentials WHERE uid=?', (g.uid,))
                return jsonify(saved=False)
            request.max_content_length = 8192
            body = request.get_json(silent=True)
            key = body.get('apiKey') if isinstance(body, dict) else None
            if not isinstance(key, str) or not key.strip() or len(key) > 512 or re.search(r'\s', key.strip()):
                raise self.Error('Enter a valid OpenAI API key.')
            key = key.strip()
            try:
                cipher = protect_secret(key)
            except Exception:
                raise self.Error('FUPCJ Server could not protect your API key. Restart the installed Windows processor and try again.', 503)
            with self.db() as db:
                db.execute('''INSERT INTO account_credentials(uid,openai_key_cipher,updated_at) VALUES(?,?,?)
                    ON CONFLICT(uid) DO UPDATE SET openai_key_cipher=excluded.openai_key_cipher,updated_at=excluded.updated_at''',
                    (g.uid, cipher, time.time()))
            return jsonify(saved=True)

        @app.get('/api/projects')
        def account_projects():
            if g.auth_kind != 'firebase-google':
                # Never expose the installation's legacy projects through its
                # shared connection token. Discovery requires verified identity.
                raise self.Error('Sign in with Google to open your saved projects.', 404)
            with self.db() as db:
                rows = db.execute('SELECT id,title,revision,updated_at FROM projects WHERE owner_uid=? ORDER BY updated_at DESC,id',
                                  (g.uid,)).fetchall()
            return jsonify(projects=[dict(id=r['id'], title=r['title'], revision=r['revision'],
                                          updatedAt=r['updated_at']) for r in rows])

        @app.post('/api/projects/<project_id>/claim')
        def claim_project(project_id):
            if g.auth_kind != 'firebase-google':
                raise self.Error('Sign in with Google to add this project to your account.', 403)
            if not re.fullmatch(r'[A-Za-z0-9_-]{16,120}', project_id):
                raise self.Error('Invalid project identifier.')
            key = request.headers.get('X-Vision-Project-Key', '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', key):
                raise self.Error('Open the original saved project to add it to your account.', 403)
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
                if row is None or (row['owner_uid'] and row['owner_uid'] != g.uid):
                    raise self.Error('This project is unavailable for this account.', 404)
                if not hmac.compare_digest(row['key_hash'], hashlib.sha256(key.encode()).hexdigest()):
                    raise self.Error('This project key does not match the saved project.', 403)
                document = json.loads(row['project_json'])
                title = str(document.get('title') or document.get('name') or 'Untitled project')[:200]
                db.execute('UPDATE projects SET owner_uid=?,title=? WHERE id=?', (g.uid, title, project_id))
            return jsonify(claimed=True, revision=row['revision'], updatedAt=row['updated_at'])

        @app.route('/api/projects/<project_id>', methods=['GET', 'PUT'])
        def persistent_project(project_id):
            if request.method == 'GET':
                value, _ = self.project(project_id)
                return jsonify(project=json.loads(value['project_json']), revision=value['revision'], updatedAt=value['updated_at'])
            request.max_content_length = PROJECT_LIMIT
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or not isinstance(body.get('project'), dict):
                raise self.Error('Send a saved Vision project.')
            revision = body.get('revision', 0)
            if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
                raise self.Error('Invalid project revision.')
            # Clients must never include their OpenAI billing credential in a project.
            saved = json.dumps(body['project'], separators=(',', ':'))
            if re.search(r'"(?:apiKey|openaiKey|openaiApiKey|X-OpenAI-Key)"\s*:\s*"[^"\s]+', saved, re.I):
                raise self.Error('Remove the API key before saving this project.')
            now = time.time()
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                old, digest = self.project(project_id, create=True, db=db)
                expected = old['revision'] if old else 0
                if revision != expected:
                    return jsonify(error='This project changed on another device. Load the saved version before saving again.', revision=expected), 409
                title = str(body['project'].get('title') or body['project'].get('name') or 'Untitled project')[:200]
                owner = g.uid if g.auth_kind == 'firebase-google' else None
                db.execute('''INSERT INTO projects(id,key_hash,project_json,revision,created_at,updated_at,owner_uid,title)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    project_json=excluded.project_json,revision=excluded.revision,updated_at=excluded.updated_at,title=excluded.title''',
                    (project_id, digest, saved, revision+1, now, now, owner, title))
            return jsonify(revision=revision+1, updatedAt=now)

        @app.route('/api/projects/<project_id>/runs', methods=['GET', 'POST'])
        def persistent_runs(project_id):
            self.project(project_id)
            if request.method == 'GET':
                with self.db() as db:
                    rows = db.execute('SELECT * FROM project_runs WHERE project_id=? ORDER BY created_at,id', (project_id,)).fetchall()
                return jsonify(runs=[self.snapshot(dict(r)) for r in rows])
            request.max_content_length = UPLOAD_LIMIT
            try:
                options = json.loads(request.form.get('options', '{}'))
                if not isinstance(options, dict):
                    raise ValueError()
            except ValueError:
                raise self.Error('Invalid run options.')
            client_id = options.get('clientRequestId', '')
            if not isinstance(client_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', client_id):
                raise self.Error('A valid clientRequestId is required.')
            with self.db() as db:
                old = db.execute('SELECT * FROM project_runs WHERE project_id=? AND client_id=?', (project_id, client_id)).fetchone()
            if old:
                return jsonify(self.snapshot(dict(old))), 202
            key = request.headers.get('X-OpenAI-Key', '').strip()
            if not key or len(key) > 512 or '\n' in key or '\r' in key:
                raise self.Error('Enter your OpenAI API key.')
            try:
                cipher = protect_secret(key)
            except Exception:
                raise self.Error('FUPCJ Server could not protect the API key. Restart the installed Windows processor and try again.', 503)
            model = options.get('model') or 'gpt-6-astra'
            if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', model):
                raise self.Error('Choose a valid model.')
            message = str(options.get('message') or '')[:50000]
            try:
                limit = min(64000, max(512, int(options.get('maxOutputTokens', 16000))))
            except (ValueError, TypeError):
                raise self.Error('Choose a valid output length.')
            previous = options.get('previousRunId') or None
            if previous:
                prior = self.row(previous, project_id)
                if prior['status'] not in TERMINAL:
                    raise self.Error('Wait for the previous response before continuing.', 409)
            uploads = []
            for field in ('file', 'archive', 'attachments', 'attachments[]'):
                uploads.extend(request.files.getlist(field))
            if len(uploads) > 20:
                raise self.Error('Attach up to 20 files per message.')
            if not uploads and not message.strip():
                raise self.Error('Write a message or attach a project.')
            run_id = secrets.token_hex(16)
            directory = self.app.config['DATA_DIR'] / 'projects' / project_id / run_id
            directory.mkdir(parents=True, exist_ok=True)
            inputs = []
            try:
                total = 0
                for upload in uploads:
                    name = safe_name(upload.filename)
                    path = directory / (secrets.token_hex(12) + '.input')
                    size = 0
                    with path.open('wb') as target:
                        while chunk := upload.stream.read(65536):
                            size += len(chunk)
                            total += len(chunk)
                            if total > UPLOAD_LIMIT:
                                raise self.Error('Attachments must total less than 25 MB.', 413)
                            target.write(chunk)
                    inputs.append(dict(path=str(path), name=name, mime=upload.mimetype or 'application/octet-stream', size=size))
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    old = db.execute('SELECT * FROM project_runs WHERE project_id=? AND client_id=?', (project_id, client_id)).fetchone()
                    if old:
                        import shutil
                        shutil.rmtree(directory, ignore_errors=True)
                        return jsonify(self.snapshot(dict(old))), 202
                    busy = db.execute("SELECT id FROM project_runs WHERE project_id=? AND status IN ('queued','preparing','submitting','in_progress','saving')", (project_id,)).fetchone()
                    if busy:
                        raise self.Error('A response is already running in this project.', 409)
                    count = db.execute("SELECT COUNT(*) FROM project_runs WHERE status IN ('queued','preparing','submitting','in_progress','saving')").fetchone()[0]
                    if count >= 25:
                        raise self.Error('FUPCJ Server run queue is full. Try again after a response finishes.', 429)
                    now = time.time()
                    db.execute('''INSERT INTO project_runs(id,project_id,client_id,status,phase,message,model,max_tokens,previous_id,key_cipher,inputs_json,project_prompt,created_at,updated_at)
                        VALUES(?,?,?,'queued','Waiting for FUPCJ Server',?,?,?,?,?,?,?,?,?)''',
                        (run_id, project_id, client_id, message, model, limit, previous, cipher, json.dumps(inputs), str(options.get('projectPrompt') or '')[:250000], now, now))
            except Exception:
                import shutil
                shutil.rmtree(directory, ignore_errors=True)
                raise
            snap = self.update(run_id)
            self.wake.set()
            return jsonify(snap), 202

        @app.get('/api/projects/<project_id>/runs/<run_id>')
        def persistent_run(project_id, run_id):
            self.project(project_id)
            return jsonify(self.snapshot(self.row(run_id, project_id)))

        @app.get('/api/projects/<project_id>/runs/<run_id>/events')
        def persistent_events(project_id, run_id):
            self.project(project_id)
            self.row(run_id, project_id)
            after = request.args.get('after', '0')
            if not after.isdigit():
                raise self.Error('Invalid event cursor.')
            def events():
                cursor, end = int(after), time.monotonic()+25
                while time.monotonic() < end:
                    with self.db() as db:
                        rows = db.execute('SELECT sequence,snapshot_json FROM run_events WHERE run_id=? AND sequence>? ORDER BY sequence', (run_id, cursor)).fetchall()
                    if rows:
                        for item in rows:
                            cursor = item['sequence']
                            yield f'id: {cursor}\nevent: snapshot\ndata: {item["snapshot_json"]}\n\n'
                    snap = self.snapshot(run_id)
                    if not rows and snap['sequence'] > cursor:
                        cursor = snap['sequence']
                        yield f'id: {cursor}\nevent: snapshot\ndata: {json.dumps(snap)}\n\n'
                    if snap['status'] in TERMINAL:
                        return
                    yield ': keepalive\n\n'
                    self.stop.wait(1)
            return Response(stream_with_context(events()), content_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

        @app.post('/api/projects/<project_id>/runs/<run_id>/cancel')
        def persistent_cancel(project_id, run_id):
            self.project(project_id)
            value = self.row(run_id, project_id)
            if value['status'] not in TERMINAL:
                if value['status'] == 'queued':
                    self.update(run_id, cancel_requested=1, status='cancelled', phase='Cancelled', key_cipher=None)
                else:
                    self.update(run_id, cancel_requested=1, phase='Stopping')
                self.wake.set()
            return jsonify(self.snapshot(run_id))

        @app.get('/api/projects/<project_id>/runs/<run_id>/artifacts/<artifact_id>')
        def persistent_artifact(project_id, run_id, artifact_id):
            self.project(project_id)
            self.row(run_id, project_id)
            with self.db() as db:
                file = db.execute('SELECT * FROM run_artifacts WHERE id=? AND run_id=?', (artifact_id, run_id)).fetchone()
            if not file or not file['path'] or not Path(file['path']).is_file():
                raise self.Error('This file is not available on FUPCJ Server.', 404)
            return send_file(file['path'], as_attachment=True, download_name=file['name'], mimetype=file['mime'], max_age=0)

    def call(self, key, method, path, **kwargs):
        try:
            result = requests.request(method, OPENAI+path, headers={'Authorization': 'Bearer '+key}, timeout=(15, 60), **kwargs)
        except requests.RequestException:
            raise UpstreamFailure('OpenAI could not be reached. FUPCJ Server will reconnect.')
        if result.status_code >= 400:
            try:
                message = str(result.json().get('error', {}).get('message') or 'OpenAI rejected the request.')
            except (ValueError, AttributeError):
                message = 'OpenAI rejected the request.'
            result.close()
            raise UpstreamFailure(message.replace(key, '[redacted]')[:700], result.status_code)
        return result

    def call_json(self, key, method, path, **kwargs):
        response = self.call(key, method, path, **kwargs)
        try:
            try:
                return response.json()
            except ValueError:
                raise UpstreamFailure('OpenAI returned an incomplete response. FUPCJ Server will reconnect.')
        finally:
            response.close()

    def recover(self):
        with self.db() as db:
            rows = db.execute("SELECT * FROM project_runs WHERE status IN ('preparing','submitting','in_progress','saving')").fetchall()
        for record in rows:
            row = dict(record)
            if row['status'] == 'saving' and row['response_json']:
                self.update(row['id'], phase='Resuming saved files', next_attempt=0)
            elif row['response_id']:
                self.update(row['id'], status='in_progress', phase='Reconnecting after restart', next_attempt=0)
            elif row['status'] == 'preparing':
                self.update(row['id'], status='queued', phase='Resuming after restart', next_attempt=0)
            else:
                self.update(row['id'], status='error', phase='Check interrupted submission', key_cipher=None,
                            error='FUPCJ Server restarted before the response ID was saved. Check OpenAI usage before starting another run; this request was not automatically submitted again.')

    def lineage(self, row):
        values, seen = [], set()
        current = row
        while current and current['id'] not in seen:
            seen.add(current['id'])
            values.append(current)
            current = self.row(current['previous_id'], row['project_id']) if current['previous_id'] else None
        return list(reversed(values))

    def prepare_payload(self, row, key):
        chain = self.lineage(row)
        previous = next((item for item in reversed(chain[:-1]) if item['response_id'] and item['status'] in ('completed', 'incomplete')), None)
        files = json.loads(row['inputs_json'])
        container = None
        if previous and previous['container_id']:
            try:
                candidate = self.call_json(key, 'GET', '/containers/'+previous['container_id'])
                if candidate.get('status') == 'active':
                    container = previous['container_id']
            except UpstreamFailure as error:
                if error.status not in (404, 410):
                    raise
        if not container:
            files = []
            for item in chain:
                files.extend(json.loads(item['inputs_json']))
                with self.db() as db:
                    artifacts = db.execute('SELECT * FROM run_artifacts WHERE run_id=? AND path IS NOT NULL', (item['id'],)).fetchall()
                files.extend(dict(path=f['path'], name=f['name'], mime=f['mime'], size=f['size']) for f in artifacts)
        uploaded, seen = [], set()
        for file in files:
            if file['path'] in seen:
                continue
            seen.add(file['path'])
            with open(file['path'], 'rb') as source:
                if container:
                    self.call_json(key, 'POST', '/containers/'+container+'/files', files={'file': (file['name'], source, file['mime'])})
                else:
                    result = self.call_json(key, 'POST', '/files', data={'purpose': 'user_data', 'expires_after[anchor]': 'created_at', 'expires_after[seconds]': '86400'}, files={'file': (file['name'], source, file['mime'])})
                    uploaded.append(result['id'])
        message = row['message'] or 'Carry out the task specified in the attached project instructions.'
        payload = dict(model=row['model'], instructions=self.instructions,
                       input=message, background=True, stream=True, store=True, max_output_tokens=row['max_tokens'],
                       tools=[dict(type='code_interpreter', container=container or dict(type='auto', memory_limit='1g', file_ids=uploaded))])
        if files:
            payload['tool_choice'] = 'required'
        if previous and previous['response_id']:
            try:
                self.call_json(key, 'GET', '/responses/'+previous['response_id'])
                payload['previous_response_id'] = previous['response_id']
            except UpstreamFailure as error:
                if error.status not in (404, 410):
                    raise
        if len(chain) > 1 and 'previous_response_id' not in payload:
            # Stored conversation text survives provider response expiration.
            history = []
            for item in chain[:-1]:
                if item['message']:
                    history.append(dict(role='user', content=item['message']))
                if item['text']:
                    history.append(dict(role='assistant', content=item['text']))
            payload['input'] = history + [dict(role='user', content=message)]
        if re.match(r'^(gpt-[56](?:[.-]|$)|o[134](?:[.-]|$))', row['model']):
            payload['reasoning'] = {'summary': 'auto'}
        return payload

    def absorb_response(self, run_id, response, replace_text=True):
        fields = {}
        rid = response.get('id')
        if rid and ID.fullmatch(str(rid)):
            fields['response_id'] = rid
        text = []
        for item in response.get('output', []):
            if item.get('type') == 'code_interpreter_call' and ID.fullmatch(str(item.get('container_id', ''))):
                fields['container_id'] = item['container_id']
            for content in item.get('content', []):
                if content.get('type') == 'output_text':
                    text.append(content.get('text', ''))
                elif content.get('type') == 'refusal':
                    text.append(content.get('refusal', ''))
        if text and replace_text:
            fields['text'] = '\n\n'.join(text)
        status = response.get('status')
        if status in ('completed', 'incomplete', 'failed', 'cancelled'):
            fields.update(status='saving', phase='Saving response and files', response_json=json.dumps(response))
        else:
            fields.update(status='in_progress', phase='Preparing response' if status == 'queued' else 'Working')
        return self.update(run_id, **fields)

    def consume(self, run_id, stream):
        current = self.row(run_id)
        pending_text, cursor, last_write = current['text'], current['upstream_sequence'], time.monotonic()
        try:
            for line in stream.iter_lines(chunk_size=1):
                if not line.startswith(b'data:'):
                    continue
                raw = line[5:].strip()
                if raw == b'[DONE]':
                    break
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                seq = event.get('sequence_number')
                if isinstance(seq, int) and seq <= cursor:
                    continue
                if isinstance(seq, int):
                    cursor = seq
                kind = event.get('type', '')
                if kind == 'response.output_text.delta':
                    pending_text += str(event.get('delta', ''))
                    if time.monotonic() - last_write >= .25:
                        self.update(run_id, text=pending_text, upstream_sequence=cursor, phase='Writing response')
                        last_write = time.monotonic()
                elif event.get('response'):
                    self.update(run_id, text=pending_text, upstream_sequence=cursor)
                    self.absorb_response(run_id, event['response'])
                    pending_text = self.row(run_id)['text']
                elif 'code_interpreter_call' in kind:
                    self.update(run_id, text=pending_text, upstream_sequence=cursor, phase='Running code' if kind.endswith('.in_progress') or kind.endswith('.interpreting') else 'Working with files')
                elif 'reasoning' in kind and kind.endswith('.added'):
                    self.update(run_id, text=pending_text, upstream_sequence=cursor, phase='Thinking')
                if self.stop.is_set() or self.row(run_id)['cancel_requested']:
                    break
        except requests.RequestException:
            pass  # known response ID is recovered without submitting again
        finally:
            stream.close()
            self.update(run_id, text=pending_text, upstream_sequence=cursor)

    def retain_artifacts(self, row, key, response):
        candidates = {}
        for item in response.get('output', []):
            for content in item.get('content', []):
                for annotation in content.get('annotations', []):
                    if annotation.get('type') == 'container_file_citation':
                        cid, fid = annotation.get('container_id', ''), annotation.get('file_id', '')
                        if ID.fullmatch(cid) and ID.fullmatch(fid):
                            candidates[(cid, fid)] = safe_name(annotation.get('filename') or fid)
        # Retain other generated files as well as files cited in the final answer.
        if row['container_id']:
            try:
                params = {'limit': 100}
                while True:
                    page = self.call_json(key, 'GET', '/containers/'+row['container_id']+'/files', params=params)
                    for file in page.get('data', []):
                        if file.get('source') == 'assistant' and ID.fullmatch(str(file.get('id', ''))):
                            candidates[(row['container_id'], file['id'])] = safe_name(file.get('path') or file.get('filename') or file['id'])
                    last = page.get('last_id')
                    if not page.get('has_more') or not isinstance(last, str) or not ID.fullmatch(last) or params.get('after') == last:
                        break
                    params['after'] = last
            except UpstreamFailure:
                pass  # citations still provide the final deliverables
        for (cid, fid), name in candidates.items():
            with self.db() as db:
                old = db.execute('SELECT * FROM run_artifacts WHERE run_id=? AND container_id=? AND file_id=?', (row['id'], cid, fid)).fetchone()
                if old and old['path']:
                    continue
                aid = old['id'] if old else secrets.token_hex(16)
                mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
                db.execute('INSERT OR IGNORE INTO run_artifacts(id,run_id,container_id,file_id,name,mime) VALUES(?,?,?,?,?,?)', (aid, row['id'], cid, fid, name, mime))
                cached = db.execute('''SELECT a.path,a.size FROM run_artifacts a JOIN project_runs r ON r.id=a.run_id
                    WHERE r.project_id=? AND a.container_id=? AND a.file_id=? AND a.path IS NOT NULL LIMIT 1''',
                    (row['project_id'], cid, fid)).fetchone()
                if cached and Path(cached['path']).is_file():
                    db.execute('UPDATE run_artifacts SET path=?,size=?,error=NULL WHERE id=?', (cached['path'], cached['size'], aid))
                    continue
            path = self.app.config['DATA_DIR'] / 'projects' / row['project_id'] / row['id'] / (aid+'.artifact')
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.part')
            try:
                source = self.call(key, 'GET', f'/containers/{cid}/files/{fid}/content', stream=True)
                size = 0
                try:
                    with temporary.open('wb') as output:
                        for chunk in source.iter_content(65536):
                            size += len(chunk)
                            if size > ARTIFACT_LIMIT:
                                raise UpstreamFailure('This generated file exceeds the 100 MB retention limit.', 413)
                            output.write(chunk)
                finally:
                    source.close()
                os.replace(temporary, path)
                with self.db() as db:
                    db.execute('UPDATE run_artifacts SET path=?,size=?,error=NULL WHERE id=?', (str(path), size, aid))
            except (UpstreamFailure, requests.RequestException) as error:
                with self.db() as db:
                    db.execute('UPDATE run_artifacts SET error=? WHERE id=?', ('FUPCJ Server could not retain this file: '+str(error).replace(key, '[redacted]')[:400], aid))
                if isinstance(error, requests.RequestException) or getattr(error, 'status', 500) >= 500 or getattr(error, 'status', 500) == 429:
                    raise UpstreamFailure('Reconnecting to save generated files.')
            finally:
                temporary.unlink(missing_ok=True)
            self.update(row['id'])

    def work_once(self):
        with self.db() as db:
            record = db.execute("SELECT * FROM project_runs WHERE status IN ('queued','preparing','submitting','in_progress','saving') AND next_attempt<=? ORDER BY created_at LIMIT 1", (time.time(),)).fetchone()
        if record is None:
            return False
        row = dict(record)
        run_id, key = row['id'], ''
        try:
            key = unprotect_secret(row['key_cipher'])
            if row['cancel_requested'] and row['status'] != 'saving':
                if row['response_id']:
                    result = self.call_json(key, 'GET', '/responses/'+row['response_id'])
                    if result.get('status') in ('queued', 'in_progress'):
                        result = self.call_json(key, 'POST', '/responses/'+row['response_id']+'/cancel')
                    self.absorb_response(run_id, result)
                else:
                    self.update(run_id, status='cancelled', phase='Cancelled', key_cipher=None)
                    return True
            elif row['status'] == 'queued':
                self.update(run_id, status='preparing', phase='Preparing project files')
                payload = self.prepare_payload(row, key)
                if self.row(run_id)['cancel_requested']:
                    self.update(run_id, status='cancelled', phase='Cancelled', key_cipher=None)
                    return True
                # Persist before POST; a crash here must never blindly create a second paid run.
                self.update(run_id, status='submitting', phase='Starting response')
                stream = self.call(key, 'POST', '/responses', json=payload, stream=True)
                self.consume(run_id, stream)
            row = self.row(run_id)
            if row['status'] not in TERMINAL and not row['response_id']:
                self.update(run_id, status='error', phase='Check interrupted submission', key_cipher=None,
                            error='OpenAI did not return a saved response ID. Check your OpenAI usage before starting another run; this request will not be submitted again automatically.')
                return True
            if row['response_id'] and row['status'] != 'saving':
                response = self.call_json(key, 'GET', '/responses/'+row['response_id'])
                self.absorb_response(run_id, response, replace_text=response.get('status') not in ('queued', 'in_progress'))
                row = self.row(run_id)
                if row['status'] == 'in_progress' and not row['cancel_requested']:
                    params = {'stream': 'true'}
                    if row['upstream_sequence'] >= 0:
                        params['starting_after'] = row['upstream_sequence']
                    stream = self.call(key, 'GET', '/responses/'+row['response_id'], params=params, stream=True)
                    self.consume(run_id, stream)
            row = self.row(run_id)
            if row['status'] == 'saving':
                response = json.loads(row['response_json'])
                self.retain_artifacts(row, key, response)
                status = response.get('status', 'failed')
                if status == 'failed':
                    status = 'error'
                error = (response.get('error') or {}).get('message')
                if status == 'incomplete':
                    error = 'The response stopped before finishing ('+str((response.get('incomplete_details') or {}).get('reason', 'output limit'))+'). You can continue the conversation.'
                self.update(run_id, status=status, phase={'completed': 'Complete', 'cancelled': 'Cancelled', 'incomplete': 'Response incomplete'}.get(status, 'Stopped'),
                            error=str(error).replace(key, '[redacted]')[:700] if error else None, key_cipher=None)
            else:
                self.update(run_id, next_attempt=time.time()+2)
        except UpstreamFailure as error:
            row = self.row(run_id)
            message = str(error).replace(key, '[redacted]') if key else str(error)
            if row['response_id'] and (error.status >= 500 or error.status == 429):
                self.update(run_id, phase='Reconnecting to OpenAI', next_attempt=time.time()+10)
            elif row['status'] == 'preparing' and (error.status >= 500 or error.status == 429):
                self.update(run_id, status='queued', phase='Waiting for connection', next_attempt=time.time()+10)
            else:
                if row['status'] == 'submitting' and not row['response_id'] and error.status >= 500:
                    message += ' Submission may have been accepted. Check OpenAI usage before starting another run; no duplicate request was sent.'
                self.update(run_id, status='error', phase='Stopped', error=message[:900], key_cipher=None)
        except Exception:
            self.update(run_id, status='error', phase='Stopped', error='FUPCJ Server could not resume this run. Check the processor storage and Windows account. No new OpenAI request was submitted automatically.', key_cipher=None)
        return True

    def loop(self):
        while not self.stop.is_set():
            try:
                if self.work_once():
                    continue
            except Exception:
                self.app.logger.error('Project session worker encountered a local error.')
            self.wake.wait(2)
            self.wake.clear()

    def start(self):
        self.recover()
        self.stop.clear()
        threading.Thread(target=self.loop, name='vision-responses', daemon=True).start()
