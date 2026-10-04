"""Account-level Gemini permission and owner-only request usage accounting.

These records never contain audio, prompts, transcript text, or credentials.
The existing activity-admins.json UID allowlist authorizes administrative routes.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time


def public_id(uid):
    return hashlib.sha256(uid.encode('utf-8')).hexdigest()


def clean(value, limit=200):
    return re.sub(r'[\x00-\x1f\x7f-\x9f\u2028-\u202e\u2066-\u2069]', ' ', str(value or ''))[:limit].strip()


@contextmanager
def transaction(connect_db, existing=None):
    if existing is not None:
        yield existing
        return
    db = connect_db()
    try:
        with db:
            yield db
    finally:
        db.close()


class GeminiAccessDenied(Exception):
    def __init__(self, status='pending'):
        self.status = status
        super().__init__('Waiting for Gemini approval' if status in ('pending', 'unrequested') else
                         'Gemini access denied' if status == 'denied' else 'Gemini access revoked')


class GeminiRequestAlreadyRecorded(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__('This Gemini request was already sent. Its result could not be safely recovered; review the request in Vision Status before retrying.')


class GeminiAccess:
    def __init__(self, app, connect_db, wake=None):
        self.app, self.db, self.wake = app, connect_db, wake

    def initialize(self):
        with transaction(self.db) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS gemini_access (
                    uid TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('pending','approved','denied','revoked')),
                    requested_at REAL NOT NULL, decided_at REAL, decided_by TEXT);
                CREATE TABLE IF NOT EXISTS gemini_access_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL,
                    actor_uid TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS gemini_access_status ON gemini_access(status,requested_at);
            ''')

    def status(self, uid, db=None):
        if not isinstance(uid, str) or not uid.startswith('firebase:'):
            return 'denied'
        with transaction(self.db, db) as connection:
            row = connection.execute('SELECT status FROM gemini_access WHERE uid=?', (uid,)).fetchone()
        return row['status'] if row else 'unrequested'

    def request_access(self, uid, db=None):
        """Only verified Google identities may request; a denial is never reset by submission."""
        if not isinstance(uid, str) or not re.fullmatch(r'firebase:[^\s]{1,128}', uid):
            return 'denied'
        with transaction(self.db, db) as connection:
            connection.execute("INSERT OR IGNORE INTO gemini_access(uid,status,requested_at) VALUES(?,'pending',?)",
                               (uid, time.time()))
            return connection.execute('SELECT status FROM gemini_access WHERE uid=?', (uid,)).fetchone()['status']

    def require_approved(self, uid):
        status = self.status(uid)
        if status != 'approved':
            raise GeminiAccessDenied(status)

    def _jobs(self, db, uid, decision):
        # Migrations add requester_uid to the existing queue. The fallback is only
        # for older queue rows and never grants access based on a display name/email.
        columns = {row[1] for row in db.execute('PRAGMA table_info(local_transcriptions)')}
        if 'provider' not in columns:
            return 0
        owner = ('requester_uid=?' if 'requester_uid' in columns else
                 'project_id IN (SELECT id FROM projects WHERE owner_uid=?)')
        now = time.time()
        if decision == 'approved':
            return db.execute("UPDATE local_transcriptions SET status='queued',phase='Waiting for Gemini processing',"
                              "updated_at=? WHERE provider='gemini' AND status='approval_waiting' "
                              'AND cancel_requested=0 AND ' + owner, (now, uid)).rowcount
        phase = 'Gemini access denied' if decision == 'denied' else 'Gemini access revoked'
        db.execute("UPDATE local_transcriptions SET status='approval_waiting',phase=?,updated_at=? "
                   "WHERE provider='gemini' AND status IN ('queued','approval_waiting') AND cancel_requested=0 AND " + owner,
                   (phase, now, uid))
        return 0

    def decide(self, user_id, decision, actor_uid):
        if not isinstance(user_id, str) or not re.fullmatch(r'[0-9a-f]{64}', user_id):
            raise ValueError('Choose a valid Gemini access request.')
        if decision not in ('approved', 'denied', 'revoked'):
            raise ValueError('Choose approved, denied, or revoked.')
        with transaction(self.db) as db:
            db.execute('BEGIN IMMEDIATE')
            row = next((r for r in db.execute('SELECT uid FROM gemini_access') if public_id(r['uid']) == user_id), None)
            if row is None:
                raise LookupError('This Gemini access request no longer exists.')
            uid, now = row['uid'], time.time()
            db.execute('UPDATE gemini_access SET status=?,decided_at=?,decided_by=? WHERE uid=?',
                       (decision, now, actor_uid, uid))
            db.execute('INSERT INTO gemini_access_decisions(uid,actor_uid,status,created_at) VALUES(?,?,?,?)',
                       (uid, actor_uid, decision, now))
            released = self._jobs(db, uid, decision)
            pending = db.execute("SELECT COUNT(*) FROM gemini_access WHERE status='pending'").fetchone()[0]
        if released and self.wake is not None:
            self.wake() if callable(self.wake) else self.wake.set()
        audit = self.app.config.get('AUDIT_LOGS')
        if audit:
            audit.event(uid, 'gemini_access_' + decision, outcome=decision)
        return {'userId': user_id, 'status': decision, 'releasedJobs': released, 'pendingCount': pending}

    def snapshot(self):
        with transaction(self.db) as db:
            rows = db.execute('''SELECT a.*,u.name,u.email FROM gemini_access a
                LEFT JOIN audit_users u ON u.uid=a.uid
                ORDER BY CASE a.status WHEN 'pending' THEN 0 ELSE 1 END,a.requested_at DESC''').fetchall()
            columns = {row[1] for row in db.execute('PRAGMA table_info(local_transcriptions)')}
            waiting = {}
            if {'provider', 'requester_uid'} <= columns:
                waiting = dict(db.execute("SELECT requester_uid,COUNT(*) FROM local_transcriptions WHERE provider='gemini' "
                                          "AND status='approval_waiting' AND cancel_requested=0 GROUP BY requester_uid"))
        requests = [{'userId': public_id(r['uid']), 'name': clean(r['name']), 'email': clean(r['email']),
                     'status': r['status'], 'requestedAt': r['requested_at'], 'decidedAt': r['decided_at'],
                     'waitingJobs': waiting.get(r['uid'], 0)} for r in rows]
        return {'accessRequests': requests, 'pendingCount': sum(r['status'] == 'pending' for r in requests)}


TOKEN_KEYS = {
    'promptTokenCount', 'candidatesTokenCount', 'thoughtsTokenCount', 'totalTokenCount',
    'cachedContentTokenCount', 'toolUsePromptTokenCount', 'promptTokensDetails',
    'cacheTokensDetails', 'candidatesTokensDetails', 'toolUsePromptTokensDetails',
    'total_input_tokens', 'total_output_tokens', 'total_thought_tokens', 'total_tokens',
    'total_cached_tokens', 'total_tool_use_tokens', 'input_tokens_by_modality',
    'output_tokens_by_modality', 'cached_tokens_by_modality', 'input_token_details',
    'output_token_details', 'inputTokens', 'outputTokens', 'thoughtTokens', 'totalTokens',
}
UNSET = object()


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def raw_usage(value):
    """Keep raw measurements and modality breakdowns, not arbitrary response content."""
    if not isinstance(value, dict):
        return {}
    if isinstance(value.get('usageMetadata'), dict):
        value = value['usageMetadata']
    elif isinstance(value.get('usage'), dict):
        value = value['usage']

    def item(raw, depth=0):
        if depth > 4:
            return None
        if number(raw) is not None:
            return raw
        if isinstance(raw, str) and re.fullmatch(r'[A-Za-z_-]{1,40}', raw):
            return raw
        if isinstance(raw, list):
            return [item(v, depth + 1) for v in raw[:100]]
        if isinstance(raw, dict):
            return {key: item(v, depth + 1) for key, v in list(raw.items())[:100]
                    if re.fullmatch(r'[A-Za-z_]{1,60}', str(key)) and
                    (key.lower() in ('modality', 'type', 'audio', 'text', 'image', 'video') or 'token' in key.lower())}
        return None

    return {key: item(value[key]) for key in TOKEN_KEYS if key in value}


def measurements(usage):
    def first(*keys):
        return next((number(usage[k]) for k in keys if number(usage.get(k)) is not None), None)
    result = {
        'inputTokens': first('promptTokenCount', 'total_input_tokens', 'inputTokens'),
        'outputTokens': first('candidatesTokenCount', 'total_output_tokens', 'outputTokens'),
        'thoughtTokens': first('thoughtsTokenCount', 'total_thought_tokens', 'thoughtTokens'),
        'totalTokens': first('totalTokenCount', 'total_tokens', 'totalTokens'),
        'cachedTokens': first('cachedContentTokenCount', 'total_cached_tokens'),
        'toolTokens': first('toolUsePromptTokenCount', 'total_tool_use_tokens'),
    }
    # Reported total already includes thought tokens: never add them twice.
    result['billableTokens'] = result['totalTokens']
    if result['billableTokens'] is None and all(result[k] is not None for k in ('inputTokens', 'outputTokens')):
        result['billableTokens'] = result['inputTokens'] + result['outputTokens'] + (result['thoughtTokens'] or 0)
    return result


def validate_pricing(value):
    if not isinstance(value, dict) or not isinstance(value.get('version'), str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', value['version']):
        raise ValueError('Pricing requires a version identifier.')
    if value.get('currency') != 'USD' or not isinstance(value.get('models'), dict) or len(value['models']) > 100:
        raise ValueError('Pricing requires USD and a model-rate mapping.')
    result = {k: clean(value.get(k), 500) for k in ('version', 'currency', 'basis', 'source')}
    result['models'] = {}
    for model, schedules in value['models'].items():
        if not re.fullmatch(r'[a-zA-Z0-9._/-]{1,120}', model) or not isinstance(schedules, list) or not 1 <= len(schedules) <= 50:
            raise ValueError('Each model requires one or more dated rate schedules.')
        checked = []
        for rates in schedules:
            if not isinstance(rates, dict):
                raise ValueError('Invalid pricing rate schedule.')
            entry = {}
            for name in ('effectiveFrom', 'effectiveUntil'):
                if name in rates:
                    try:
                        entry[name] = date.fromisoformat(rates[name]).isoformat()
                    except (TypeError, ValueError):
                        raise ValueError('Pricing effective dates must use YYYY-MM-DD.') from None
            if 'effectiveFrom' not in entry or entry.get('effectiveUntil', '9999-12-31') <= entry['effectiveFrom']:
                raise ValueError('Pricing requires an ordered effective date range.')
            for name in ('inputPerMillion', 'outputPerMillion', 'thoughtPerMillion', 'cachedInputPerMillion'):
                if name in rates:
                    rate = number(rates[name])
                    if rate is None or rate > 1_000_000:
                        raise ValueError('Token rates must be finite nonnegative USD amounts.')
                    entry[name] = rate
            if not {'inputPerMillion', 'outputPerMillion'} <= entry.keys():
                raise ValueError('Pricing needs input and output rates per million tokens.')
            checked.append(entry)
        checked.sort(key=lambda r: r['effectiveFrom'])
        for before, after in zip(checked, checked[1:]):
            if before.get('effectiveUntil', '9999-12-31') > after['effectiveFrom']:
                raise ValueError('Pricing effective date ranges must not overlap.')
        result['models'][model] = checked
    return result


class GeminiUsage:
    def __init__(self, app, connect_db):
        self.app, self.db = app, connect_db
        self.pricing_lock = threading.Lock()

    @property
    def pricing_path(self):
        return Path(self.app.config['DATA_DIR']) / 'gemini-pricing.json'

    def initialize(self):
        with transaction(self.db) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS gemini_usage (
                    id TEXT PRIMARY KEY, uid TEXT NOT NULL, project_id TEXT NOT NULL, job_id TEXT NOT NULL,
                    request_key TEXT, kind TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL,
                    event_start REAL, event_end REAL, clip_duration REAL,
                    started_at REAL NOT NULL, finished_at REAL, http_status INTEGER,
                    raw_usage TEXT NOT NULL DEFAULT '{}', estimate_json TEXT NOT NULL DEFAULT '{}');
                CREATE INDEX IF NOT EXISTS gemini_usage_time ON gemini_usage(started_at);
                CREATE INDEX IF NOT EXISTS gemini_usage_user ON gemini_usage(uid,started_at);
                CREATE INDEX IF NOT EXISTS gemini_usage_job ON gemini_usage(job_id,started_at);
                CREATE UNIQUE INDEX IF NOT EXISTS gemini_usage_request_key ON gemini_usage(job_id,request_key)
                    WHERE request_key IS NOT NULL;
            ''')
            # An interrupted call may have consumed tokens. Do not imply success or zero cost.
            db.execute("UPDATE gemini_usage SET status='interrupted',finished_at=? WHERE status='started'", (time.time(),))
        if not self.pricing_path.exists():
            source = Path(__file__).with_name('gemini-pricing.json')
            if source.is_file():
                self.save_pricing(json.loads(source.read_text(encoding='utf-8')))

    def pricing(self):
        try:
            path = self.pricing_path
            if path.is_symlink() or path.stat().st_size > 131072:
                return None
            return validate_pricing(json.loads(path.read_text(encoding='utf-8-sig')))
        except (OSError, ValueError, TypeError):
            return None

    def save_pricing(self, value):
        value = validate_pricing(value)
        with self.pricing_lock:
            path = self.pricing_path
            if path.is_symlink():
                raise ValueError('The pricing configuration must be a regular local file.')
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
            try:
                with temporary.open('x', encoding='utf-8') as out:
                    json.dump(value, out, indent=2)
                    out.write('\n')
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return value

    def estimate(self, model, usage, at=None, pricing=UNSET):
        pricing = self.pricing() if pricing is UNSET else pricing
        estimate = {'currency': 'USD', 'available': False, 'cost': None,
                    'pricingVersion': pricing['version'] if pricing else None, 'rates': None,
                    'basis': pricing.get('basis') if pricing else None, 'reason': None}
        if pricing is None:
            estimate['reason'] = 'Pricing is unavailable.'
            return estimate
        day = datetime.fromtimestamp(at if at is not None else time.time(), timezone.utc).date().isoformat()
        rates = next((r for r in pricing['models'].get(model, [])
                      if r['effectiveFrom'] <= day < r.get('effectiveUntil', '9999-12-31')), None)
        estimate['rates'] = rates
        if rates is None:
            estimate['reason'] = 'No configured rate applies to this model and request date.'
            return estimate
        tokens = measurements(usage)
        if tokens['inputTokens'] is None or tokens['outputTokens'] is None:
            estimate['reason'] = 'The API did not report complete input and output token usage.'
            return estimate
        cached = tokens['cachedTokens'] or 0
        if cached and 'cachedInputPerMillion' not in rates:
            estimate['reason'] = 'Cached tokens were reported without a configured cache rate.'
            return estimate
        if tokens['toolTokens']:
            estimate['reason'] = 'Tool token pricing is not configured.'
            return estimate
        thought_rate = rates.get('thoughtPerMillion', rates['outputPerMillion'])
        cost = ((tokens['inputTokens'] - min(cached, tokens['inputTokens'])) * rates['inputPerMillion']
                + cached * rates.get('cachedInputPerMillion', 0)
                + tokens['outputTokens'] * rates['outputPerMillion']
                + (tokens['thoughtTokens'] or 0) * thought_rate) / 1_000_000
        estimate.update(available=True, cost=round(cost, 10))
        return estimate

    def begin_request(self, uid, project_id, job_id, model, kind='sound', event_start=None,
                      event_end=None, clip_duration=None, request_key=None):
        if kind not in ('sound', 'speech'):
            raise ValueError('Gemini request kind must be sound or speech.')
        for value in (event_start, event_end, clip_duration):
            if value is not None and number(value) is None:
                raise ValueError('Gemini event times and clip duration must be finite and nonnegative.')
        if event_start is not None and event_end is not None and event_end < event_start:
            raise ValueError('Gemini event end must follow its start.')
        ident, now = secrets.token_hex(16), time.time()
        initial = self.estimate(model, {}, now)
        request_key = clean(request_key) or None
        with transaction(self.db) as db:
            db.execute('BEGIN IMMEDIATE')
            if request_key:
                previous = db.execute('SELECT status FROM gemini_usage WHERE job_id=? AND request_key=?',
                                      (job_id, request_key)).fetchone()
                if previous:
                    raise GeminiRequestAlreadyRecorded(previous['status'])
            db.execute('''INSERT INTO gemini_usage(id,uid,project_id,job_id,request_key,kind,model,status,
                event_start,event_end,clip_duration,started_at,estimate_json) VALUES(?,?,?,?,?,?,?,'started',?,?,?,?,?)''',
                       (ident, uid, clean(project_id), clean(job_id), request_key, kind,
                        clean(model, 120), event_start, event_end, clip_duration, now, json.dumps(initial)))
        return ident

    def discard_unsubmitted(self, request_id):
        """Release a reservation only when the caller proves no transport was attempted.

        Used if the final permission/cancellation guard fails before requests.post.
        An attempted, completed, or interrupted request must retain its durable key.
        """
        with transaction(self.db) as db:
            return db.execute("DELETE FROM gemini_usage WHERE id=? AND status='started' AND finished_at IS NULL",
                              (request_id,)).rowcount

    def finish_request(self, request_id, status='succeeded', usage=None, http_status=None):
        if status not in ('succeeded', 'failed', 'rejected', 'cancelled', 'interrupted'):
            raise ValueError('Invalid Gemini API request status.')
        usage = raw_usage(usage)
        with transaction(self.db) as db:
            row = db.execute('SELECT model,started_at,estimate_json FROM gemini_usage WHERE id=?', (request_id,)).fetchone()
            if row is None:
                raise LookupError('Unknown Gemini request.')
            # Preserve the rate/version selected before submission even if the
            # owner changes centralized prices while this request is in flight.
            original = json.loads(row['estimate_json'])
            original_pricing = ({'version': original['pricingVersion'], 'currency': 'USD',
                                 'basis': original.get('basis'),
                                 'models': {row['model']: [original['rates']] if original.get('rates') else []}}
                                if original.get('pricingVersion') else None)
            estimate = self.estimate(row['model'], usage, row['started_at'], original_pricing)
            http_status = int(http_status) if number(http_status) is not None and 100 <= http_status <= 599 else None
            db.execute('UPDATE gemini_usage SET status=?,finished_at=?,http_status=?,raw_usage=?,estimate_json=? WHERE id=?',
                       (status, time.time(), http_status, json.dumps(usage), json.dumps(estimate), request_id))

    def record_request(self, uid, project_id, job_id, model, kind='sound', status='succeeded', usage=None,
                       http_status=None, event_start=None, event_end=None, clip_duration=None, request_key=None):
        ident = self.begin_request(uid, project_id, job_id, model, kind, event_start, event_end, clip_duration, request_key)
        self.finish_request(ident, status, usage, http_status)
        return ident

    def snapshot(self, person='', limit=200, since=None, until=None):
        if person and not re.fullmatch(r'[0-9a-f]{64}', person):
            raise ValueError('Choose a valid person.')
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 2000:
            raise ValueError('Choose a request limit from 1 through 2000.')
        for boundary in (since, until):
            if boundary is not None and number(boundary) is None:
                raise ValueError('Time filters must be nonnegative Unix timestamps.')
        if since is not None and until is not None and until < since:
            raise ValueError('The end of the time filter must follow its start.')
        filters, params = [], []
        if since is not None:
            filters.append('g.started_at>=?')
            params.append(since)
        if until is not None:
            filters.append('g.started_at<?')
            params.append(until)
        pricing = self.pricing()

        def group(**extra):
            return dict(requests=0, inputTokens=0, outputTokens=0, thoughtTokens=0, totalTokens=0,
                        estimatedCost=None, unpricedRequests=0, **extra)

        def add(total, item):
            total['requests'] += 1
            for field in ('inputTokens', 'outputTokens', 'thoughtTokens', 'totalTokens'):
                total[field] += item[field] or 0
            if item['estimate']['available']:
                total['estimatedCost'] = round((total['estimatedCost'] or 0) + item['estimate']['cost'], 10)
            else:
                total['unpricedRequests'] += 1

        requests, totals, by_job, by_user, by_day = [], group(), {}, {}, {}
        with transaction(self.db) as db:
            if person:
                match = next((r[0] for r in db.execute('SELECT DISTINCT uid FROM gemini_usage') if public_id(r[0]) == person), None)
                filters.append('g.uid=?')
                params.append(match or '')
            query = '''SELECT g.*,u.name,u.email FROM gemini_usage g LEFT JOIN audit_users u ON u.uid=g.uid'''
            if filters:
                query += ' WHERE ' + ' AND '.join(filters)
            for row in db.execute(query + ' ORDER BY g.started_at DESC,g.id DESC', params):
                raw = json.loads(row['raw_usage'])
                tokens = measurements(raw)
                item = {'id': row['id'], 'userId': public_id(row['uid']), 'name': clean(row['name']), 'email': clean(row['email']),
                        'projectId': row['project_id'], 'jobId': row['job_id'], 'kind': row['kind'],
                        'eventStart': row['event_start'], 'eventEnd': row['event_end'], 'clipDuration': row['clip_duration'],
                        'model': row['model'], 'status': row['status'], 'startedAt': row['started_at'],
                        'finishedAt': row['finished_at'], 'httpStatus': row['http_status'], **tokens,
                        'rawUsage': raw, 'estimate': self.estimate(row['model'], raw, row['started_at'], pricing),
                        'historicalEstimate': json.loads(row['estimate_json'])}
                if len(requests) < limit:
                    requests.append(item)
                job_key = (row['project_id'], row['job_id'])
                job = by_job.setdefault(job_key, group(jobId=row['job_id'], projectId=row['project_id'], userId=item['userId']))
                user = by_user.setdefault(item['userId'], group(userId=item['userId'], name=item['name'], email=item['email']))
                day = datetime.fromtimestamp(row['started_at'], timezone.utc).date().isoformat()
                daily = by_day.setdefault(day, group(day=day))
                for target in (totals, job, user, daily):
                    add(target, item)
        return {'requests': requests, 'totals': totals, 'byJob': list(by_job.values()),
                'byUser': list(by_user.values()), 'byDay': list(by_day.values()),
                'hasMore': totals['requests'] > len(requests), 'maxRequests': 2000,
                'pricingVersion': pricing['version'] if pricing else None,
                'pricingBasis': pricing.get('basis') if pricing else None}
