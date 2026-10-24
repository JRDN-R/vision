"""Small, account-scoped services for the native Vision iOS companion.

Google credentials live only in a bounded, short-lived PKCE rendezvous. Native
transfers use a separate, hashed capability for exactly one owned Vortex job.
The VTX1 framing keeps a single background URLSession transfer alive while the
existing durable Vortex worker prepares its output; it never prefixes a movie
with fake media bytes. The native client must unpack and validate the envelope.
"""
from __future__ import annotations

import base64
from collections import deque
import hashlib
import hmac
import json
import math
import mimetypes
import re
import secrets
import threading
import time
from urllib.parse import urlencode

from flask import Response, g, jsonify, request
import requests

WEB_ORIGIN = 'https://jrdn-r.github.io'
SIGNIN_PAGE = WEB_ORIGIN + '/vision/ios-signin.html'
AUTH_PREFIX = '/api/ios/auth/'
TRANSFER_ROUTE = re.compile(r'^/api/ios/vortex/([0-9a-f]{24})/transfer$')
URLSAFE = re.compile(r'^[A-Za-z0-9_-]{43}$')
STATE = re.compile(r'^[A-Za-z0-9_-]{32,128}$')
VERIFIER = re.compile(r'^[A-Za-z0-9._~-]{43,128}$')
TRANSFER_TOKEN = re.compile(r'^vtf_[A-Za-z0-9_-]{43}$')
AUTH_START_SECONDS = 300
AUTH_CODE_SECONDS = 120
TRANSFER_SECONDS = 24 * 3600
TRANSFER_WAIT_SECONDS = 2 * 3600
HEARTBEAT_SECONDS = 15
MAX_PENDING_AUTH = 256
MAX_TRANSFERS = 2
MAX_ATTEMPTS = 32
MAX_FRAME_BYTES = 8192
MAX_FILE_BYTES = 2 * 1024 ** 3


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def pkce_challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')


def public_request():
    """Exact public bootstrap/transfer routes, never a whole /api/ios exemption."""
    return ((request.method == 'POST' and request.path in (AUTH_PREFIX + 'start', AUTH_PREFIX + 'exchange'))
            or (request.method == 'GET' and TRANSFER_ROUTE.fullmatch(request.path) is not None))


