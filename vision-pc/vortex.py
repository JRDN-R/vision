"""Account-owned, restartable Vortex downloads on the existing FUPCJ processor.

The SQLite queue is authoritative; browsers only enqueue and poll. Each engine
runs in a cancellable subprocess with a public-network guard, bounded disk/time,
no user flags, and no credentials inherited from Firebase requests.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import importlib.machinery
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time

from flask import g, jsonify, request, send_file

from uploaded_media import WindowsJob
from vortex_network import engine_for, validate_input

RETENTION_SECONDS = 5 * 86400
MAX_BYTES = 2 * 1024**3
ACCOUNT_BYTES = 10 * 1024**3
GLOBAL_BYTES = 40 * 1024**3
DISK_RESERVE = 2 * 1024**3
MAX_ITEMS = 50
MAX_DURATION = 7200
MAX_ACTIVE = 3
MAX_GLOBAL_ACTIVE = 20
MAX_HISTORY = 500
TICKET_SECONDS = 300
ACTIVE = ('queued', 'processing')
TERMINAL = ('complete', 'error', 'cancelled', 'expired')


class VortexJobs:
    def __init__(self, app, connect_db, api_error):
        self.app, self.db, self.Error = app, connect_db, api_error
        self.stop, self.wake = threading.Event(), threading.Event()
        self.worker_lock = threading.Lock()
        self.thread = None
        self._sweep_lock = threading.Lock()
        self.register_routes()

    @property
    def root(self):
        return Path(self.app.config['DATA_DIR']) / 'vortex'

    def initialize(self):
        self.root.mkdir(exist_ok=True, mode=0o700)
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS vortex_jobs (
                id TEXT PRIMARY KEY, uid TEXT NOT NULL, request_id TEXT NOT NULL,
                input TEXT NOT NULL, kind TEXT NOT NULL, quality TEXT NOT NULL,
                engine TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL,
                progress REAL, media_json TEXT, results_json TEXT, filename TEXT,
                output_size INTEGER NOT NULL DEFAULT 0, error TEXT,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL,
                expires_at REAL, attempts INTEGER NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                delete_requested INTEGER NOT NULL DEFAULT 0,
                UNIQUE(uid,request_id))''')
            db.execute('CREATE INDEX IF NOT EXISTS vortex_owner_created ON vortex_jobs(uid,created_at DESC)')
            db.execute('CREATE INDEX IF NOT EXISTS vortex_queue ON vortex_jobs(status,created_at)')

    def capability(self):
        packages = self.app.config.get('VORTEX_PACKAGES')
        def available(module):
            return ((packages and importlib.machinery.PathFinder.find_spec(module, [packages]) is not None)
                    or importlib.util.find_spec(module) is not None)
        engines = {name: bool(available(module)) for name, module in
                   (('yt-dlp', 'yt_dlp'), ('gallery-dl', 'gallery_dl'), ('spotdl', 'spotdl'))}
        ffmpeg = Path(self.app.config.get('FFMPEG', ''))
        ready = bool(self.app.config.get('FIREBASE_IDENTITY')) and engines['yt-dlp'] and ffmpeg.is_file()
        return dict(ready=ready, engines=engines, retentionDays=5, maxFileBytes=MAX_BYTES,
                    maxItems=MAX_ITEMS, maxDuration=MAX_DURATION, persistentJobs=True,
                    firebaseRequired=True, spotifyNote='Spotify supplies track metadata; audio is matched from another public service, not downloaded from Spotify.')

    def _uid(self):
        uid = getattr(g, 'uid', '')
        if getattr(g, 'auth_kind', '') != 'firebase-google' or not uid.startswith('firebase:'):
            raise self.Error('Sign in to use Vortex.', 401)
        return uid

    def row(self, job_id, uid=None):
        if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{24}', job_id):
            raise self.Error('This Vortex item is unavailable.', 404)
        with self.db() as db:
            row = db.execute('SELECT * FROM vortex_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None or (uid is not None and row['uid'] != uid) or row['delete_requested']:
            raise self.Error('This Vortex item is unavailable.', 404)
        return dict(row)

    def snapshot(self, value):
        media = json.loads(value['media_json']) if value.get('media_json') else None
        expired = value['status'] == 'complete' and value.get('expires_at') is not None and value['expires_at'] <= time.time()
        status = 'expired' if expired else value['status']
        return dict(id=value['id'], requestId=value['request_id'], input=value['input'],
                    kind=value['kind'], quality=value['quality'], engine=value['engine'],
                    status=status, phase='File expired' if expired else value['phase'],
                    progress=value['progress'], error=value.get('error'),
                    media=media, results=json.loads(value['results_json']) if value.get('results_json') else [],
                    title=(media or {}).get('title'), source=(media or {}).get('source'),
                    thumbnail=(media or {}).get('thumbnail'), format=Path(value.get('filename') or '').suffix.lstrip('.'),
                    filename=None if expired else value.get('filename'), size=0 if expired else value['output_size'],
                    createdAt=value['created_at'], updatedAt=value['updated_at'],
                    completedAt=value.get('completed_at'), expiresAt=value.get('expires_at'),
                    cancelRequested=bool(value['cancel_requested']),
                    resultReady=status == 'complete' and bool(value.get('filename')))

    def _capacity(self, db, uid, kind):
        rows = db.execute('SELECT uid,status,kind,output_size,delete_requested FROM vortex_jobs').fetchall()
        if sum(r['status'] in ACTIVE for r in rows if r['uid'] == uid) >= MAX_ACTIVE:
            raise self.Error('Three Vortex operations are already active. Wait for one to finish.', 429)
        if sum(r['status'] in ACTIVE for r in rows) >= MAX_GLOBAL_ACTIVE:
            raise self.Error('The Vortex queue is full. Try again shortly.', 429)
        if sum(r['uid'] == uid and not r['delete_requested'] for r in rows) >= MAX_HISTORY:
            raise self.Error('Your Vortex history is full. Remove older entries to continue.', 429)
        if kind == 'download':
            def reserved(row):
                return MAX_BYTES if row['status'] in ACTIVE and row['kind'] == 'download' else row['output_size']
            if sum(reserved(r) for r in rows if r['uid'] == uid) + MAX_BYTES > ACCOUNT_BYTES:
                raise self.Error('Your Vortex storage is full. Delete a saved file to continue.', 507)
            if sum(reserved(r) for r in rows) + MAX_BYTES > GLOBAL_BYTES:
                raise self.Error('Vortex storage is full. Try again after files expire.', 507)
            pending = sum(MAX_BYTES for r in rows if r['status'] in ACTIVE and r['kind'] == 'download')
            if shutil.disk_usage(self.root).free < DISK_RESERVE + pending + 3 * MAX_BYTES:
                raise self.Error('FUPCJ Server needs more free space before accepting another download.', 507)

    def enqueue(self, data, uid):
        if not isinstance(data, dict):
            raise self.Error('Send a media link or search as JSON.')
        kind, quality = data.get('kind', 'download'), data.get('quality', 'max')
        if kind not in ('inspect', 'download') or quality not in ('small', 'balanced', 'max'):
            raise self.Error('Choose a supported Vortex operation and quality.')
        request_id = data.get('requestId') or secrets.token_hex(16)
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', request_id):
            raise self.Error('Use a valid request identifier.')
        try:
            # Syntax before idempotency lookup; DNS only for newly accepted work.
            value = validate_input(data.get('input', data.get('url')), kind, resolve=False)
        except ValueError as error:
            raise self.Error(str(error)) from None
        with self.db() as db:
            old = db.execute('SELECT * FROM vortex_jobs WHERE uid=? AND request_id=?', (uid, request_id)).fetchone()
        if old:
            if old['input'] != value or old['kind'] != kind or old['quality'] != quality:
                raise self.Error('That request identifier belongs to a different Vortex operation.', 409)
            if old['delete_requested']:
                raise self.Error('This Vortex request has been removed.', 410)
            return self.snapshot(dict(old))
        try:
            value = validate_input(value, kind)
        except ValueError as error:
            raise self.Error(str(error)) from None
        engine = engine_for(value)
        capability = self.capability()
        if not capability['ready']:
            raise self.Error('Vortex is not ready on FUPCJ Server. Update the processor and enable Firebase sign-in.', 503)
        if not capability['engines'].get(engine):
            raise self.Error('This media engine is not installed. Run Setup-Vision-PC.ps1 -Action InstallVortexTools on FUPCJ Server.', 503)
        now, job_id = time.time(), secrets.token_hex(12)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # A second tab may have accepted the same request during DNS resolution.
            old = db.execute('SELECT * FROM vortex_jobs WHERE uid=? AND request_id=?', (uid, request_id)).fetchone()
            if old:
                if (old['input'], old['kind'], old['quality']) != (value, kind, quality):
                    raise self.Error('That request identifier belongs to a different Vortex operation.', 409)
                if old['delete_requested']:
                    raise self.Error('This Vortex request has been removed.', 410)
                return self.snapshot(dict(old))
            self._capacity(db, uid, kind)
            db.execute('''INSERT INTO vortex_jobs(id,uid,request_id,input,kind,quality,engine,status,phase,created_at,updated_at)
                          VALUES(?,?,?,?,?,?,?,'queued','Waiting for FUPCJ Server',?,?)''',
                       (job_id, uid, request_id, value, kind, quality, engine, now, now))
        self.wake.set()
        return self.snapshot(self.row(job_id, uid))

    def register_routes(self):
        app = self.app

        @app.get('/api/vortex/capabilities')
        def vortex_capabilities():
            self._uid()
            return jsonify(self.capability())

        @app.route('/api/vortex/jobs', methods=['GET', 'POST'])
        def vortex_jobs():
            uid = self._uid()
            if request.method == 'POST':
                return jsonify(self.enqueue(request.get_json(silent=True), uid)), 202
            cursor = request.args.get('cursor')
            clause, args = '', [uid]
            kind = request.args.get('kind')
            if kind:
                if kind not in ('inspect', 'download'):
                    raise self.Error('Choose a supported Vortex operation.')
                clause += ' AND kind=?'
                args.append(kind)
            if cursor:
                prior = self.row(cursor, uid)
                clause += ' AND (created_at<? OR (created_at=? AND id<?))'
                args += [prior['created_at'], prior['created_at'], prior['id']]
            with self.db() as db:
                rows = db.execute('SELECT * FROM vortex_jobs WHERE uid=? AND delete_requested=0' + clause +
                                  ' ORDER BY created_at DESC,id DESC LIMIT 101', args).fetchall()
            return jsonify(jobs=[self.snapshot(dict(row)) for row in rows[:100]],
                           nextCursor=rows[99]['id'] if len(rows) > 100 else None, serverTime=time.time())

        @app.route('/api/vortex/jobs/<job_id>', methods=['GET', 'DELETE'])
        def vortex_item(job_id):
            uid = self._uid()
            self.row(job_id, uid)
            if request.method == 'DELETE':
                # Completion and deletion serialize on the same DB write lock.
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    current = db.execute('SELECT status FROM vortex_jobs WHERE id=? AND uid=?', (job_id, uid)).fetchone()
                    if current is None:
                        raise self.Error('This Vortex item is unavailable.', 404)
                    running = current['status'] == 'processing'
                    if running:
                        db.execute('UPDATE vortex_jobs SET cancel_requested=1,delete_requested=1,updated_at=? WHERE id=?', (time.time(), job_id))
                    else:
                        db.execute("UPDATE vortex_jobs SET cancel_requested=1,delete_requested=1,status='cancelled',updated_at=? WHERE id=?", (time.time(), job_id))
                if not running:
                    self._remove_terminal(job_id, delete=True)
                self.wake.set()
                return jsonify(deleted=True, id=job_id)
            return jsonify(self.snapshot(self.row(job_id, uid)))

        @app.post('/api/vortex/jobs/<job_id>/cancel')
        def vortex_cancel(job_id):
            uid = self._uid()
            self.row(job_id, uid)
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT status FROM vortex_jobs WHERE id=? AND uid=?', (job_id, uid)).fetchone()
                if row is None:
                    raise self.Error('This Vortex item is unavailable.', 404)
                if row['status'] not in ACTIVE:
                    raise self.Error('This operation has already finished.', 409)
                db.execute("UPDATE vortex_jobs SET cancel_requested=1,phase='Cancelling',updated_at=? WHERE id=?", (time.time(), job_id))
                if row['status'] == 'queued':
                    db.execute("UPDATE vortex_jobs SET status='cancelled',phase='Cancelled',completed_at=? WHERE id=?", (time.time(), job_id))
            self.wake.set()
            return jsonify(self.snapshot(self.row(job_id, uid)))

        @app.post('/api/vortex/jobs/<job_id>/ticket')
        def vortex_ticket(job_id):
            uid = self._uid()
            value = self.authorized_result(job_id, uid)
            expiry = min(int(time.time()) + TICKET_SECONDS, int(value['expires_at']))
            payload = base64.urlsafe_b64encode(json.dumps([job_id, uid, expiry], separators=(',', ':')).encode()).rstrip(b'=').decode()
            signature = hmac.new(self.ticket_key(), payload.encode(), hashlib.sha256).hexdigest()
            url = self.app.config.get('BACKEND_URL', '').rstrip('/') + '/api/vortex/jobs/' + job_id + '/file?ticket=' + payload + '.' + signature
            return jsonify(url=url, expiresAt=expiry)

        @app.get('/api/vortex/jobs/<job_id>/file')
        def vortex_file(job_id):
            uid = g.uid if getattr(g, 'auth_kind', '') == 'vortex-ticket' else self._uid()
            value = self.authorized_result(job_id, uid)
            path = self.output_path(value)
            response = send_file(path, as_attachment=True, download_name=self.download_name(value),
                                 mimetype=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                                 conditional=True, max_age=0)
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
            return response

    def ticket_key(self):
        return hmac.new(self.app.config['CONNECTION_TOKEN'].encode(), b'vision:vortex:file:v1', hashlib.sha256).digest()

    def authorize_ticket(self):
        """The server calls this only for GET/HEAD on the exact file route."""
        match = re.fullmatch(r'/api/vortex/jobs/([0-9a-f]{24})/file', request.path)
        if request.method not in ('GET', 'HEAD') or match is None:
            raise self.Error('This file link is invalid.', 401)
        ticket = request.args.get('ticket', '')
        if not isinstance(ticket, str) or len(ticket) > 1024:
            raise self.Error('This file link has expired. Open Vortex to download it again.', 401)
        try:
            payload, signature = ticket.split('.')
            expected = hmac.new(self.ticket_key(), payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError()
            job_id, uid, expiry = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
            if (job_id != match.group(1) or not isinstance(uid, str) or not uid.startswith('firebase:') or
                    not isinstance(expiry, int) or not time.time() < expiry <= time.time() + TICKET_SECONDS + 1):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            raise self.Error('This file link has expired. Open Vortex to download it again.', 401) from None
        self.authorized_result(job_id, uid)
        g.uid, g.auth_kind = uid, 'vortex-ticket'

    def authorized_result(self, job_id, uid):
        value = self.row(job_id, uid)
        if (value['status'] != 'complete' or value['cancel_requested'] or not value['filename'] or
                not value['expires_at'] or value['expires_at'] <= time.time()):
            raise self.Error('This Vortex file has expired or is unavailable.', 410)
        self.output_path(value)
        return value

    def output_path(self, value):
        filename = value.get('filename')
        if not isinstance(filename, str) or filename != Path(filename).name or not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', filename):
            raise self.Error('This Vortex file is unavailable.', 410)
        directory = self.root / value['id']
        path = directory / filename
        if directory.is_symlink() or path.is_symlink() or path.resolve().parent != directory.resolve() or not path.is_file():
            raise self.Error('This Vortex file is unavailable.', 410)
        return path

    def download_name(self, value):
        media = json.loads(value['media_json']) if value.get('media_json') else {}
        stem = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '', str(media.get('title') or 'Vortex media')).strip(' .')[:140] or 'Vortex media'
        return stem + Path(value['filename']).suffix

    def recover(self):
        """Startup only: accepted operations resume after a processor/Windows restart."""
        now = time.time()
        with self.db() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM vortex_jobs WHERE status IN ('queued','processing') OR delete_requested=1")]
            for row in rows:
                if row['delete_requested']:
                    db.execute('DELETE FROM vortex_jobs WHERE id=?', (row['id'],))
                elif row['cancel_requested']:
                    db.execute("UPDATE vortex_jobs SET status='cancelled',phase='Cancelled',completed_at=?,updated_at=? WHERE id=?", (now, now, row['id']))
                elif row['attempts'] >= 3:
                    db.execute("UPDATE vortex_jobs SET status='error',phase='Stopped',error='Processing stopped repeatedly. Try this item again.',completed_at=?,updated_at=? WHERE id=?", (now, now, row['id']))
                else:
                    db.execute("UPDATE vortex_jobs SET status='queued',phase='Resuming after server restart',progress=NULL,updated_at=? WHERE id=?", (now, row['id']))
        for row in rows:
            shutil.rmtree(self.root / row['id'], ignore_errors=True)
        # Remove orphan directories left by an interrupted delete, never live jobs.
        with self.db() as db:
            retained = {row[0] for row in db.execute('SELECT id FROM vortex_jobs')}
        for directory in self.root.iterdir():
            if directory.is_dir() and re.fullmatch(r'[0-9a-f]{24}', directory.name) and directory.name not in retained:
                shutil.rmtree(directory, ignore_errors=True)
        self.sweep()

    def _remove_terminal(self, job_id, delete=False):
        directory = self.root / job_id
        try:
            if directory.exists():
                shutil.rmtree(directory)
        except OSError:
            # Windows may hold an open download handle. Keep its disk reservation
            # and retry on the next sweep instead of silently leaking the file.
            return False
        with self.db() as db:
            if delete:
                db.execute("DELETE FROM vortex_jobs WHERE id=? AND status NOT IN ('queued','processing') AND delete_requested=1", (job_id,))
            else:
                db.execute("UPDATE vortex_jobs SET output_size=0 WHERE id=? AND status IN ('expired','error','cancelled')", (job_id,))
        return True

    def sweep(self):
        with self._sweep_lock:
            self._sweep()

    def _sweep(self):
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            expired = [row['id'] for row in db.execute("SELECT id FROM vortex_jobs WHERE status='complete' AND expires_at IS NOT NULL AND expires_at<=?", (now,))]
            for job_id in expired:
                db.execute("UPDATE vortex_jobs SET status='expired',phase='File expired',filename=NULL,updated_at=? WHERE id=?", (now, job_id))
            # Metadata history remains until its owner removes it. Only short-lived
            # inspection receipts are pruned; download history is never silently lost.
            db.execute("DELETE FROM vortex_jobs WHERE kind='inspect' AND status NOT IN ('queued','processing') AND updated_at<?", (now - 86400,))
            cleanup = [(row['id'], bool(row['delete_requested'])) for row in db.execute("SELECT id,delete_requested FROM vortex_jobs WHERE status IN ('expired','error','cancelled') OR (delete_requested=1 AND status NOT IN ('queued','processing'))")]
            retained = {row[0] for row in db.execute('SELECT id FROM vortex_jobs')}
        for job_id, deleted in cleanup:
            self._remove_terminal(job_id, delete=deleted)
        for directory in self.root.iterdir():
            if directory.is_dir() and re.fullmatch(r'[0-9a-f]{24}', directory.name) and directory.name not in retained:
                try:
                    shutil.rmtree(directory)
                except OSError:
                    pass  # Retry next sweep, including interrupted Windows file delivery.

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        self.recover()
        self.thread = threading.Thread(target=self.loop, name='vision-vortex', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)

    def loop(self):
        last_sweep = 0
        while not self.stop.is_set():
            try:
                if time.monotonic() - last_sweep >= 30:
                    self.sweep()
                    last_sweep = time.monotonic()
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row = db.execute("SELECT * FROM vortex_jobs WHERE status='queued' AND cancel_requested=0 AND delete_requested=0 ORDER BY created_at,id LIMIT 1").fetchone()
                    if row:
                        db.execute("UPDATE vortex_jobs SET status='processing',phase='Starting media engine',attempts=attempts+1,updated_at=? WHERE id=?", (time.time(), row['id']))
                if row:
                    try:
                        self.process(dict(row))
                    except Exception:
                        with self.db() as db:
                            db.execute("UPDATE vortex_jobs SET status='error',phase='Unable to start',error='The server could not prepare this media operation.',updated_at=? WHERE id=? AND status='processing'", (time.time(), row['id']))
                else:
                    self.wake.wait(2)
                    self.wake.clear()
            except Exception:
                # No raw extractor output, URLs, tickets or account tokens enter logs.
                self.app.logger.exception('Vortex worker checkpoint failed')
                self.stop.wait(2)

    def _event(self, job_id, event):
        if not isinstance(event, dict):
            return
        updates = {'updated_at': time.time()}
        if isinstance(event.get('phase'), str):
            updates['phase'] = event['phase'][:200]
        if 'progress' in event:
            progress = event['progress']
            updates['progress'] = (round(max(0, min(100, progress)), 2)
                                   if isinstance(progress, (float, int)) and not isinstance(progress, bool) and math.isfinite(progress) else None)
        for public, column in (('media', 'media_json'), ('results', 'results_json')):
            if public in event and isinstance(event[public], dict if public == 'media' else list):
                raw = json.dumps(event[public], separators=(',', ':'), allow_nan=False)
                if len(raw) <= 256 * 1024:
                    updates[column] = raw
        with self.db() as db:
            db.execute('UPDATE vortex_jobs SET ' + ','.join(key + '=?' for key in updates) +
                       " WHERE id=? AND status='processing' AND cancel_requested=0 AND delete_requested=0", [*updates.values(), job_id])

    def _finish(self, job_id, final):
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            raw = db.execute('SELECT * FROM vortex_jobs WHERE id=?', (job_id,)).fetchone()
            if raw is None or raw['delete_requested'] or raw['cancel_requested']:
                return False
            row = dict(raw)
            filename, size = None, 0
            if row['kind'] == 'download':
                row['filename'] = final.get('filename')
                path = self.output_path(row)
                size = path.stat().st_size
                if not 0 < size <= MAX_BYTES:
                    raise ValueError('This download exceeded the file size limit.')
                filename = path.name
            db.execute("UPDATE vortex_jobs SET status='complete',phase=?,progress=100,filename=?,output_size=?,completed_at=?,expires_at=?,updated_at=? WHERE id=?",
                       ('Ready to save' if filename else 'Media identified', filename, size, now,
                        now + RETENTION_SECONDS if filename else None, now, job_id))
        return True

    @staticmethod
    def terminate(process):
        try:
            if os.name == 'nt':
                if process.poll() is None:
                    process.kill()  # WindowsJob.close kills its descendants as well.
            else:
                # The worker leader may already have exited while ffmpeg is alive.
                os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def process(self, row):
        with self.worker_lock:
            self._process(row)

    def _process(self, row):
        job_id = row['id']
        directory = self.root / job_id
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(mode=0o700)
        specification = dict(id=job_id, input=row['input'], kind=row['kind'], quality=row['quality'],
                             directory=str(directory.resolve()), ffmpeg=self.app.config['FFMPEG'],
                             deno=self.app.config.get('DENO'), maxBytes=MAX_BYTES, maxItems=MAX_ITEMS,
                             maxDuration=MAX_DURATION, packagesPath=self.app.config.get('VORTEX_PACKAGES'))
        request_path = directory / 'request.json'
        request_path.write_text(json.dumps(specification), encoding='utf-8')
        events_path = directory / 'events.jsonl'
        process, guard, final, failed, interrupted = None, None, None, None, False
        start = time.monotonic()
        deadline = 180 if row['kind'] == 'inspect' else 45 * 60
        env = {key: value for key, value in os.environ.items() if not any(word in key.upper() for word in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY', 'CREDENTIAL', 'PROXY'))}
        # These optional, administrator-configured credentials belong solely to
        # Vortex's Spotify metadata integration; unrelated service secrets stay out.
        for key in ('VORTEX_SPOTIFY_CLIENT_ID', 'VORTEX_SPOTIFY_CLIENT_SECRET'):
            if os.environ.get(key):
                env[key] = os.environ[key]
        env['PYTHONUNBUFFERED'] = '1'
        # Isolate cache/config paths: engines cannot read installation-owner cookies.
        env['XDG_CACHE_HOME'] = str(directory / 'cache')
        env['APPDATA'] = env['LOCALAPPDATA'] = str(directory / 'cache')
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        try:
            with events_path.open('wb') as output:
                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('vortex_worker.py')), '--request', str(request_path)],
                                           stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                                           cwd=directory, env=env, **flags)
                guard = WindowsJob(process)
                with events_path.open('r', encoding='utf-8', errors='replace') as events:
                    last_size_check = 0
                    last_sweep = time.monotonic()
                    partial = ''
                    while True:
                        with self.db() as db:
                            current = db.execute('SELECT cancel_requested,delete_requested FROM vortex_jobs WHERE id=?', (job_id,)).fetchone()
                        if self.stop.is_set():
                            interrupted = True
                            break
                        if time.monotonic() - last_sweep >= 30:
                            self.sweep()
                            last_sweep = time.monotonic()
                        if current is None or current['cancel_requested'] or current['delete_requested']:
                            break
                        if time.monotonic() - start > deadline:
                            failed = 'This operation took too long. Try a shorter item or another source.'
                            break
                        # Log growth and all temporary/merged media count against bounded worker storage.
                        if time.monotonic() - last_size_check > 1:
                            total = sum(p.stat().st_size for p in directory.rglob('*') if p.is_file() and not p.is_symlink())
                            if total > 3 * MAX_BYTES or events_path.stat().st_size > 4 * 1024 * 1024 or shutil.disk_usage(directory).free < DISK_RESERVE:
                                failed = 'This operation reached the server storage limit.'
                                break
                            last_size_check = time.monotonic()
                        partial += events.read(512 * 1024)
                        lines = partial.split('\n')
                        partial = lines.pop()
                        for line in lines:
                            try:
                                event = json.loads(line)
                            except ValueError:
                                continue
                            self._event(job_id, event)
                            if isinstance(event, dict) and event.get('complete') is True:
                                final = event
                            if isinstance(event, dict) and isinstance(event.get('error'), str):
                                failed = event['error'][:400]
                        if process.poll() is not None:
                            # Files are unbuffered by the child; the next pass drained all completed writes.
                            partial += events.read(512 * 1024)
                            for line in partial.splitlines():
                                try:
                                    event = json.loads(line)
                                except ValueError:
                                    continue
                                self._event(job_id, event)
                                if isinstance(event, dict) and event.get('complete') is True:
                                    final = event
                                if isinstance(event, dict) and isinstance(event.get('error'), str):
                                    failed = event['error'][:400]
                            break
                        self.stop.wait(0.25)
            if final and not failed and process.returncode == 0 and not interrupted:
                self._event(job_id, final)
                if self._finish(job_id, final):
                    # Keep only the finished file; requests, logs, source fragments and caches are temporary.
                    filename = final.get('filename')
                    for path in directory.iterdir():
                        if path.name != filename:
                            if path.is_dir():
                                shutil.rmtree(path, ignore_errors=True)
                            else:
                                path.unlink(missing_ok=True)
                    return
        except Exception:
            failed = 'The media engine could not finish this item. Try another public link or update Vortex tools.'
        finally:
            if process:
                self.terminate(process)
            if guard:
                guard.close()
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT cancel_requested,delete_requested FROM vortex_jobs WHERE id=?', (job_id,)).fetchone()
            if current:
                if current['delete_requested']:
                    db.execute('DELETE FROM vortex_jobs WHERE id=?', (job_id,))
                elif current['cancel_requested']:
                    db.execute("UPDATE vortex_jobs SET status='cancelled',phase='Cancelled',progress=NULL,completed_at=?,updated_at=? WHERE id=?", (now, now, job_id))
                elif interrupted:
                    db.execute("UPDATE vortex_jobs SET status='queued',phase='Waiting for server restart',progress=NULL,updated_at=? WHERE id=?", (now, job_id))
                else:
                    db.execute("UPDATE vortex_jobs SET status='error',phase='Unable to finish',error=?,progress=NULL,completed_at=?,updated_at=? WHERE id=?",
                               (failed or 'The media engine stopped before finishing. Try this item again.', now, now, job_id))
        shutil.rmtree(directory, ignore_errors=True)
