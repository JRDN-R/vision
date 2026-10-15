"""Real accepted jobs/storage/cancellation with an explicit model fixture."""
import json
import unittest
from unittest.mock import patch

import local_ultra as ultra
import test_local_inference as base
import test_local_ultra as fixtures
from test_local_long_response import result


class DeepIntegrationTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.UltraIntegrationTests.setUpClass.__func__)
    setUp = fixtures.UltraIntegrationTests.setUp
    tearDown = fixtures.UltraIntegrationTests.tearDown
    token = fixtures.UltraIntegrationTests.token
    headers = fixtures.UltraIntegrationTests.headers
    new = fixtures.UltraIntegrationTests.new
    send = fixtures.UltraIntegrationTests.send

    def test_default_deep_survives_browser_reopen_and_only_saves_final_answer(self):
        owner, calls = self, []
        class ThreeStages(base.FakeRuntime):
            def generate(self, messages, budget):
                calls.append(messages)
                snapshot = owner.client.get('/api/projects/' + owner.cid + '/runs/' + rid, headers=owner.a).json
                owner.assertEqual(snapshot['text'], '')
                owner.assertEqual(snapshot['localQueue']['metrics']['reasoning']['stageNumber'], len(calls))
                owner.assertIsNone(snapshot['localQueue']['estimatedTotalSeconds'])
                yield result(['HIDDEN_DRAFT: 54', 'HIDDEN_REVIEW: 56', 'The product is 56.'][len(calls)-1],
                             reasoning='PRIVATE_REASONING')
        self.local.runtime_factory = ThreeStages
        with patch.object(self.s, 'call', side_effect=AssertionError('Unexpected cloud request')):
            sent = self.send(model=ultra.MODEL, message='What is 7 times 8?', qwenUltra={'longResponses': False})
            self.assertEqual(sent.status_code, 202, sent.json)
            rid = sent.json['runId']
            profile = json.loads(self.s.row(rid)['local_profile_json'])
            self.assertEqual(profile['reasoningMode'], 'deep')
            self.s.work_once()
        saved = self.s.snapshot(rid)
        self.assertEqual(saved['status'], 'completed', saved)
        self.assertEqual(saved['text'], 'The product is 56.')
        self.assertEqual(saved['localQueue']['reasoningMode'], 'deep')
        self.assertEqual(saved['localQueue']['metrics']['reasoning']['completedStages'], 3)
        self.assertEqual(saved['localQueue']['metrics']['usage']['completion_tokens'], 60)
        serialized = json.dumps(self.s.row(rid))
        for hidden in ('PRIVATE_REASONING', 'HIDDEN_DRAFT', 'HIDDEN_REVIEW'):
            self.assertNotIn(hidden, serialized)
        with self.local.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ultra_timing_samples').fetchone()[0], 0)
        self.assertEqual(self.client.get('/api/projects/' + self.cid + '/runs/' + rid, headers=self.b).status_code, 404)

    def test_stop_during_review_retains_no_unreviewed_answer(self):
        owner, calls = self, []
        class StopReview(base.FakeRuntime):
            def generate(self, messages, budget):
                calls.append(messages)
                if len(calls) == 2:
                    response = owner.client.post('/api/projects/' + owner.cid + '/runs/' + rid + '/cancel', headers=owner.a)
                    owner.assertEqual(response.status_code, 200)
                yield result('unreviewed text')
        self.local.runtime_factory = StopReview
        sent = self.send(model=ultra.MODEL)
        self.assertEqual(sent.status_code, 202, sent.json)
        rid = sent.json['runId']
        self.s.work_once()
        self.assertEqual(self.s.snapshot(rid)['status'], 'cancelled')
        self.assertEqual(self.s.snapshot(rid)['text'], '')
        self.assertEqual(len(calls), 2)

    def test_queued_deep_settings_are_immutable_and_standard_is_explicit(self):
        sent = self.send(model=ultra.MODEL, qwenUltra={'reasoningMode': 'standard'})
        self.assertEqual(sent.status_code, 202, sent.json)
        rid = sent.json['runId']
        self.assertEqual(json.loads(self.s.row(rid)['local_profile_json'])['reasoningMode'], 'standard')
        self.s.work_once()
        self.assertEqual(self.s.snapshot(rid)['status'], 'completed')
        self.assertEqual(self.s.snapshot(rid)['localQueue']['reasoningMode'], 'standard')
        self.assertEqual(len(base.FakeRuntime.requests), 1)


if __name__ == '__main__':
    unittest.main()
