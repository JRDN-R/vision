"""Optional APNs updates for already-started iOS Vortex Live Activities.

Disabled by default. No push-to-start or background execution entitlement is
implied by this service. Apple developer signing keys stay on this Windows PC;
ActivityKit destination tokens are DPAPI encrypted and scoped to account jobs.
"""
from __future__ import annotations

from collections import deque
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import threading
import time

from flask import g, jsonify, request

HEX_TOKEN = re.compile(r'^(?:[0-9a-fA-F]{2}){16,256}$')
IDENTIFIER = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
MAX_LIFETIME = 8 * 3600
MIN_INTERVAL = 15
HEARTBEAT_INTERVAL = 60


class _APNsLogFilter(logging.Filter):
    def filter(self, record):
        # httpx emits request URLs at INFO. Do not allow a change to the host's
        # root log level to turn ActivityKit destinations into plaintext logs.
        message = record.getMessage()
        return not any(part in message for part in ('push.apple.com', '/3/device/', 'authorization', 'Authorization'))


def _protect_http_logs():
    for name in ('httpx', 'httpcore.http2', 'httpcore.http11', 'httpcore.connection'):
        logger = logging.getLogger(name)
        if not any(isinstance(item, _APNsLogFilter) for item in logger.filters):
            logger.addFilter(_APNsLogFilter())


