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


def model_rates(model: str, catalog: dict):
    rates = catalog.get('models', {}).get(model)
    if rates is None:
        match = re.fullmatch(r'(.+)-\d{4}-\d{2}-\d{2}', model)
        candidate = catalog.get('models', {}).get(match[1]) if match else None
        if candidate and candidate.get('inheritSnapshots', True):
            rates = candidate
    return rates


def output_measure(text: str, model: str) -> dict:
    """Local text only; never claim to observe the model's hidden reasoning.

    Reuse an installed, already loaded model encoding without downloading one
    in the request path. Final provider usage always replaces this estimate.
    """
    try:
        import tiktoken
        name = tiktoken.model.encoding_name_for_model(model)
        encoder = tiktoken.registry.ENCODINGS.get(name)
        if encoder is not None:
            return dict(tokens=len(encoder.encode(text, disallowed_special=())), kind='local-tokenizer')
    except (ImportError, AttributeError, KeyError):
        pass
    return dict(tokens=(len(text)+3)//4, kind='text-estimate')


def estimate(response: dict, requested_model: str, catalog: dict, now: float | None = None) -> dict:
    """Compute known token/search charges. Unknown coverage is explicit, not zero cost."""
    now = time.time() if now is None else now
    problems = []
    model = response.get('model') or requested_model
    rates = model_rates(model, catalog)
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
        if 'cached_tokens' not in details:
            problems.append('Cached-input usage was not returned.')
        if rates.get('cacheWriteUsageRequired', True) and 'cache_write_tokens' not in details:
            problems.append('Cache-write usage was not returned.')
        tier = response.get('service_tier')
        if not tier:
            tier = 'default'
            problems.append('Service tier was not returned; standard pricing was assumed.')
        multiplier = rates.get('tierMultipliers', catalog['tierMultipliers']).get(tier)
        if multiplier is None:
            raise ValueError('The returned service tier has no verified price.')
        long_context = rates.get('longThreshold') is not None and total_in > rates['longThreshold']
        in_factor = Decimal(rates['longInputFactor']) if long_context else Decimal(1)
        out_factor = Decimal(rates['longOutputFactor']) if long_context else Decimal(1)
        token_cost = (Decimal(total_in-cached-writes)*Decimal(rates['input']) +
                      Decimal(cached)*Decimal(rates['cached']) +
                      Decimal(writes)*Decimal(rates.get('write', rates['input']))) * in_factor
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


def estimate_responses(responses: list[dict], requested_model: str, catalog: dict,
                       now: float | None = None) -> dict:
    """Price individual Responses API hops before summing their reported usage.

    A tool continuation is a separate billable response. Summing its input tokens
    before pricing would incorrectly trigger long-context rates across requests.
    Reasoning tokens remain part of output_tokens, and containers are charged by
    the existing account-scoped session meter after taking the union here.
    """
    priced = [estimate(response, requested_model, catalog, now) for response in responses]
    usage = {}
    for field in ('input_tokens', 'output_tokens', 'total_tokens'):
        values = [item['usage'].get(field) for item in priced]
        if values and all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in values):
            usage[field] = sum(values)
    for field, keys in (('input_tokens_details', ('cached_tokens', 'cache_write_tokens')),
                        ('output_tokens_details', ('reasoning_tokens',))):
        for key in keys:
            values = [(item['usage'].get(field) or {}).get(key) for item in priced]
            if values and all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in values):
                usage.setdefault(field, {})[key] = sum(values)
    return dict(micro=sum(item['micro'] for item in priced),
                problems=list(dict.fromkeys(problem for item in priced for problem in item['problems'])),
                containers=sorted({cid for item in priced for cid in item['containers']}),
                model=priced[-1]['model'] if priced else requested_model, usage=usage,
                pricingVersion=catalog.get('version', 'unknown'), responseCount=len(priced),
                responses=[dict(responseId=response.get('id'), model=item['model'], usage=item['usage'],
                                micro=item['micro']) for response, item in zip(responses, priced)])


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
                CREATE TABLE IF NOT EXISTS context_responses (
                    run_id TEXT NOT NULL,response_id TEXT NOT NULL,response_json TEXT NOT NULL,
                    PRIMARY KEY(run_id,response_id));
            ''')
            columns = {r[1] for r in db.execute('PRAGMA table_info(project_runs)')}
            for name, declaration in [('venture_key_id', 'TEXT'), ('venture_pricing_json', 'TEXT'), ('venture_live_json', 'TEXT')]:
                if name not in columns:
                    db.execute('ALTER TABLE project_runs ADD COLUMN '+name+' '+declaration)

    def bind(self, db, run_id: str, key: str):
        # Called in the run-acceptance transaction. Later price updates cannot
        # silently reprice requests already accepted or rebill old responses.
        db.execute('UPDATE project_runs SET venture_key_id=?,venture_pricing_json=? WHERE id=?',
                   (key_id(key), json.dumps(self.catalog), run_id))

    def prepare_live(self, row: dict, key: str, payload: dict):
        """Count the exact prepared request, not just the text in the composer.

        Counting is best-effort and never retries or prevents generation. Only
        calibrated connections need this extra read-only provider request.
        Neither the key nor the prepared input is stored in live telemetry.
        """
        if not row.get('venture_key_id'):
            return
        with self.db() as db:
            baseline = db.execute('SELECT 1 FROM venture_funding f JOIN projects p ON p.owner_uid=f.uid '
                                  'WHERE p.id=? AND f.key_id=?', (row['project_id'], row['venture_key_id'])).fetchone()
        if not baseline:
            return
        live = dict(model=payload['model'], serviceTier=payload.get('service_tier', 'default'),
                    inputTokens=None, inputSource='unavailable', responseId=None, observedAt=time.time())
        # The count endpoint's schema excludes generation-only controls such as
        # background/stream/store/max_output_tokens/service_tier.
        fields = ('model', 'input', 'instructions', 'tools', 'tool_choice', 'reasoning', 'text',
                  'previous_response_id', 'conversation', 'truncation', 'parallel_tool_calls')
        try:
            measured = self.sessions.call_json(key, 'POST', '/responses/input_tokens',
                                              json={k: payload[k] for k in fields if k in payload}, timeout=(3, 8))
            live.update(inputTokens=count(measured['input_tokens']), inputSource='provider-count')
        except Exception:
            # Do not echo provider errors: they may contain request content.
            pass
        with self.db() as db:
            db.execute('UPDATE project_runs SET venture_live_json=? WHERE id=?', (json.dumps(live), row['id']))

    def observe_live(self, row: dict, response: dict):
        if not row.get('venture_key_id'):
            return
        live = json.loads(row.get('venture_live_json') or '{}')
        live.update(responseId=response.get('id'), model=response.get('model') or row['model'],
                    observedAt=time.time())
        if response.get('service_tier'):
            live['serviceTier'] = response['service_tier']
        usage = response.get('usage')
        if isinstance(usage, dict):
            live['usage'] = usage
        with self.db() as db:
            db.execute('UPDATE project_runs SET venture_live_json=? WHERE id=?', (json.dumps(live), row['id']))

    def pending_estimate(self, db, runs: list) -> dict:
        """A read-only overlay; it never debits or advances the durable ledger."""
        micro, issues, input_tokens, output_tokens, models, observed = 0, [], 0, 0, set(), 0
        provisional, waiting = False, False
        coverage = {}  # Share container estimates across this snapshot's active runs.
        for value in runs:
            row = dict(value)
            catalog = json.loads(row.get('venture_pricing_json') or '{}') or self.catalog
            responses = {r['response_id']: json.loads(r['response_json']) for r in db.execute(
                'SELECT response_id,response_json FROM context_responses WHERE run_id=?', (row['id'],))}
            final = json.loads(row.get('response_json') or '{}')
            if final:
                responses[final.get('id') or row['response_id'] or '__final__'] = final
            live = json.loads(row.get('venture_live_json') or '{}')
            rid = live.get('responseId')
            if rid and rid not in responses:
                usage = live.get('usage')
                if not isinstance(usage, dict) or not all(isinstance(usage.get(k), int) and not isinstance(usage[k], bool)
                                                        and usage[k] >= 0 for k in ('input_tokens', 'output_tokens')):
                    provisional = True
                    measured = output_measure(row.get('text') or '', live.get('model') or row['model'])
                    usage = dict(input_tokens=live.get('inputTokens') or 0,
                                 output_tokens=min(measured['tokens'], row['max_tokens']),
                                 input_tokens_details=dict(cached_tokens=0, cache_write_tokens=0))
                    if live.get('inputTokens') is None:
                        issues.append('Input token count is unavailable for an active response.')
                responses[rid] = dict(id=rid, model=live.get('model') or row['model'],
                                      service_tier=live.get('serviceTier'), usage=usage, output=[])
            elif not responses or row.get('context_pending_parent'):
                waiting = True
            priced = estimate_responses(list(responses.values()), row['model'], catalog)
            micro += priced['micro']
            issues.extend(priced['problems'])
            input_tokens += priced['usage'].get('input_tokens', 0)
            output_tokens += priced['usage'].get('output_tokens', 0)
            if responses:
                models.update(item.get('model') or row['model'] for item in responses.values())
                observed = max(observed, row['updated_at'], live.get('observedAt', 0))
            for cid in priced['containers']:
                key = (row['venture_key_id'], cid)
                if key not in coverage:
                    prior = db.execute('SELECT covered_until FROM venture_container_costs WHERE uid=? AND key_id=? AND container_id=?',
                                       (row['owner_uid'], key[0], cid)).fetchone()
                    coverage[key] = prior[0] if prior else None
                start = coverage[key]
                if start is None or row['updated_at'] > start:
                    import math
                    seconds = int(catalog['tools']['containerSessionSeconds'])
                    start = max(start or row['created_at'], row['created_at'])
                    blocks = max(1, math.ceil((row['updated_at']-start)/seconds))
                    micro += int(Decimal(catalog['tools']['container1gSession'])*MICRO)*blocks
                    coverage[key] = start+blocks*seconds
                issues.append('Container duration is estimated, not an official billing measurement.')
        if provisional:
            issues.append('Live estimates use counted input and visible output; caching, hidden reasoning and tool usage reconcile when reported.')
        if waiting:
            issues.append('Waiting for provider usage for an active response.')
        return dict(micro=micro, issues=issues, observedAt=observed,
                    usage=dict(inputTokens=input_tokens, outputTokens=output_tokens, models=sorted(models),
                               kind='live-estimate' if provisional or waiting else 'provider', scope='active'))

    def account_for(self, row: dict):
        if not row.get('venture_key_id'):
            return  # Historical runs precede accounting; never retroactively debit them.
        with self.db() as db:
            owner = db.execute('SELECT owner_uid FROM projects WHERE id=?', (row['project_id'],)).fetchone()
            hops = db.execute('SELECT response_id,response_json FROM context_responses WHERE run_id=? ORDER BY rowid',
                              (row['id'],)).fetchall()
        if not owner or not owner[0]:
            return
        uid, kid = owner[0], row['venture_key_id']
        response = json.loads(row.get('response_json') or '{}')
        catalog = json.loads(row.get('venture_pricing_json') or '{}') or self.catalog
        # Last-hop JSON is also kept on project_runs for existing conversation
        # consumers. It must never be charged twice when it is in the hop table.
        responses = {}
        for hop in hops:
            value = json.loads(hop['response_json'])
            responses[hop['response_id']] = {**value, 'id': hop['response_id']}
        if response:
            responses[response.get('id') or row.get('response_id') or '__final__'] = response
        result = estimate_responses(list(responses.values()), row['model'], catalog) if responses else estimate(response, row['model'], catalog)
        # Cancelled before any provider submission is known to be free.
        if row['status'] == 'cancelled' and not row['response_id'] and not row.get('response_json') and not hops:
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

    def status(self, uid: str, kid: str | None, model: str | None = None) -> dict:
        empty = dict(provider='estimate', status='uncalibrated', fraction=None, revision=0, connectionId=kid,
                     updatedAt=None, coverage='unknown', issues=[], pricingVersion=self.catalog['version'],
                     liveMeterVersion=1, activeRuns=0, usage=None)
        if not kid:
            return {**empty,'status':'no-key'}
        reconciled = self.reconcile(uid,kid)
        with self.db() as db:
            # Balance, ledger and pending responses must come from one snapshot:
            # finalization on a worker thread cannot cause a transient double debit.
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM venture_funding WHERE uid=? AND key_id=?',(uid,kid)).fetchone()
            if row is None:
                return empty
            rows = db.execute('SELECT details FROM venture_ledger WHERE uid=? AND key_id=? AND created_at>?',
                              (uid,kid,row['calibrated_at'])).fetchall()
            pending = db.execute("""SELECT r.*,p.owner_uid FROM project_runs r JOIN projects p ON p.id=r.project_id
                LEFT JOIN venture_ledger l ON l.run_id=r.id
                WHERE p.owner_uid=? AND r.venture_key_id=? AND l.run_id IS NULL
                ORDER BY r.created_at""", (uid,kid)).fetchall()
            live = self.pending_estimate(db, pending)
            latest = db.execute('SELECT details FROM venture_ledger WHERE uid=? AND key_id=? ORDER BY created_at DESC LIMIT 1',
                                (uid,kid)).fetchone()
        active = sum(r['status'] in ('queued','preparing','submitting','in_progress','saving') for r in pending)
        issues = list(dict.fromkeys(p for r in rows for p in json.loads(r[0]).get('problems',[])))
        issues.extend(live['issues'])
        if model and not model_rates(model, self.catalog):
            issues.append('The selected model has no verified price; its usage cannot be fully priced.')
        fraction = max(0,min(1,(row['balance']-live['micro'])/row['capacity'])) if row['capacity'] else 0
        if row['exhausted']:
            fraction = 0
        status = 'exhausted' if row['exhausted'] else 'estimated-empty' if fraction<=0 else 'low' if fraction<=.2 else 'available'
        if not reconciled:
            issues.append('Some completed responses are awaiting local usage reconciliation.')
        if time.time()>=utc_seconds(self.catalog['reviewAfter']):
            issues.append('Pricing needs review; update FUPCJ Server.')
        usage = live['usage'] if pending else None
        if not pending and latest:
            detail = json.loads(latest['details'])
            tokens = detail.get('usage') or {}
            usage = dict(inputTokens=tokens.get('input_tokens'), outputTokens=tokens.get('output_tokens'),
                         cachedTokens=(tokens.get('input_tokens_details') or {}).get('cached_tokens'),
                         reasoningTokens=(tokens.get('output_tokens_details') or {}).get('reasoning_tokens'),
                         models=[detail['model']] if detail.get('model') else [], kind='provider', scope='last')
        return {**empty,'status':status,'fraction':fraction,'revision':row['revision'],
                'updatedAt':max(row['updated_at'],live['observedAt']),'coverage':'partial' if issues else 'tracked',
                'issues':list(dict.fromkeys(issues)),'activeRuns':active,'usage':usage}

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
        if kind=='add' and amount<=0:
            raise self.sessions.Error('Enter the amount successfully added, greater than zero.')
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
