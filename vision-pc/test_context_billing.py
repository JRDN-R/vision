"""Offline accounting checks for adaptive retrieval's multiple provider calls."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from types import SimpleNamespace

from venture_billing import Funding, estimate, estimate_responses, key_id


def response(rid, tokens=150_000, containers=()):
    return dict(id=rid, model='gpt-6-astra', service_tier='default',
                usage=dict(input_tokens=tokens, output_tokens=100, total_tokens=tokens+100,
                           input_tokens_details=dict(cached_tokens=100, cache_write_tokens=0),
                           output_tokens_details=dict(reasoning_tokens=70)),
                output=[dict(type='code_interpreter_call', container_id=cid) for cid in containers])


class ContextBillingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'sessions.db'
        self.sessions = SimpleNamespace(db=self.db)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE projects(id TEXT PRIMARY KEY,owner_uid TEXT);
                CREATE TABLE project_runs(id TEXT PRIMARY KEY,project_id TEXT);
                INSERT INTO projects VALUES('project-a','firebase:alice');
                INSERT INTO projects VALUES('project-b','firebase:bob');
            ''')
        self.funding = Funding(self.sessions)
        self.funding.initialize()
        self.now = time.time()
        with self.db() as db:
            db.execute('INSERT INTO venture_funding VALUES(?,?,?,?,?,?,?,?)',
                       ('firebase:alice', key_id('test-key'), 20_000_000, 20_000_000, 1, self.now, self.now, 0))

    def tearDown(self):
        self.temp.cleanup()

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def row(self, final=None, status='completed'):
        return dict(id='run-a', project_id='project-a', model='gpt-6-astra', status=status,
                    response_id=final.get('id') if final else None,
                    response_json=json.dumps(final) if final else None,
                    venture_key_id=key_id('test-key'), venture_pricing_json=json.dumps(self.funding.catalog),
                    created_at=self.now, updated_at=self.now+1)

    def persist(self, *responses):
        with self.db() as db:
            for value in responses:
                db.execute('INSERT INTO context_responses VALUES(?,?,?)', ('run-a', value['id'], json.dumps(value)))

    def ledger(self):
        with self.db() as db:
            return dict(db.execute('SELECT * FROM venture_ledger').fetchone())

    def test_each_hop_is_priced_before_usage_is_aggregated(self):
        values = [response('response-first'), response('response-final')]
        result = estimate_responses(values, 'gpt-6-astra', self.funding.catalog)
        separate = sum(estimate(value, 'gpt-6-astra', self.funding.catalog)['micro'] for value in values)
        self.assertEqual(result['micro'], separate)
        self.assertEqual(result['usage']['input_tokens'], 300_000)
        self.assertEqual(result['usage']['output_tokens'], 200)
        self.assertEqual(result['usage']['output_tokens_details']['reasoning_tokens'], 140)
        self.assertEqual(result['responseCount'], 2)
        # Aggregate pricing would cross the per-request threshold and overbill.
        combined = response('bad-aggregate', 300_000)
        self.assertGreater(estimate(combined, 'gpt-6-astra', self.funding.catalog)['micro'], separate)

    def test_final_hop_duplicate_and_repeat_reconciliation_debit_once(self):
        first, final = response('response-first'), response('response-final')
        self.persist(first, final)
        self.funding.account_for(self.row(final))
        before = self.ledger()
        self.funding.account_for(self.row(final))
        after = self.ledger()
        self.assertEqual(before, after)
        self.assertEqual(json.loads(after['details'])['responseCount'], 2)
        expected = sum(estimate(item, 'gpt-6-astra', self.funding.catalog)['micro'] for item in (first, final))
        self.assertEqual(after['micro'], expected)
        with self.db() as db:
            balance = db.execute('SELECT balance FROM venture_funding').fetchone()[0]
            self.assertEqual(balance, 20_000_000-expected)

    def test_final_hop_not_yet_in_table_is_included_and_containers_unioned(self):
        first = response('response-first', containers=('container-shared',))
        final = response('response-final', containers=('container-shared', 'container-new'))
        self.persist(first)
        self.funding.account_for(self.row(final))
        result = json.loads(self.ledger()['details'])
        token_cost = sum(estimate(item, 'gpt-6-astra', self.funding.catalog)['micro'] for item in (first, final))
        self.assertEqual(result['micro'], token_cost+60_000)
        self.assertEqual(result['containers'], ['container-new', 'container-shared'])
        self.assertEqual(result['responseCount'], 2)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM venture_container_costs').fetchone()[0], 2)

    def test_cancellation_after_paid_retrieval_does_not_zero_prior_usage(self):
        first = response('response-first')
        self.persist(first)
        self.funding.account_for(self.row(status='cancelled'))
        self.assertEqual(self.ledger()['micro'], estimate(first, 'gpt-6-astra', self.funding.catalog)['micro'])

    def test_cancelled_before_submission_remains_free(self):
        self.funding.account_for(self.row(status='cancelled'))
        self.assertEqual(self.ledger()['micro'], 0)

    def test_unknown_usage_is_not_fabricated_in_aggregate(self):
        missing = response('response-missing')
        del missing['usage']
        result = estimate_responses([response('response-first'), missing], 'gpt-6-astra', self.funding.catalog)
        self.assertNotIn('input_tokens', result['usage'])
        self.assertTrue(result['problems'])
        self.assertEqual(len(result['responses']), 2)

    def test_unrelated_runs_are_not_included(self):
        foreign = response('response-other-user')
        with self.db() as db:
            db.execute('INSERT INTO context_responses VALUES(?,?,?)', ('run-b', foreign['id'], json.dumps(foreign)))
        final = response('response-final')
        self.funding.account_for(self.row(final))
        self.assertEqual(json.loads(self.ledger()['details'])['responseCount'], 1)


if __name__ == '__main__':
    unittest.main()
