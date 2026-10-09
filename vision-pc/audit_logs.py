"""Private, bounded administrative usage logs; never records request content."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time

MAX_EVENTS = 2000


def clean(value, limit=200):
    return re.sub(r'[\x00-\x1f\x7f-\x9f\u2028-\u202e\u2066-\u2069]', ' ', str(value or ''))[:limit].strip()


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='seconds')


class AuditLogs:
    def __init__(self, directory, connect_db, logger):
        self.directory, self.db, self.logger = Path(directory), connect_db, logger
        self.lock, self.seen = threading.RLock(), {}

    def initialize(self):
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS audit_users (
                    uid TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL,
                    first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                    auth_time REAL NOT NULL, sign_in_sightings INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL,
                    created_at REAL NOT NULL, event TEXT NOT NULL, details TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS audit_events_uid_id ON audit_events(uid,id);
                CREATE TABLE IF NOT EXISTS audit_signins (
                    uid TEXT NOT NULL, auth_time REAL NOT NULL, PRIMARY KEY(uid,auth_time));
                CREATE TABLE IF NOT EXISTS audit_account_metadata (
                    uid TEXT PRIMARY KEY, provider TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit_user_apps (
                    uid TEXT NOT NULL, app TEXT NOT NULL, first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL, PRIMARY KEY(uid,app));
            ''')
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.lock:
            try:
                self._index()
            except OSError as error:
                self.logger.warning('User index could not be written (%s).', type(error).__name__)

    def _write(self, path, value):
        temporary = path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8', newline='\n') as out:
            out.write(value)
            out.flush()
            os.fsync(out.fileno())
        for attempt in range(3):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(.02)

    def _filename(self, uid):
        return 'User-' + hashlib.sha256(uid.encode()).hexdigest() + '.txt'

    def _index(self):
        with self.db() as db:
            users = db.execute('SELECT * FROM audit_users ORDER BY email,name,uid').fetchall()
        lines = ['FUPCJ Server | Accounts', 'Times are UTC. Sign-in sightings count distinct verified authentication sessions.',
                 'Name\tEmail\tFirst seen\tLast seen\tSign-in sightings\tUsage log']
        for u in users:
            lines.append('\t'.join([u['name'], u['email'], stamp(u['first_seen']), stamp(u['last_seen']),
                                     str(u['sign_in_sightings']), self._filename(u['uid'])]))
        self._write(self.directory / 'Users.txt', '\n'.join(lines) + '\n')

    def _user(self, uid):
        with self.db() as db:
            user = db.execute('SELECT * FROM audit_users WHERE uid=?', (uid,)).fetchone()
            events = db.execute('SELECT created_at,event,details FROM audit_events WHERE uid=? ORDER BY id DESC LIMIT ?', (uid, MAX_EVENTS)).fetchall()
        if user is None:
            return
        lines = ['FUPCJ Server | User activity', 'Name: ' + user['name'], 'Email: ' + user['email'],
                 'First seen: ' + stamp(user['first_seen']), 'Last seen: ' + stamp(user['last_seen']),
                 'Sign-in sightings: ' + str(user['sign_in_sightings']),
                 'Times are UTC. Duration is elapsed wall time, not CPU utilization.',
                 'Request bytes include protocol/body overhead. Up to 2,000 recent events are retained.',
                 'No passwords, API keys, prompts, transcripts, file contents, or filenames are recorded.', '']
        for row in reversed(events):
            details = json.loads(row['details'])
            summary = ' '.join(k + '=' + str(v) for k, v in details.items())
            lines.append(stamp(row['created_at']) + ' | ' + row['event'] + (' | ' + summary if summary else ''))
        self._write(self.directory / self._filename(uid), '\n'.join(lines) + '\n')

    def identity(self, uid, claims, app='vision'):
        name, email, auth_time = clean(claims.get('name')), clean(claims.get('email')), claims['auth_time']
        provider = {'google.com': 'google', 'password': 'password'}.get(
            (claims.get('firebase') or {}).get('sign_in_provider'), 'unknown')
        app = app if app in ('vision', 'vortex') else ''
        now = time.time()
        try:
            with self.lock:
                cache_key = (uid, auth_time, app)
                previous = self.seen.get(cache_key)
                if previous and previous[1:] == (name, email, provider) and now - previous[0] < 60:
                    return
                with self.db() as db:
                    new_sign_in = bool(db.execute('INSERT OR IGNORE INTO audit_signins VALUES(?,?)', (uid, auth_time)).rowcount)
                    db.execute('''INSERT INTO audit_users VALUES(?,?,?,?,?,?,1) ON CONFLICT(uid) DO UPDATE SET
                        name=excluded.name,email=excluded.email,last_seen=excluded.last_seen,auth_time=excluded.auth_time,
                        sign_in_sightings=sign_in_sightings+?''',
                        (uid, name, email, now, now, auth_time, int(new_sign_in)))
                    db.execute('INSERT OR REPLACE INTO audit_account_metadata VALUES(?,?)', (uid, provider))
                    first_app = False
                    if app:
                        first_app = bool(db.execute('INSERT OR IGNORE INTO audit_user_apps VALUES(?,?,?,?)',
                                                   (uid, app, now, now)).rowcount)
                        db.execute('UPDATE audit_user_apps SET last_seen=? WHERE uid=? AND app=?', (now, uid, app))
                if len(self.seen) >= 10000:
                    self.seen.clear()
                self.seen[cache_key] = (now, name, email, provider)
                if new_sign_in:
                    self._event(uid, 'google_sign_in' if provider == 'google' else 'account_sign_in',
                                {'authProvider': provider, **({'app': app} if app else {})})
                elif first_app and app == 'vortex':
                    self._event(uid, 'app_first_seen', {'app': app, 'authProvider': provider})
                self._index()
                self._user(uid)
        except Exception as error:
            self.logger.warning('User usage log could not be updated (%s).', type(error).__name__)

    def _event(self, uid, kind, details):
        with self.db() as db:
            db.execute('INSERT INTO audit_events(uid,created_at,event,details) VALUES(?,?,?,?)',
                       (uid, time.time(), kind, json.dumps(details, separators=(',', ':'))))
            db.execute('DELETE FROM audit_events WHERE uid=? AND id NOT IN (SELECT id FROM audit_events WHERE uid=? ORDER BY id DESC LIMIT ?)',
                       (uid, uid, MAX_EVENTS))

    def event(self, uid, kind, **fields):
        if not uid or not uid.startswith('firebase:') or not re.fullmatch(r'[a-z_]{1,40}', kind):
            return
        details = {}
        for name in ('requestBytes', 'uploadedBytes', 'outputBytes', 'requestWallSeconds', 'processingWallSeconds', 'runElapsedSeconds', 'httpStatus'):
            value = fields.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                details[name] = round(value, 3)
        for name in ('jobType', 'outcome', 'app', 'authProvider'):
            value = fields.get(name)
            if isinstance(value, str) and re.fullmatch(r'[a-z_]{1,32}', value):
                details[name] = value
        try:
            with self.lock:
                self._event(uid, kind, details)
                self._user(uid)
        except Exception as error:
            self.logger.warning('User usage event could not be recorded (%s).', type(error).__name__)

    def project_event(self, project_id, kind, **fields):
        try:
            with self.db() as db:
                row = db.execute('SELECT owner_uid FROM projects WHERE id=?', (project_id,)).fetchone()
            if row:
                self.event(row['owner_uid'], kind, **fields)
        except Exception as error:
            self.logger.warning('Project usage event could not be recorded (%s).', type(error).__name__)

    def request_event(self, uid, request, status, elapsed):
        # Route patterns are fixed; user paths, query strings, headers and bodies
        # are never emitted. Polling GETs produce no activity events.
        path, method = request.path, request.method
        if path == '/api/vortex/jobs' and method == 'POST':
            kind = 'vortex_submitted'
        elif path.startswith('/api/vortex/') and path.endswith('/ticket'):
            kind = 'vortex_download_link_created'
        elif path.startswith('/api/vortex/') and method == 'DELETE':
            kind = 'vortex_removed'
        elif path.startswith('/api/vortex/') and path.endswith('/cancel'):
            kind = 'vortex_cancel_requested'
        elif path == '/api/account/openai-key':
            kind = 'api_key_saved' if method == 'PUT' else 'api_key_removed'
        elif re.fullmatch(r'/api/projects/[^/]+', path):
            kind = 'project_saved'
        elif path.endswith('/claim'):
            kind = 'project_claimed'
        elif path.endswith('/transcriptions'):
            kind = 'audio_submitted'
        elif path.endswith('/media'):
            kind = 'video_uploaded'
        elif path.endswith('/runs') or path == '/api/openai/run':
            kind = 'run_submitted'
        elif path == '/api/youtube':
            kind = 'youtube_submitted'
        elif method == 'DELETE' or path.endswith('/cancel'):
            kind = 'job_removed_or_cancelled'
        else:
            kind = 'request_changed'
        self.event(uid, kind, httpStatus=status, requestBytes=request.content_length or 0,
                   requestWallSeconds=elapsed, outcome='accepted' if status < 400 else 'rejected',
                   app='vortex' if path.startswith('/api/vortex/') else 'vision')
