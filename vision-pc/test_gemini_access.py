"""Offline permission/administrative billing checks; no external API calls."""
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from flask import Flask, g, request

from activity_dashboard import register
from gemini_access import (GeminiAccess, GeminiAccessDenied, GeminiRequestAlreadyRecorded,
                           GeminiUsage, public_id, validate_pricing)


class GeminiAccessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DATA_DIR=self.path)

        def connect():
            db = sqlite3.connect(self.path / 'vision.sqlite3', timeout=5)
            db.row_factory = sqlite3.Row
            return db
        self.db = connect
        self.uid, self.other, self.owner = 'firebase:requester123', 'firebase:other123', 'firebase:owner123'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE audit_users(uid TEXT PRIMARY KEY,name TEXT,email TEXT);
                CREATE TABLE local_transcriptions(id TEXT PRIMARY KEY,project_id TEXT,requester_uid TEXT,
                    provider TEXT,status TEXT,phase TEXT,updated_at REAL,cancel_requested INTEGER DEFAULT 0);
            ''')
            db.executemany('INSERT INTO audit_users VALUES(?,?,?)', [
                (self.uid, 'Jordan', 'jordan@example.com'), (self.other, 'Andrea', 'andrea@example.com'),
                (self.owner, 'Owner', 'owner@example.com')])
        self.wake = threading.Event()
        self.access = GeminiAccess(self.app, self.db, self.wake.set)
        self.usage = GeminiUsage(self.app, self.db)
        self.access.initialize()
        self.usage.initialize()
        self.app.config.update(GEMINI_ACCESS=self.access, GEMINI_USAGE=self.usage)
        (self.path / 'activity-admins.json').write_text(json.dumps({'uids': [self.owner]}))

        @self.app.before_request
        def identity():
            g.uid = request.headers.get('X-Test-Uid', self.uid)
            g.auth_kind = request.headers.get('X-Test-Kind', 'firebase-google')
        register(self.app, self.db)
        self.client = self.app.test_client()
        self.owner_headers = {'X-Test-Uid': self.owner, 'Origin': 'https://jrdn-r.github.io'}

    def tearDown(self):
        self.temporary.cleanup()

    def job(self, ident, uid=None, provider='gemini', status='approval_waiting', cancelled=0):
        with self.db() as db:
            db.execute('INSERT INTO local_transcriptions VALUES(?,?,?,?,?,?,?,?)',
                       (ident, 'project-one', uid or self.uid, provider, status, 'Waiting for Gemini approval', 1, cancelled))

    def row(self, ident):
        with self.db() as db:
            return dict(db.execute('SELECT * FROM local_transcriptions WHERE id=?', (ident,)).fetchone())

    def pricing(self, version='test-pricing-1', input_rate=2, output_rate=12):
        self.usage.save_pricing({'version': version, 'currency': 'USD', 'models': {
            'model-test': [{'effectiveFrom': '2020-01-01', 'inputPerMillion': input_rate,
                            'outputPerMillion': output_rate, 'thoughtPerMillion': output_rate}]}})

    def record(self, uid=None, job='job-one', usage=None, key=None, status='succeeded'):
        return self.usage.record_request(uid or self.uid, 'project-one', job, 'model-test', usage=usage,
                                        status=status, request_key=key, event_start=12, event_end=30, clip_duration=20)

    def test_pending_denied_and_revoked_never_gain_access_by_resubmitting(self):
        self.assertEqual(self.access.status(self.uid), 'unrequested')
        with self.assertRaises(GeminiAccessDenied):
            self.access.require_approved(self.uid)
        with self.db() as db:
            self.assertEqual(self.access.request_access(self.uid, db=db), 'pending')
        self.assertEqual(self.access.request_access(self.uid), 'pending')
        self.assertEqual(self.access.snapshot()['pendingCount'], 1)
        self.access.decide(public_id(self.uid), 'denied', self.owner)
        self.assertEqual(self.access.request_access(self.uid), 'denied')
        with self.assertRaises(GeminiAccessDenied) as denied:
            self.access.require_approved(self.uid)
        self.assertEqual(denied.exception.status, 'denied')
        self.access.decide(public_id(self.uid), 'approved', self.owner)
        self.access.require_approved(self.uid)
        self.access.decide(public_id(self.uid), 'revoked', self.owner)
        self.assertEqual(self.access.request_access(self.uid), 'revoked')
        self.assertEqual(GeminiAccess(self.app, self.db).status(self.uid), 'revoked')
        self.assertEqual(self.access.request_access('installation-owner'), 'denied')

    def test_approval_releases_only_requesters_gemini_jobs_atomically(self):
        self.access.request_access(self.uid)
        self.access.request_access(self.other)
        self.job('one')
        self.job('two')
        self.job('other', uid=self.other)
        self.job('whisper', provider='whisper')
        self.job('cancelled', cancelled=1)
        result = self.access.decide(public_id(self.uid), 'approved', self.owner)
        self.assertEqual(result['releasedJobs'], 2)
        self.assertEqual(result['pendingCount'], 1)
        self.assertTrue(self.wake.is_set())
        self.assertEqual(self.row('one')['status'], 'queued')
        for ident in ('other', 'whisper', 'cancelled'):
            self.assertEqual(self.row(ident)['status'], 'approval_waiting')
        self.access.decide(public_id(self.uid), 'revoked', self.owner)
        self.assertEqual(self.row('one')['status'], 'approval_waiting')
        self.assertEqual(self.row('one')['phase'], 'Gemini access revoked')

    def test_admin_authorization_rejects_other_google_users_private_pc_and_untrusted_origin(self):
        self.access.request_access(self.uid)
        decision = {'userId': public_id(self.uid), 'decision': 'approved'}
        for headers in ({}, {'X-Test-Uid': self.owner, 'X-Test-Kind': 'private-pc'},
                        {'X-Test-Uid': self.owner, 'Origin': 'null'},
                        {'X-Test-Uid': self.owner, 'Origin': 'https://other.example'}):
            self.assertEqual(self.client.get('/api/admin/gemini', headers=headers).status_code, 403)
            self.assertEqual(self.client.post('/api/admin/gemini/access', json=decision, headers=headers).status_code, 403)
            self.assertEqual(self.client.get('/api/admin/gemini/pricing', headers=headers).status_code, 403)
        response = self.client.get('/api/admin/gemini', headers=self.owner_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['accessRequests'][0]['name'], 'Jordan')
        self.assertIn('no-store', response.headers['Cache-Control'])
        response = self.client.post('/api/admin/gemini/access', json=decision, headers=self.owner_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.access.status(self.uid), 'approved')

    def test_own_access_endpoint_exposes_only_permission_not_admin_usage(self):
        response = self.client.post('/api/gemini/access')
        self.assertEqual(response.json, {'status': 'pending', 'approved': False})
        self.assertEqual(self.client.get('/api/gemini/access').json, response.json)
        self.assertEqual(self.client.post('/api/gemini/access', headers={'X-Test-Kind': 'trial'}).status_code, 403)
        self.assertEqual(self.client.get('/api/admin/gemini').status_code, 403)

    def test_thought_usage_and_cost_are_counted_once_and_only_raw_metrics_are_saved(self):
        self.pricing()
        ident = self.record(usage={'usageMetadata': {'promptTokenCount': 1000, 'candidatesTokenCount': 100,
                            'thoughtsTokenCount': 50, 'totalTokenCount': 1150, 'promptTokensDetails': [
                                {'modality': 'AUDIO', 'tokenCount': 980}, {'modality': 'TEXT', 'tokenCount': 20}],
                            'api_key': 'secret-key', 'text': 'private transcript'}})
        row = self.usage.snapshot()['requests'][0]
        self.assertEqual(row['id'], ident)
        self.assertEqual(row['billableTokens'], 1150)
        self.assertEqual(row['thoughtTokens'], 50)
        self.assertAlmostEqual(row['estimate']['cost'], .0038)
        self.assertEqual(row['rawUsage']['promptTokensDetails'][0]['tokenCount'], 980)
        self.assertNotIn('secret-key', json.dumps(row))
        self.assertNotIn('private transcript', json.dumps(row))
        self.assertEqual(row['eventStart'], 12)
        self.assertEqual(row['clipDuration'], 20)

    def test_missing_usage_unknown_model_or_unknown_cache_price_is_unavailable(self):
        self.pricing()
        self.record(status='failed')
        row = self.usage.snapshot()['requests'][0]
        self.assertIsNone(row['inputTokens'])
        self.assertIsNone(row['estimate']['cost'])
        usage = {'total_input_tokens': 100, 'total_output_tokens': 10, 'total_thought_tokens': 5, 'total_tokens': 115}
        self.assertIsNone(self.usage.estimate('unknown', usage)['cost'])
        cached = {**usage, 'total_cached_tokens': 20}
        self.assertIsNone(self.usage.estimate('model-test', cached)['cost'])
        self.assertEqual(self.usage.estimate('model-test', usage)['cost'], .00038)
        self.assertIsNone(self.usage.snapshot()['totals']['estimatedCost'])
        self.assertEqual(self.usage.snapshot()['totals']['unpricedRequests'], 1)

    def test_repricing_preserves_original_measurements_and_historical_rate_snapshot(self):
        self.pricing()
        self.record(usage={'promptTokenCount': 1000, 'candidatesTokenCount': 100})
        self.pricing('test-pricing-2', 4, 24)
        value = self.usage.snapshot()['requests'][0]
        self.assertEqual(value['historicalEstimate']['pricingVersion'], 'test-pricing-1')
        self.assertAlmostEqual(value['historicalEstimate']['cost'], .0032)
        self.assertEqual(value['estimate']['pricingVersion'], 'test-pricing-2')
        self.assertAlmostEqual(value['estimate']['cost'], .0064)
        self.usage.initialize()
        self.assertEqual(self.usage.pricing()['version'], 'test-pricing-2')

    def test_pricing_changed_during_request_keeps_submission_rate_snapshot(self):
        self.pricing()
        ident = self.usage.begin_request(self.uid, 'project-one', 'job-one', 'model-test')
        self.pricing('test-pricing-2', 4, 24)
        self.usage.finish_request(ident, usage={'promptTokenCount': 1000, 'candidatesTokenCount': 100})
        value = self.usage.snapshot()['requests'][0]
        self.assertEqual(value['historicalEstimate']['pricingVersion'], 'test-pricing-1')
        self.assertAlmostEqual(value['historicalEstimate']['cost'], .0032)
        self.assertAlmostEqual(value['estimate']['cost'], .0064)

    def test_durable_request_key_prevents_duplicate_calls_even_after_interrupted_restart(self):
        ident = self.usage.begin_request(self.uid, 'project-one', 'job-one', 'model-test', request_key='event-1')
        with self.assertRaises(GeminiRequestAlreadyRecorded):
            self.record(key='event-1')
        self.usage.initialize()
        with self.assertRaises(GeminiRequestAlreadyRecorded):
            self.record(key='event-1')
        row = self.usage.snapshot()['requests'][0]
        self.assertEqual(row['id'], ident)
        self.assertEqual(row['status'], 'interrupted')
        self.assertIsNone(row['estimate']['cost'])

    def test_only_undispatched_started_reservation_can_be_discarded(self):
        ident = self.usage.begin_request(self.uid, 'project-one', 'job-one', 'model-test', request_key='unsent')
        self.assertEqual(self.usage.discard_unsubmitted(ident), 1)
        resumed = self.usage.begin_request(self.uid, 'project-one', 'job-one', 'model-test', request_key='unsent')
        self.usage.finish_request(resumed, status='failed')
        self.assertEqual(self.usage.discard_unsubmitted(resumed), 0)
        with self.assertRaises(GeminiRequestAlreadyRecorded):
            self.record(key='unsent')
        interrupted = self.usage.begin_request(self.uid, 'project-one', 'job-one', 'model-test', request_key='interrupted')
        self.usage.initialize()
        self.assertEqual(self.usage.discard_unsubmitted(interrupted), 0)

    def test_filtered_totals_cover_all_requests_not_only_visible_page(self):
        self.pricing()
        usage = {'total_input_tokens': 1000, 'total_output_tokens': 100, 'total_thought_tokens': 50, 'total_tokens': 1150}
        with patch('gemini_access.time.time', return_value=1_750_000_000):
            for index in range(3):
                self.record(usage=usage, job='job-' + str(index // 2))
        with patch('gemini_access.time.time', return_value=1_760_000_000):
            self.record(uid=self.other, usage=usage)
        result = self.usage.snapshot(limit=1, person=public_id(self.uid))
        self.assertEqual(len(result['requests']), 1)
        self.assertEqual(result['totals']['requests'], 3)
        self.assertAlmostEqual(result['totals']['estimatedCost'], .0114)
        self.assertTrue(result['hasMore'])
        self.assertEqual(len(result['byJob']), 2)
        self.assertEqual(len(result['byUser']), 1)
        self.assertEqual(len(result['byDay']), 1)
        self.assertEqual(self.usage.snapshot(since=1_759_000_000)['totals']['requests'], 1)
        self.access.request_access(self.uid)
        self.access.request_access(self.other)
        result = self.client.get('/api/admin/gemini?user=' + public_id(self.uid) + '&limit=1', headers=self.owner_headers)
        self.assertEqual(result.json['pendingCount'], 2)
        self.assertEqual(result.json['usage']['totals']['requests'], 3)

    def test_malformed_admin_filters_pricing_and_decisions_fail_closed(self):
        for query in ('limit=0', 'limit=2001', 'since=nan', 'until=inf', 'user=not-a-user', 'since=2&until=1'):
            self.assertEqual(self.client.get('/api/admin/gemini?' + query, headers=self.owner_headers).status_code, 400)
        for body in ([], {'userId': public_id(self.uid), 'decision': 'auto-approve'}, {'userId': 'wrong', 'decision': 'approved'}):
            self.assertEqual(self.client.post('/api/admin/gemini/access', headers=self.owner_headers, json=body).status_code, 400)
        self.assertEqual(self.client.put('/api/admin/gemini/pricing', headers=self.owner_headers, json={}).status_code, 400)
        with self.assertRaises(ValueError):
            validate_pricing({'version': 'bad', 'currency': 'USD', 'models': {'model': [
                {'effectiveFrom': '2026-01-01', 'inputPerMillion': 1, 'outputPerMillion': 2},
                {'effectiveFrom': '2027-01-01', 'inputPerMillion': 1, 'outputPerMillion': 2}]}})


if __name__ == '__main__':
    unittest.main()