class IOSCompanion:
    def __init__(self, app, connect_db, api_error, vortex, accounts):
        self.app, self.db, self.Error = app, connect_db, api_error
        self.vortex, self.accounts = vortex, accounts
        self.lock = threading.RLock()
        self.pending, self.limits, self.active = {}, {}, {}
        self.wait = threading.Event()
        self.auth_thread = None
        self.register_routes()

    def start(self):
        if self.auth_thread and self.auth_thread.is_alive():
            return
        self.wait.clear()
        def sweep_auth():
            while not self.wait.wait(15):
                with self.lock:
                    self._prune_auth()
            with self.lock:
                self.pending.clear()
        self.auth_thread = threading.Thread(target=sweep_auth, name='vision-ios-auth-expiry', daemon=True)
        self.auth_thread.start()

    def initialize(self):
        with self.lock:
            self.pending.clear()
            self.limits.clear()
            self.active.clear()
        self.wait.clear()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS ios_transfer_tokens (
                    token_hash TEXT PRIMARY KEY, job_id TEXT NOT NULL, uid TEXT NOT NULL,
                    expires_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS ios_transfer_owner ON ios_transfer_tokens(uid,expires_at);
                CREATE TABLE IF NOT EXISTS ios_transfer_leases (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, uid TEXT NOT NULL, expires_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS ios_transfer_job ON ios_transfer_leases(job_id,expires_at);
            ''')
            # initialize runs once at process startup: no previous process can
            # still own an HTTP stream. Transfer tokens remain valid for retries.
            db.execute('DELETE FROM ios_transfer_leases')
            db.execute('DELETE FROM ios_transfer_tokens WHERE expires_at<=?', (time.time(),))

    def source_leased(self, db, job_id):
        return db.execute('SELECT 1 FROM ios_transfer_leases WHERE job_id=? AND expires_at>?',
                          (job_id, time.time())).fetchone() is not None

    def _origin(self, required=False):
        origin = request.headers.get('Origin')
        if (required and origin != WEB_ORIGIN) or (origin is not None and origin != WEB_ORIGIN):
            raise self.Error('Open this connection from the official Vision iOS sign-in page.', 403)

    def _body(self, allowed):
        request.max_content_length = 24 * 1024
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != set(allowed):
            raise self.Error('Invalid iOS connection request.')
        return body

    def _limit(self, operation, key, maximum=30):
        now = time.monotonic()
        bucket_key = operation + ':' + digest(str(key))
        with self.lock:
            for old in [k for k, values in self.limits.items() if not values or values[-1] <= now - 60]:
                self.limits.pop(old, None)
            if len(self.limits) >= 2048 and bucket_key not in self.limits:
                raise self.Error('Too many connection attempts. Try again shortly.', 429)
            values = self.limits.setdefault(bucket_key, deque())
            while values and values[0] <= now - 60:
                values.popleft()
            if len(values) >= maximum:
                raise self.Error('Too many connection attempts. Try again in a minute.', 429)
            values.append(now)

    def _prune_auth(self):
        now = time.time()
        for ident in [key for key, value in self.pending.items() if value['expires'] <= now]:
            self.pending.pop(ident, None)

    def _pending(self, body):
        ident, state = body.get('id'), body.get('state')
        if not isinstance(ident, str) or not URLSAFE.fullmatch(ident) or not isinstance(state, str) or not STATE.fullmatch(state):
            raise self.Error('This iOS sign-in connection is invalid or expired.', 401)
        self._prune_auth()
        entry = self.pending.get(ident)
        if not entry or not hmac.compare_digest(entry['state'], state):
            raise self.Error('This iOS sign-in connection is invalid or expired.', 401)
        return ident, entry

    def _same_google_account(self, token, claims):
        if not isinstance(token, str) or not 16 <= len(token) <= 16384 or re.search(r'[\x00-\x20\x7f]', token):
            raise self.Error('Google returned an invalid credential.')
        firebase = claims.get('firebase', {})
        google_ids = firebase.get('identities', {}).get('google.com', [])
        if firebase.get('sign_in_provider') != 'google.com' or not isinstance(google_ids, list) or not google_ids:
            raise self.Error('Use Google to approve this iOS connection.', 403)
        try:
            response = requests.get('https://openidconnect.googleapis.com/v1/userinfo',
                                    headers={'Authorization': 'Bearer ' + token},
                                    timeout=(5, 10), allow_redirects=False)
            try:
                if response.status_code != 200:
                    raise ValueError()
                value = response.json()
                subject = value.get('sub') if isinstance(value, dict) else None
                if not isinstance(subject, str) or not any(isinstance(item, str) and hmac.compare_digest(item, subject) for item in google_ids):
                    raise ValueError()
            finally:
                response.close()
        except (requests.RequestException, ValueError, TypeError):
            # Never report upstream bodies, headers, or temporary credentials.
            raise self.Error('The Google credential does not match the signed-in Vision account. Start sign-in again.', 401) from None

    def _frame(self, value):
        data = json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\n'
        if len(data) > MAX_FRAME_BYTES:
            raise ValueError('Transfer metadata exceeded the protocol limit')
        return data

    def _progress(self, row):
        progress = row.get('progress')
        if isinstance(progress, bool) or not isinstance(progress, (int, float)) or not math.isfinite(progress):
            progress = None
        else:
            progress = min(100, max(0, progress))
        phase = str(row.get('phase') or 'Preparing on FUPCJ Server')[:500]
        return self._frame(dict(type='progress', phase=phase, message=phase, progress=progress))

    def _transfer_error(self, message):
        return self._frame(dict(type='error', message=str(message)[:1000]))

    def _lease(self, job_id, token):
        if not TRANSFER_TOKEN.fullmatch(token):
            raise self.Error('This background transfer authorization has expired. Reopen Vision to retry.', 401)
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            record = db.execute('SELECT * FROM ios_transfer_tokens WHERE token_hash=? AND job_id=? AND expires_at>?',
                                (digest(token), job_id, time.time())).fetchone()
            if record is None or record['attempts'] >= MAX_ATTEMPTS:
                raise self.Error('This background transfer authorization has expired. Reopen Vision to retry.', 401)
            uid = record['uid']
            if len(self.active) >= MAX_TRANSFERS or uid in self.active.values():
                return None
            row = db.execute('SELECT * FROM vortex_jobs WHERE id=? AND uid=? AND delete_requested=0', (job_id, uid)).fetchone()
            if row is None or row['kind'] != 'download' or row['cancel_requested'] or row['status'] not in ('queued', 'processing', 'complete'):
                raise self.Error('This Vortex transfer is no longer available.', 410)
            if row['status'] == 'complete' and (not row['expires_at'] or row['expires_at'] <= time.time()):
                raise self.Error('This Vortex file has expired.', 410)
            lease_id = secrets.token_hex(16)
            db.execute('UPDATE ios_transfer_tokens SET attempts=attempts+1 WHERE token_hash=?', (digest(token),))
            db.execute('INSERT INTO ios_transfer_leases VALUES(?,?,?,?)',
                       (lease_id, job_id, uid, time.time() + TRANSFER_SECONDS))
            self.active[lease_id] = uid
        if not self.accounts.begin_request(uid):
            self._release(lease_id, uid, account=False)
            raise self.Error('This Vision account has been removed.', 403, 'account-removed')
        return lease_id, uid

    def _release(self, lease_id, uid, account=True):
        with self.lock:
            if lease_id not in self.active:
                return
            self.active.pop(lease_id, None)
            with self.db() as db:
                db.execute('DELETE FROM ios_transfer_leases WHERE id=?', (lease_id,))
        if account:
            self.accounts.end_request(uid)

    def _stream(self, job_id, lease_id, uid):
        deadline = time.monotonic() + TRANSFER_WAIT_SECONDS
        file_started = False
        try:
            yield b'VTX1\n'
            while not self.wait.is_set():
                if self.accounts.blocked(uid):
                    yield self._transfer_error('This Vision account is unavailable.')
                    return
                try:
                    row = self.vortex.row(job_id, uid)
                except self.Error:
                    yield self._transfer_error('This Vortex job is unavailable.')
                    return
                if row['cancel_requested'] or row['status'] in ('error', 'cancelled', 'expired'):
                    yield self._transfer_error(row.get('error') or 'This Vortex operation stopped. Reopen Vision to retry.')
                    return
                if row['status'] == 'complete':
                    path = self.vortex.output_path(row)
                    size = path.stat().st_size
                    if size <= 0 or size > MAX_FILE_BYTES or size != row['output_size']:
                        yield self._transfer_error('The completed media file failed validation. Retry in Vision.')
                        return
                    with path.open('rb') as source:
                        metadata = dict(type='file', filename=self.vortex.download_name(row),
                                        mimeType=mimetypes.guess_type(path.name)[0] or 'application/octet-stream', size=size)
                        yield self._frame(metadata)
                        file_started = True
                        remaining = size
                        while remaining and not self.wait.is_set():
                            chunk = source.read(min(256 * 1024, remaining))
                            if not chunk:
                                return  # The native client rejects truncated files.
                            remaining -= len(chunk)
                            yield chunk
                    return
                if time.monotonic() >= deadline:
                    yield self._transfer_error('FUPCJ Server is still preparing this media. Reopen Vision to resume the transfer.')
                    return
                yield self._progress(row)
                self.wait.wait(HEARTBEAT_SECONDS)
            if not file_started:
                yield self._transfer_error('FUPCJ Server is restarting. Reopen Vision to resume.')
        except (self.Error, OSError, ValueError):
            if not file_started:
                yield self._transfer_error('The transfer was interrupted. Reopen Vision to resume.')
        finally:
            self._release(lease_id, uid)

    def register_routes(self):
        @self.app.post(AUTH_PREFIX + 'start')
        def ios_auth_start():
            self._origin()
            self._limit('auth-start-global', 'global', 60)
            self._limit('auth-start-ip', request.remote_addr, 12)
            body = self._body(('challenge', 'state'))
            if not isinstance(body['challenge'], str) or not URLSAFE.fullmatch(body['challenge']) or not isinstance(body['state'], str) or not STATE.fullmatch(body['state']):
                raise self.Error('Use an S256 PKCE challenge and a random state for iOS sign-in.')
            if not self.app.config.get('FIREBASE_IDENTITY'):
                raise self.Error('Enable account sign-in on FUPCJ Server first.', 503)
            with self.lock:
                self._prune_auth()
                if len(self.pending) >= MAX_PENDING_AUTH:
                    raise self.Error('The sign-in service is busy. Try again shortly.', 503)
                ident, expiry = secrets.token_urlsafe(32), time.time() + AUTH_START_SECONDS
                self.pending[ident] = dict(challenge=body['challenge'], state=body['state'], expires=expiry, attempts=0)
            return jsonify(id=ident, expiresAt=expiry,
                           authURL=SIGNIN_PAGE + '#' + urlencode(dict(id=ident, state=body['state']))), 201

        @self.app.post(AUTH_PREFIX + 'approve')
        def ios_auth_approve():
            self._origin(required=True)
            if getattr(g, 'auth_kind', '') != 'firebase-google':
                raise self.Error('Sign in to connect Vision on iOS.', 401)
            self._limit('auth-approve', g.uid, 12)
            body = self._body(('id', 'state', 'googleAccessToken'))
            with self.lock:
                ident, entry = self._pending(body)
                if 'code_hash' in entry or entry.get('approving'):
                    raise self.Error('This iOS sign-in connection has already been approved.', 409)
                entry['approving'] = True
            try:
                self._same_google_account(body['googleAccessToken'], getattr(g, 'identity_claims', {}))
                with self.lock:
                    _, current = self._pending(body)
                    if current is not entry:
                        raise self.Error('This iOS connection expired. Start sign-in again.', 401)
                    code = secrets.token_urlsafe(32)
                    entry.update(code_hash=digest(code), google_access=body['googleAccessToken'],
                                 uid=g.uid.removeprefix('firebase:'), expires=min(entry['expires'], time.time() + AUTH_CODE_SECONDS))
                    entry.pop('approving', None)
                    return jsonify(code=code, expiresAt=entry['expires'])
            finally:
                with self.lock:
                    if self.pending.get(ident) is entry:
                        entry.pop('approving', None)

        @self.app.post(AUTH_PREFIX + 'exchange')
        def ios_auth_exchange():
            self._origin()
            self._limit('auth-exchange', request.remote_addr, 60)
            body = self._body(('id', 'state', 'code', 'verifier'))
            if not isinstance(body['code'], str) or not URLSAFE.fullmatch(body['code']) or not isinstance(body['verifier'], str) or not VERIFIER.fullmatch(body['verifier']):
                raise self.Error('This iOS sign-in response is invalid.', 401)
            with self.lock:
                ident, entry = self._pending(body)
                valid_code = hmac.compare_digest(entry.get('code_hash', ''), digest(body['code']))
                valid_verifier = hmac.compare_digest(entry['challenge'], pkce_challenge(body['verifier']))
                if not valid_code or not valid_verifier:
                    entry['attempts'] += 1
                    if entry['attempts'] >= 5:
                        self.pending.pop(ident, None)
                    raise self.Error('This iOS sign-in response is invalid.', 401)
                if self.accounts.blocked('firebase:' + entry['uid']):
                    self.pending.pop(ident, None)
                    raise self.Error('This Vision account has been removed.', 403, 'account-removed')
                self.pending.pop(ident, None)
                return jsonify(googleAccessToken=entry['google_access'], uid=entry['uid'], expiresAt=entry['expires'])

        @self.app.post('/api/ios/vortex/<job_id>/transfer-ticket')
        def ios_transfer_ticket(job_id):
            self._origin()
            uid = self.vortex._uid()
            self._limit('transfer-ticket', uid, 30)
            self._body(())
            row = self.vortex.row(job_id, uid)
            if row['kind'] != 'download' or row['cancel_requested'] or row['status'] not in ('queued', 'processing', 'complete'):
                raise self.Error('This Vortex transfer is unavailable.', 410)
            if row['status'] == 'complete' and (not row['expires_at'] or row['expires_at'] <= time.time()):
                raise self.Error('This Vortex file has expired.', 410)
            token, expiry = 'vtf_' + secrets.token_urlsafe(32), time.time() + TRANSFER_SECONDS
            if row['expires_at']:
                expiry = min(expiry, row['expires_at'])
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM ios_transfer_tokens WHERE expires_at<=?', (time.time(),))
                if db.execute('SELECT COUNT(*) FROM ios_transfer_tokens WHERE uid=?', (uid,)).fetchone()[0] >= 64:
                    raise self.Error('Too many background transfer authorizations. Retry an existing transfer.', 429)
                db.execute('INSERT INTO ios_transfer_tokens VALUES(?,?,?,?,0)', (digest(token), job_id, uid, expiry))
            return jsonify(url=self.app.config.get('BACKEND_URL', '').rstrip('/') + '/api/ios/vortex/' + job_id + '/transfer',
                           token=token, expiresAt=expiry, protocol='VTX1')

        @self.app.get('/api/ios/vortex/<job_id>/transfer')
        def ios_transfer(job_id):
            self._origin()
            if not re.fullmatch(r'[0-9a-f]{24}', job_id) or request.method != 'GET':
                raise self.Error('Invalid background transfer.', 404)
            if request.headers.get('Range'):
                raise self.Error('Restart this transfer from its beginning.', 416)
            lease = self._lease(job_id, request.headers.get('X-Vision-Transfer', ''))
            if lease is None:
                response = jsonify(error='Another iOS transfer is active. Vision will retry when you reopen Downloads.')
                response.status_code = 503
                response.headers['Retry-After'] = '30'
                return response
            lease_id, uid = lease
            response = Response(self._stream(job_id, lease_id, uid), mimetype='application/vnd.vision.vortex-transfer')
            response.headers.update({'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no',
                                     'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff',
                                     'Content-Disposition': 'attachment; filename="' + job_id + '.vtx"'})
            response.call_on_close(lambda: self._release(lease_id, uid))
            return response
