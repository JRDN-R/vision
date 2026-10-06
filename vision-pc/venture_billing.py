"""Account-scoped estimated funding, with an idempotent micro-dollar ledger.

This is deliberately not a billing scraper. Calibration is explicit. Returned
provider usage is the input; output reasoning is never charged a second time.
No raw credential or dollar balance is returned by the meter API.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import time

MICRO = Decimal(1_000_000)
BILLING_ERRORS = frozenset({
    'credit_balance_exhausted', 'organization_spend_limit_exceeded',
    'project_spend_limit_exceeded', 'organization_usage_limit_exceeded',
})


def key_id(key: str) -> str:
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


def amount_micro(value) -> int:
    # Strings avoid binary float rounding in calibration requests.
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,7}(?:\.\d{1,6})?', value):
        raise ValueError('Enter a non-negative amount with up to six decimal places.')
    amount = Decimal(value)
    if amount > 1_000_000:
        raise ValueError('The maximum calibration amount is 1,000,000 USD.')
    return int((amount * MICRO).to_integral_exact())


def utc_seconds(value: str) -> float:
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def count(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('Invalid provider usage count')
    return value


def estimate(response: dict, requested_model: str, catalog: dict, now: float | None = None) -> dict:
    """Compute known token/search charges. Unknown coverage is explicit, not zero cost."""
    now = time.time() if now is None else now
    problems = []
    model = response.get('model') or requested_model
    rates = catalog.get('models', {}).get(model)
    # Only dated snapshots of a known exact ID inherit its rates, not arbitrary suffixes.
    if rates is None:
        match = re.fullmatch(r'(.+)-\d{4}-\d{2}-\d{2}', model)
        rates = catalog.get('models', {}).get(match[1]) if match else None
    usage = response.get('usage') or {}
    token_cost = Decimal(0)
    try:
        if not rates:
            raise ValueError('This model has no verified price in the server catalog.')
        total_in, total_out = count(usage['input_tokens']), count(usage['output_tokens'])
        details = usage.get('input_tokens_details') or {}
        cached = count(details.get('cached_tokens', 0))
        writes = count(details.get('cache_write_tokens', 0))
        if cached + writes > total_in:
            raise ValueError('Provider input-token details are inconsistent.')
        if 'cache_write_tokens' not in details:
            problems.append('Cache-write usage was not returned.')
        tier = response.get('service_tier')
        if not tier:
            tier = 'default'
            problems.append('Service tier was not returned; standard pricing was assumed.')
        multiplier = catalog['tierMultipliers'].get(tier)
        if multiplier is None:
            raise ValueError('The returned service tier has no verified price.')
        long_context = total_in > rates['longThreshold']
        in_factor = Decimal(rates['longInputFactor']) if long_context else Decimal(1)
        out_factor = Decimal(rates['longOutputFactor']) if long_context else Decimal(1)
        token_cost = (Decimal(total_in-cached-writes)*Decimal(rates['input']) +
                      Decimal(cached)*Decimal(rates['cached']) +
                      Decimal(writes)*Decimal(rates['write'])) * in_factor
        token_cost += Decimal(total_out)*Decimal(rates['output'])*out_factor
        token_cost *= Decimal(multiplier)
        # Prices are USD / million, so the result above already is micro-USD.
        for deadline in (catalog.get('reviewAfter'), rates.get('reviewAfter')):
            if deadline and now >= utc_seconds(deadline):
                problems.append('Pricing needs review; update the FUPCJ Server price catalog.')
                break
    except (KeyError, ValueError, TypeError, InvalidOperation) as error:
        problems.append(str(error) if isinstance(error, ValueError) else 'Complete token usage was not returned.')

    search_ids, containers = set(), set()
    for index, item in enumerate(response.get('output') or []):
        kind = item.get('type', '')
        if kind == 'web_search_call':
            search_ids.add(item.get('id') or f'call-{index}')
        elif kind == 'code_interpreter_call':
            container = item.get('container_id')
            if isinstance(container, str) and re.fullmatch(r'[\w-]{1,200}', container):
                containers.add(container)
        elif kind.endswith('_call') and kind not in ('function_call',):
            problems.append('An additional tool charge could not be determined.')
    tool_micro = Decimal(len(search_ids)) * Decimal(catalog['tools']['webSearchCall']) * MICRO
    return dict(micro=int((token_cost+tool_micro).quantize(Decimal(1), rounding=ROUND_HALF_UP)),
                problems=list(dict.fromkeys(problems)), containers=sorted(containers),
                model=model, usage=usage, pricingVersion=catalog.get('version', 'unknown'))


class Funding:
    def __init__(self, sessions):
        self.sessions, self.db = sessions, sessions.db
        self.catalog = {}

    def initialize(self):
        self.catalog = json.loads(Path(__file__).with_name('venture-pricing.json').read_text(encoding='utf-8'))
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS venture_funding (
                    uid TEXT NOT NULL, key_id TEXT NOT NULL, balance INTEGER NOT NULL,
                    capacity INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    calibrated_at REAL NOT NULL, updated_at REAL NOT NULL, exhausted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(uid,key_id));
                CREATE TABLE IF NOT EXISTS venture_calibrations (
                    uid TEXT NOT NULL,key_id TEXT NOT NULL,request_id TEXT NOT NULL,
                    payload TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(uid,key_id,request_id));
                CREATE TABLE IF NOT EXISTS venture_ledger (
                    run_id TEXT PRIMARY KEY,uid TEXT NOT NULL,key_id TEXT NOT NULL,
                    response_id TEXT,micro INTEGER NOT NULL,details TEXT NOT NULL,created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS venture_ledger_account ON venture_ledger(uid,key_id,created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS venture_ledger_response ON venture_ledger(uid,key_id,response_id)
                    WHERE response_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS venture_container_costs (
                    uid TEXT NOT NULL,key_id TEXT NOT NULL,container_id TEXT NOT NULL,
                    covered_until REAL NOT NULL,PRIMARY KEY(uid,key_id,container_id));
            ''')
            columns = {r[1] for r in db.execute('PRAGMA table_info(project_runs)')}
            for name, declaration in [('venture_key_id', 'TEXT'), ('venture_pricing_json', 'TEXT')]:
                if name not in columns:
                    db.execute('ALTER TABLE project_runs ADD COLUMN '+name+' '+declaration)

    def bind(self, db, run_id: str, key: str):
        # Called in the run-acceptance transaction. Later price updates cannot
        # silently reprice requests already accepted or rebill old responses.
        db.execute('UPDATE project_runs SET venture_key_id=?,venture_pricing_json=? WHERE id=?',
                   (key_id(key), json.dumps(self.catalog), run_id))

    def account_for(self, row: dict):
        if not row.get('venture_key_id'):
            return  # Historical runs precede accounting; never retroactively debit them.
        with self.db() as db:
            owner = db.execute('SELECT owner_uid FROM projects WHERE id=?', (row['project_id'],)).fetchone()
        if not owner or not owner[0]:
            return
        uid, kid = owner[0], row['venture_key_id']
        response = json.loads(row.get('response_json') or '{}')
        catalog = json.loads(row.get('venture_pricing_json') or '{}') or self.catalog
        result = estimate(response, row['model'], catalog)
        # Cancelled before any provider submission is known to be free.
        if row['status'] == 'cancelled' and not row['response_id'] and not row.get('response_json'):
            result.update(micro=0, problems=[], containers=[])
        rid = row.get('response_id') or None
        now = time.time()
        observed = row.get('updated_at') or now
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT 1 FROM venture_ledger WHERE run_id=? OR (uid=? AND key_id=? AND response_id=?)',
                             (row['id'], uid, kid, rid)).fetchone()
            if old:
                return
            seconds = int(catalog['tools']['containerSessionSeconds'])
            for cid in result['containers']:
                covered = db.execute('SELECT covered_until FROM venture_container_costs WHERE uid=? AND key_id=? AND container_id=?',
                                     (uid,kid,cid)).fetchone()
                start = covered[0] if covered else row['created_at']
                if not covered or observed > start:
                    # Activity time is an estimate: the provider does not supply
                    # exact billable container minutes in every response.
                    import math
                    blocks = max(1, math.ceil((observed-max(start,row['created_at']))/seconds))
                    result['micro'] += int(Decimal(catalog['tools']['container1gSession'])*MICRO)*blocks
                    db.execute('INSERT INTO venture_container_costs VALUES(?,?,?,?) ON CONFLICT(uid,key_id,container_id) '
                               'DO UPDATE SET covered_until=excluded.covered_until', (uid,kid,cid,max(start,row['created_at'])+blocks*seconds))
                result['problems'].append('Container duration is estimated, not an official billing measurement.')
            result['problems'] = list(dict.fromkeys(result['problems']))
            db.execute('INSERT INTO venture_ledger VALUES(?,?,?,?,?,?,?)',
                       (row['id'],uid,kid,rid,result['micro'],json.dumps(result),now))
            db.execute('UPDATE venture_funding SET balance=balance-?,revision=revision+1,updated_at=? WHERE uid=? AND key_id=?',
                       (result['micro'],now,uid,kid))

    def reconcile(self, uid: str, kid: str) -> bool:
        """Retry only local accounting after a crash, never an inference request."""
        with self.db() as db:
            rows = db.execute("""SELECT r.* FROM project_runs r JOIN projects p ON p.id=r.project_id
                LEFT JOIN venture_ledger l ON l.run_id=r.id
                WHERE p.owner_uid=? AND r.venture_key_id=? AND l.run_id IS NULL
                AND r.status IN ('completed','incomplete','cancelled','error') ORDER BY r.created_at LIMIT 100""", (uid,kid)).fetchall()
        complete = len(rows) < 100
        for row in rows:
            try:
                self.account_for(dict(row))
            except Exception:
                self.sessions.app.logger.warning('Venture accounting reconciliation is pending.')
                complete = False
        return complete

    def exhausted(self, row: dict, code: str | None):
        if code != 'credit_balance_exhausted' or not row.get('venture_key_id'):
            return
        with self.db() as db:
            owner = db.execute('SELECT owner_uid FROM projects WHERE id=?',(row['project_id'],)).fetchone()
            if owner and owner[0]:
                now = time.time()
                db.execute('''INSERT INTO venture_funding(uid,key_id,balance,capacity,calibrated_at,updated_at,exhausted)
                    VALUES(?,?,0,0,?,?,1) ON CONFLICT(uid,key_id) DO UPDATE SET exhausted=1,
                    balance=0,revision=revision+1,updated_at=excluded.updated_at''',
                    (owner[0],row['venture_key_id'],now,now))

    def status(self, uid: str, kid: str | None) -> dict:
        empty = dict(provider='estimate', status='uncalibrated', fraction=None, revision=0,
                     updatedAt=None, coverage='unknown', issues=[], pricingVersion=self.catalog['version'])
        if not kid:
            return {**empty,'status':'no-key'}
        reconciled = self.reconcile(uid,kid)
        with self.db() as db:
            row = db.execute('SELECT * FROM venture_funding WHERE uid=? AND key_id=?',(uid,kid)).fetchone()
            if row is None:
                return empty
            rows = db.execute('SELECT details FROM venture_ledger WHERE uid=? AND key_id=? AND created_at>?',
                              (uid,kid,row['calibrated_at'])).fetchall()
            active = db.execute("SELECT COUNT(*) FROM project_runs r JOIN projects p ON p.id=r.project_id WHERE p.owner_uid=? AND r.venture_key_id=? AND r.status IN ('queued','preparing','submitting','in_progress','saving')", (uid,kid)).fetchone()[0]
        issues = list(dict.fromkeys(p for r in rows for p in json.loads(r[0]).get('problems',[])))
        fraction = max(0,min(1,row['balance']/row['capacity'])) if row['capacity'] else 0
        status = 'exhausted' if row['exhausted'] else 'estimated-empty' if fraction<=0 else 'low' if fraction<=.2 else 'available'
        if not reconciled:
            issues.append('Some completed responses are awaiting local usage reconciliation.')
        if active:
            issues.append('An active response has not yet been deducted.')
        if time.time()>=utc_seconds(self.catalog['reviewAfter']):
            issues.append('Pricing needs review; update FUPCJ Server.')
        return {**empty,'status':status,'fraction':round(fraction,5),'revision':row['revision'],
                'updatedAt':row['updated_at'],'coverage':'partial' if issues else 'tracked',
                'issues':issues,'activeRuns':active}

    def calibrate(self, uid: str, kid: str, body: dict) -> dict:
        rid, kind = body.get('requestId'), body.get('kind')
        if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,120}',rid):
            raise self.sessions.Error('A calibration request ID is required.')
        if kind not in ('set','add'):
            raise self.sessions.Error('Choose current balance or added funds.')
        try:
            amount = amount_micro(body.get('amount'))
        except ValueError as error:
            raise self.sessions.Error(str(error))
        if not self.reconcile(uid,kid):
            raise self.sessions.Error('Usage reconciliation is pending. Try again after checking server storage.',503)
        payload=json.dumps([kind,amount],separators=(',',':'))
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT payload FROM venture_calibrations WHERE uid=? AND key_id=? AND request_id=?',(uid,kid,rid)).fetchone()
            if old:
                if old[0]!=payload:
                    raise self.sessions.Error('This calibration ID was already used with a different amount.',409)
            else:
                prior=db.execute('SELECT * FROM venture_funding WHERE uid=? AND key_id=?',(uid,kid)).fetchone()
                if body.get('revision')!=(prior['revision'] if prior else 0):
                    raise self.sessions.Error('Funding changed on another device. Refresh and enter the amount again.',409)
                if kind=='add' and prior is None:
                    raise self.sessions.Error('Set the current OpenAI balance before recording additional funds.')
                active=db.execute("SELECT 1 FROM project_runs r JOIN projects p ON p.id=r.project_id WHERE p.owner_uid=? AND r.venture_key_id=? AND r.status IN ('queued','preparing','submitting','in_progress','saving') LIMIT 1",(uid,kid)).fetchone()
                if kind=='set' and active:
                    raise self.sessions.Error('Wait for your active response to finish before setting the current balance. Added funds can be recorded now.',409)
                now=time.time()
                balance=amount+(prior['balance'] if kind=='add' else 0)
                capacity=max(balance,prior['capacity']) if kind=='add' else balance
                calibrated=prior['calibrated_at'] if kind=='add' else now
                db.execute('''INSERT INTO venture_funding VALUES(?,?,?,?,?,?,?,0)
                    ON CONFLICT(uid,key_id) DO UPDATE SET balance=excluded.balance,capacity=excluded.capacity,
                    revision=venture_funding.revision+1,calibrated_at=excluded.calibrated_at,updated_at=excluded.updated_at,exhausted=0''',
                    (uid,kid,balance,capacity,1,calibrated,now))
                db.execute('INSERT INTO venture_calibrations VALUES(?,?,?,?,?)',(uid,kid,rid,payload,now))
        return self.status(uid,kid)