class IOSLiveActivities:
    def __init__(self, app, connect_db, api_error, vortex, accounts, encrypt, decrypt):
        self.app, self.db, self.Error = app, connect_db, api_error
        self.vortex, self.accounts, self.encrypt, self.decrypt = vortex, accounts, encrypt, decrypt
        self.lock, self.worker_lock = threading.RLock(), threading.Lock()
        self.stop, self.wake = threading.Event(), threading.Event()
        self.thread, self.client, self.settings = None, None, None
        self.enabled, self.ready, self.problem = False, False, ''
        self.private_key, self.provider_token, self.provider_created = None, '', 0
        self.registration_limits = {}
        self.register_routes()

    def initialize(self, config, config_path):
        self.close()
        self.stop.clear()
        self.wake.clear()
        self.settings, self.private_key = None, None
        self.provider_token, self.provider_created = '', 0
        self.enabled, self.ready, self.problem = False, False, ''
        self.registration_limits.clear()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS ios_live_activities (
                    uid TEXT NOT NULL, activity_id TEXT NOT NULL, job_id TEXT NOT NULL,
                    record_id TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL, token_cipher BLOB NOT NULL,
                    expires_at REAL NOT NULL, last_sent REAL NOT NULL DEFAULT 0,
                    last_fingerprint TEXT NOT NULL DEFAULT '', next_attempt REAL NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(uid,activity_id));
                CREATE INDEX IF NOT EXISTS ios_activity_expiry ON ios_live_activities(expires_at,done);
            ''')
            db.execute('DELETE FROM ios_live_activities WHERE expires_at<=?', (time.time(),))
        raw = config.get('iosAPNs', {})
        if not isinstance(raw, dict) or raw.get('enabled') is not True:
            return
        self.enabled = True
        try:
            allowed = {'enabled', 'teamId', 'keyId', 'keyPath', 'bundleId', 'environment'}
            if set(raw) - allowed:
                raise ValueError('Unknown APNs configuration field')
            for key in ('teamId', 'keyId'):
                if not isinstance(raw.get(key), str) or not re.fullmatch(r'[A-Z0-9]{10}', raw[key]):
                    raise ValueError('Invalid Apple identifier')
            if not isinstance(raw.get('bundleId'), str) or not re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', raw['bundleId']) or len(raw['bundleId']) > 200:
                raise ValueError('Invalid bundle identifier')
            if raw.get('environment') not in ('sandbox', 'production'):
                raise ValueError('Invalid APNs environment')
            if not isinstance(raw.get('keyPath'), str) or not raw['keyPath']:
                raise ValueError('Missing Apple key path')
            key_path = Path(raw['keyPath'])
            if not key_path.is_absolute():
                key_path = Path(config_path).resolve().parent / key_path
            if not key_path.is_file() or key_path.stat().st_size > 16384:
                raise ValueError('Invalid Apple key file')
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            from cryptography.hazmat.primitives.asymmetric import ec
            private_key = load_pem_private_key(key_path.read_bytes(), password=None)
            if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not isinstance(private_key.curve, ec.SECP256R1):
                raise ValueError('APNs requires an ES256 key')
            # Optional dependencies and DPAPI are checked only when enabled.
            import httpx
            self.encrypt('vision-ios-apns-config-check')
            _protect_http_logs()
            self.client = httpx.Client(http2=True, timeout=10, follow_redirects=False)
            self.private_key = private_key
            self.settings = {key: raw[key] for key in ('teamId', 'keyId', 'bundleId', 'environment')}
            self.ready = True
        except Exception:
            self.problem = 'APNs setup is incomplete. Check the local Apple key, config and requirements-ios.txt on FUPCJ Server.'
            self.app.logger.warning('Optional iOS Live Activity push setup is incomplete. Other Vision services remain available.')
            if self.client:
                self.client.close()
                self.client = None

    def capability(self):
        return dict(enabled=self.enabled, ready=self.ready, pushToStart=False, requiresPaidAppleDeveloper=True,
                    message=self.problem or ('Server updates are available for Live Activities started in Vision.' if self.ready
                                             else 'Local Live Activities work without APNs. Server updates need optional Apple Developer setup.'))

    def _authorize(self):
        origin = request.headers.get('Origin')
        if origin is not None and origin != 'https://jrdn-r.github.io':
            raise self.Error('Use the official Vision app.', 403)
        return self.vortex._uid()

    def _limit(self, uid):
        now = time.monotonic()
        with self.lock:
            for key in [key for key, values in self.registration_limits.items() if not values or values[-1] <= now - 60]:
                self.registration_limits.pop(key, None)
            values = self.registration_limits.setdefault(uid, deque())
            while values and values[0] <= now - 60:
                values.popleft()
            if len(values) >= 60 or len(self.registration_limits) > 2048:
                raise self.Error('Too many Live Activity updates. Try again shortly.', 429)
            values.append(now)

    def register_routes(self):
        @self.app.get('/api/ios/capabilities')
        def ios_capabilities():
            self._authorize()
            return jsonify(version=1, systemBrowserSignIn=True, framedBackgroundTransfer=True,
                           transferProtocol='VTX1', maxConcurrentTransfers=2,
                           maxConcurrentTransfersPerAccount=1, liveActivityPush=self.capability())

        @self.app.post('/api/ios/live-activities')
        def ios_live_activity_register():
            uid = self._authorize()
            if not self.ready:
                raise self.Error(self.capability()['message'], 503, 'ios-apns-unavailable')
            self._limit(uid)
            request.max_content_length = 4096
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or set(body) != {'jobID', 'recordID', 'activityID', 'token'}:
                raise self.Error('Invalid Live Activity registration.')
            job_id, record_id, activity_id, token = (body[key] for key in ('jobID', 'recordID', 'activityID', 'token'))
            if (not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{24}', job_id)
                    or not isinstance(record_id, str) or not IDENTIFIER.fullmatch(record_id)
                    or not isinstance(activity_id, str) or not IDENTIFIER.fullmatch(activity_id)
                    or not isinstance(token, str) or not HEX_TOKEN.fullmatch(token)):
                raise self.Error('Invalid Live Activity identifiers.')
            job = self.vortex.row(job_id, uid)
            if job['kind'] != 'download':
                raise self.Error('Choose a Vortex download for this activity.')
            token = token.lower()
            token_hash = hashlib.sha256(token.encode('ascii')).hexdigest()
            try:
                cipher = self.encrypt(token)
            except Exception:
                raise self.Error('FUPCJ Server could not protect the activity token.', 503) from None
            now, expiry = time.time(), time.time() + MAX_LIFETIME
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM ios_live_activities WHERE expires_at<=? OR (done=1 AND last_sent>0 AND last_sent<?)', (now, now - 90))
                existing = db.execute('SELECT * FROM ios_live_activities WHERE uid=? AND activity_id=?', (uid, activity_id)).fetchone()
                token_owner = db.execute('SELECT uid,activity_id FROM ios_live_activities WHERE token_hash=?', (token_hash,)).fetchone()
                if token_owner and (token_owner['uid'] != uid or token_owner['activity_id'] != activity_id):
                    raise self.Error('This Live Activity token is already registered.', 409)
                if existing:
                    if existing['job_id'] != job_id or existing['record_id'] != record_id:
                        raise self.Error('This Live Activity already belongs to another download.', 409)
                    expiry = existing['expires_at']
                    if existing['token_hash'] != token_hash:
                        db.execute('UPDATE ios_live_activities SET token_hash=?,token_cipher=?,next_attempt=0,failures=0,last_fingerprint=?,done=0 WHERE uid=? AND activity_id=?',
                                   (token_hash, cipher, '', uid, activity_id))
                else:
                    if db.execute('SELECT COUNT(*) FROM ios_live_activities WHERE uid=?', (uid,)).fetchone()[0] >= 10:
                        raise self.Error('Ten Live Activities are already registered. Close one before starting another.', 429)
                    if db.execute('SELECT COUNT(*) FROM ios_live_activities').fetchone()[0] >= 1000:
                        raise self.Error('Live Activity push updates are at capacity.', 503)
                    db.execute('INSERT INTO ios_live_activities(uid,activity_id,job_id,record_id,token_hash,token_cipher,expires_at) VALUES(?,?,?,?,?,?,?)',
                               (uid, activity_id, job_id, record_id, token_hash, cipher, expiry))
            self.wake.set()
            return jsonify(registered=True, expiresAt=expiry)

        @self.app.delete('/api/ios/live-activities/<activity_id>')
        def ios_live_activity_delete(activity_id):
            uid = self._authorize()
            if not IDENTIFIER.fullmatch(activity_id):
                raise self.Error('Invalid Live Activity identifier.')
            with self.db() as db:
                db.execute('DELETE FROM ios_live_activities WHERE uid=? AND activity_id=?', (uid, activity_id))
            return jsonify(removed=True)

    def _provider(self, now):
        # Apple rejects provider JWTs older than one hour and discourages
        # rotating more often than every 20 minutes. Reuse for 50 minutes.
        if not self.provider_token or now - self.provider_created >= 50 * 60:
            import jwt
            self.provider_token = jwt.encode({'iss': self.settings['teamId'], 'iat': int(now)}, self.private_key,
                                             algorithm='ES256', headers={'kid': self.settings['keyId']})
            self.provider_created = now
        return self.provider_token

    def _payload(self, registration, now):
        try:
            row = self.vortex.row(registration['job_id'], registration['uid'])
            media = json.loads(row.get('media_json') or '{}')
            title = str(media.get('title') or 'Vortex download')[:100]
            status = row['status']
            phase = str(row.get('phase') or 'Preparing on FUPCJ Server')[:160]
            progress = row.get('progress')
            if isinstance(progress, bool) or not isinstance(progress, (int, float)) or not math.isfinite(progress):
                progress = None
            else:
                progress = min(1, max(0, progress / 100))
            if status == 'complete':
                phase, native_status, progress = 'Ready for iPhone', 'processing', None
            elif status == 'queued':
                native_status = 'queued'
            elif status == 'processing':
                native_status = 'processing'
            elif status == 'cancelled':
                phase, native_status, progress = 'Cancelled', 'cancelled', None
            else:
                phase, native_status, progress = str(row.get('error') or 'Download unavailable')[:160], 'failed', None
            end = status in ('error', 'cancelled', 'expired')
            finished = end or status == 'complete'
        except self.Error:
            title, phase, progress, native_status = 'Vortex download', 'Download unavailable', None, 'failed'
            end = finished = True
        state = dict(title=title, phase=phase, progress=progress, status=native_status, updatedAt=now)
        fingerprint = hashlib.sha256(json.dumps({key: value for key, value in state.items() if key != 'updatedAt'}, sort_keys=True).encode()).hexdigest()
        aps = {'timestamp': int(now), 'event': 'end' if end else 'update', 'content-state': state, 'stale-date': int(now) + 75}
        if end:
            aps['dismissal-date'] = int(now) + 90
        return {'aps': aps}, fingerprint, finished, end

    def process_once(self):
        if not self.ready or not self.worker_lock.acquire(blocking=False):
            return 0
        sent = 0
        try:
            now = time.time()
            with self.db() as db:
                db.execute('DELETE FROM ios_live_activities WHERE expires_at<=? OR (done=1 AND last_sent>0 AND last_sent<?)', (now, now - 90))
                rows = [dict(row) for row in db.execute('SELECT * FROM ios_live_activities WHERE done=0 AND next_attempt<=? ORDER BY last_sent LIMIT 100', (now,))]
            for registration in rows:
                uid, activity_id = registration['uid'], registration['activity_id']
                if self.stop.is_set():
                    break
                if self.accounts.blocked(uid):
                    with self.db() as db:
                        db.execute('DELETE FROM ios_live_activities WHERE uid=? AND activity_id=?', (uid, activity_id))
                    continue
                now = time.time()
                payload, fingerprint, finished, end = self._payload(registration, now)
                elapsed = now - registration['last_sent']
                if not finished and (elapsed < MIN_INTERVAL or (fingerprint == registration['last_fingerprint'] and elapsed < HEARTBEAT_INTERVAL)):
                    continue
                try:
                    token = self.decrypt(registration['token_cipher'])
                    if not HEX_TOKEN.fullmatch(token):
                        raise ValueError('Invalid protected activity token')
                    base = 'https://api.sandbox.push.apple.com' if self.settings['environment'] == 'sandbox' else 'https://api.push.apple.com'
                    headers = {'authorization': 'bearer ' + self._provider(now),
                               'apns-topic': self.settings['bundleId'] + '.push-type.liveactivity',
                               'apns-push-type': 'liveactivity', 'apns-priority': '10' if end else '5',
                               'apns-expiration': str(min(int(registration['expires_at']), int(now) + 90)),
                               'apns-collapse-id': hashlib.sha256(activity_id.encode()).hexdigest(),
                               'content-type': 'application/json'}
                    # Never log HTTP requests/responses: the APNs path contains
                    # a destination token, and Authorization contains a key JWT.
                    response = self.client.post(base + '/3/device/' + token, json=payload, headers=headers)
                    code = response.status_code
                    response.close()
                    if code == 200:
                        with self.db() as db:
                            db.execute('UPDATE ios_live_activities SET last_sent=?,last_fingerprint=?,next_attempt=0,failures=0,done=? WHERE uid=? AND activity_id=? AND token_hash=?',
                                       (now, fingerprint, int(finished), uid, activity_id, registration['token_hash']))
                        sent += 1
                    elif code in (400, 404, 410):
                        with self.db() as db:
                            db.execute('DELETE FROM ios_live_activities WHERE uid=? AND activity_id=? AND token_hash=?',
                                       (uid, activity_id, registration['token_hash']))
                    else:
                        self._retry(registration, now)
                except Exception:
                    # Exceptions from an HTTP client often include the full URL.
                    # Suppress them and retain only bounded retry bookkeeping.
                    self._retry(registration, now)
            return sent
        finally:
            self.worker_lock.release()

    def _retry(self, row, now):
        failures = row['failures'] + 1
        delay = min(300, 15 * (2 ** min(failures, 5)))
        with self.db() as db:
            db.execute('UPDATE ios_live_activities SET failures=?,next_attempt=?,done=? WHERE uid=? AND activity_id=? AND token_hash=?',
                       (failures, now + delay, int(failures >= 10), row['uid'], row['activity_id'], row['token_hash']))

    def start(self):
        if not self.ready or self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        def run():
            while not self.stop.is_set():
                try:
                    self.process_once()
                except Exception:
                    self.app.logger.warning('An iOS Live Activity update was deferred.')
                self.wake.wait(5)
                self.wake.clear()
        self.thread = threading.Thread(target=run, name='vision-ios-live-activities', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=12)
        self.thread = None
        if self.client:
            self.client.close()
            self.client = None

