"""Server-timed guest leases. Guest project documents are never saved.

Permanent receipts contain keyed hashes, not IP addresses or browser content.
These deter repeat trials; they do not identify a person for life.
"""
from __future__ import annotations
from contextlib import ExitStack
import hashlib
import hmac
import ipaddress
import re
import secrets
import shutil
import threading
import time
from flask import g, jsonify, request

DURATION = 300


class Trials:
    def __init__(self, app, db, error, sessions, transcriptions, uploaded_media, youtube_lock):
        self.app, self.db, self.Error = app, db, error
        self.sessions, self.transcriptions, self.uploaded_media = sessions, transcriptions, uploaded_media
        self.youtube_lock = youtube_lock
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.inflight = {}
        self.secret = b''
        app.add_url_rule('/api/trial/start', 'trial_start', self.start_visit, methods=['POST'])
        app.add_url_rule('/api/trial/status', 'trial_status', self.status, methods=['GET'])
        app.add_url_rule('/api/trial/end', 'trial_end', self.end_visit, methods=['POST'])

    def initialize(self):
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS trial_settings (id INTEGER PRIMARY KEY CHECK(id=1), secret TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS trial_visits (
                    id TEXT PRIMARY KEY, device_hash TEXT NOT NULL UNIQUE,
                    network_hash TEXT NOT NULL UNIQUE, started_at REAL NOT NULL,
                    expires_at REAL NOT NULL, closed INTEGER NOT NULL DEFAULT 0,
                    cleaned INTEGER NOT NULL DEFAULT 0);
            ''')
            db.execute('INSERT OR IGNORE INTO trial_settings VALUES(1,?)', (secrets.token_hex(32),))
            self.secret = bytes.fromhex(db.execute('SELECT secret FROM trial_settings WHERE id=1').fetchone()[0])
        self.sweep()

    def enabled(self):
        return bool(self.app.config.get('FIREBASE_IDENTITY')) and self.app.config.get('TRIAL_ENABLED', True)

    def digest(self, domain, value):
        return hmac.new(self.secret, (domain + '\0' + value).encode(), hashlib.sha256).hexdigest()

    def receipt(self, value):
        ident = value['id']
        return {'id': ident, 'token': 'trial_' + ident + '_' + self.digest('token', ident),
                'serverNow': time.time(), 'expiresAt': value['expires_at'], 'durationSeconds': DURATION,
                'project': {'id': 'trial-' + ident, 'key': self.digest('project', ident),
                            'revision': 0, 'backendUrl': self.app.config['BACKEND_URL']}}

    def start_visit(self):
        if not self.enabled():
            raise self.Error('Guest trials are unavailable on this server.', 503, 'trial-unavailable')
        if request.headers.get('Origin') not in {'https://jrdn-r.github.io', self.app.config['BACKEND_URL']}:
            raise self.Error('Open the online Vision page to start a trial.', 403)
        if request.content_length and request.content_length > 1024:
            raise self.Error('Invalid trial request.', 413)
        payload = request.get_json(silent=True)
        device = payload.get('deviceId', '') if isinstance(payload, dict) else ''
        if not isinstance(device, str) or not re.fullmatch(r'[0-9a-f]{64}', device):
            raise self.Error('This browser could not create a trial identifier.', 400)
        # Waitress trusts ONLY the loopback Tailscale proxy and replaces REMOTE_ADDR.
        # Never parse an arbitrary browser-supplied X-Forwarded-For value here.
        try:
            address = ipaddress.ip_address(request.remote_addr or '')
            if address.version == 6 and address.ipv4_mapped:
                address = address.ipv4_mapped
            if address.is_loopback or address.is_unspecified:
                raise ValueError('Missing client address')
            network = str(ipaddress.ip_network(str(address) + '/64', strict=False)) if address.version == 6 else str(address)
        except ValueError:
            raise self.Error('The server could not verify this connection for a trial. Sign in with Google instead.', 503, 'trial-network-unavailable')
        now = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM trial_visits WHERE device_hash=?', (self.digest('device', device),)).fetchone()
            if old:
                if old['closed'] or old['expires_at'] <= now:
                    raise self.Error('This browser has used its five-minute trial. Sign in with Google to continue.', 403, 'trial-used')
                return jsonify(self.receipt(old))
            if db.execute('SELECT 1 FROM trial_visits WHERE network_hash=?', (self.digest('network', network),)).fetchone():
                raise self.Error('The trial for this network has already been used. Shared Wi-Fi can share a trial. Sign in with Google to continue.', 403, 'trial-used')
            if db.execute('SELECT COUNT(*) FROM trial_visits WHERE expires_at>? AND closed=0', (now,)).fetchone()[0] >= 5:
                raise self.Error('The guest processing slots are busy. Try again shortly; your trial has not started.', 429, 'trial-busy')
            ident = secrets.token_hex(12)
            db.execute('INSERT INTO trial_visits(id,device_hash,network_hash,started_at,expires_at) VALUES(?,?,?,?,?)',
                       (ident, self.digest('device', device), self.digest('network', network), now, now + DURATION))
            value = db.execute('SELECT * FROM trial_visits WHERE id=?', (ident,)).fetchone()
        return jsonify(self.receipt(value)), 201

    def authorize(self, token):
        match = re.fullmatch(r'trial_([0-9a-f]{24})_([0-9a-f]{64})', token)
        if not self.enabled() or not match or not hmac.compare_digest(match[2], self.digest('token', match[1])):
            raise self.Error('This trial is invalid. Sign in with Google.', 401, 'trial-invalid')
        with self.lock, self.db() as db:
            value = db.execute('SELECT * FROM trial_visits WHERE id=?', (match[1],)).fetchone()
            if not value or value['closed'] or value['expires_at'] <= time.time():
                raise self.Error('Your five-minute trial has ended. Sign in with Google to continue.', 403, 'trial-expired')
            pid, path, method = 'trial-' + value['id'], request.path, request.method
            allowed = ((path in ('/api/health', '/api/trial/status') and method == 'GET') or
                       (path in ('/api/youtube', '/api/trial/end') and method == 'POST') or
                       (re.fullmatch(r'/api/jobs/[0-9a-f]{24}(?:/result)?', path) and method in ('GET', 'DELETE')) or
                       (re.fullmatch(r'/api/projects/' + re.escape(pid) + r'/(?:media|transcriptions|runs)(?:/[A-Za-z0-9_./-]+)?', path)
                        and method in ('GET', 'POST', 'DELETE')))
            if not allowed:
                raise self.Error('Sign in with Google to save projects or use account features.', 403, 'trial-account-required')
            g.uid, g.auth_kind, g.trial = 'trial:' + value['id'], 'trial', dict(value)
            self.inflight[value['id']] = self.inflight.get(value['id'], 0) + 1

    def status(self):
        if getattr(g, 'auth_kind', '') != 'trial':
            raise self.Error('Open a guest trial first.', 400)
        return jsonify(serverNow=time.time(), expiresAt=g.trial['expires_at'])

    def end_visit(self):
        if getattr(g, 'auth_kind', '') != 'trial':
            raise self.Error('Open a guest trial first.', 400)
        with self.db() as db:
            db.execute('UPDATE trial_visits SET closed=1 WHERE id=?', (g.trial['id'],))
        return jsonify(ended=True)

    def project(self, project_id):
        value = g.trial
        if project_id != 'trial-' + value['id'] or value['expires_at'] <= time.time():
            raise self.Error('This trial project is unavailable.', 403, 'trial-expired')
        key = request.headers.get('X-Vision-Project-Key', '')
        if not hmac.compare_digest(key.encode(), self.digest('project', value['id']).encode()):
            raise self.Error('This trial project is unavailable.', 403)
        return {'id': project_id, 'project_json': '{}', 'revision': 0, 'owner_uid': g.uid}, hashlib.sha256(key.encode()).hexdigest()

    def expired(self, project_id):
        if not project_id.startswith('trial-'):
            return False
        with self.db() as db:
            row = db.execute('SELECT closed,expires_at FROM trial_visits WHERE id=?', (project_id[6:],)).fetchone()
        return not row or bool(row['closed']) or row['expires_at'] <= time.time()

    def sweep(self):
        """Cancel first; remove temporary content only when its writers stop."""
        with self.db() as db:
            visits = db.execute('SELECT id FROM trial_visits WHERE cleaned=0 AND (closed=1 OR expires_at<=?)', (time.time(),)).fetchall()
        for visit in visits:
            ident, pid = visit['id'], 'trial-' + visit['id']
            with self.db() as db:
                db.execute("UPDATE local_transcriptions SET cancel_requested=1,status='cancelled',phase='Trial ended',result_json=NULL WHERE project_id=?", (pid,))
                db.execute("UPDATE uploaded_media SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE project_id=?", (pid,))
                db.execute('UPDATE project_runs SET cancel_requested=1 WHERE project_id=?', (pid,))
                db.execute("UPDATE jobs SET status='error',phase='Trial ended' WHERE uid=? AND status='queued'", ('trial:' + ident,))
            self.sessions.wake.set()
            with self.lock:
                if self.inflight.get(ident):
                    continue
                with ExitStack() as stack:
                    locks = (self.sessions.worker_lock, self.transcriptions._worker_lock, self.uploaded_media.worker_lock, self.youtube_lock)
                    if not all(self._acquire(stack, lock) for lock in locks):
                        continue
                    with self.db() as db:
                        if db.execute("SELECT 1 FROM project_runs WHERE project_id=? AND status NOT IN ('completed','incomplete','error','cancelled')", (pid,)).fetchone():
                            continue
                        for table, root in (('local_transcriptions', self.transcriptions.root), ('uploaded_media', self.uploaded_media.root)):
                            for row in db.execute('SELECT id FROM ' + table + ' WHERE project_id=?', (pid,)).fetchall():
                                self.remove_directory(root / row['id'])
                            db.execute('DELETE FROM ' + table + ' WHERE project_id=?', (pid,))
                        for row in db.execute('SELECT id FROM project_runs WHERE project_id=?', (pid,)).fetchall():
                            db.execute('DELETE FROM run_events WHERE run_id=?', (row['id'],))
                            db.execute('DELETE FROM run_artifacts WHERE run_id=?', (row['id'],))
                        db.execute('DELETE FROM project_runs WHERE project_id=?', (pid,))
                        self.remove_directory(self.app.config['DATA_DIR'] / 'projects' / pid)
                        for row in db.execute('SELECT id FROM jobs WHERE uid=?', ('trial:' + ident,)).fetchall():
                            for suffix in ('.json', '.tmp'):
                                (self.app.config['DATA_DIR'] / 'results' / (row['id'] + suffix)).unlink(missing_ok=True)
                        db.execute('DELETE FROM jobs WHERE uid=?', ('trial:' + ident,))
                        db.execute('UPDATE trial_visits SET cleaned=1 WHERE id=?', (ident,))

    @staticmethod
    def remove_directory(path):
        shutil.rmtree(path, ignore_errors=True)
        if path.exists():
            raise OSError('Temporary data is still in use; cleanup must retry')

    @staticmethod
    def _acquire(stack, lock):
        if not lock.acquire(blocking=False):
            return False
        stack.callback(lock.release)
        return True

    def start(self):
        self.stop.clear()
        def loop():
            while not self.stop.wait(1):
                try:
                    self.sweep()
                except Exception:
                    self.app.logger.error('Temporary trial cleanup will retry.')
        threading.Thread(target=loop, name='vision-trial-cleanup', daemon=True).start()
