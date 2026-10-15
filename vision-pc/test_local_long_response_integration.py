"""Durable-job tests with a fake model. No model weights or cloud API calls."""
import json
import unittest
from unittest.mock import patch

import local_ultra as ultra
from local_runtime import LocalFailure
from setup_local_variant import atomic_json
import test_local_inference as local_tests
import test_local_ultra as ultra_tests


class LongResponseIntegrationTests(unittest.TestCase):
    setUpClass = classmethod(ultra_tests.UltraIntegrationTests.setUpClass.__func__)
    token = ultra_tests.UltraIntegrationTests.token
    headers = ultra_tests.UltraIntegrationTests.headers
    new = ultra_tests.UltraIntegrationTests.new
    def send(self, **options):
        options['qwenUltra'] = {'reasoningMode': 'standard', **options.get('qwenUltra', {})}
        return ultra_tests.UltraIntegrationTests.send(self, **options)
    setUp = ultra_tests.UltraIntegrationTests.setUp
    tearDown = ultra_tests.UltraIntegrationTests.tearDown

    def test_two_pass_answer_is_saved_between_passes_and_reopens_without_browser(self):
        owner = self
        calls = []
        class Continue(local_tests.FakeRuntime):
            def generate(self, messages, budget):
                calls.append((messages, budget))
                if len(calls) == 1:
                    yield {'choices': [{'delta': {'reasoning_content': 'PRIVATE'}, 'finish_reason': None}]}
                    yield {'choices': [{'delta': {'content': 'First section. '}, 'finish_reason': 'length'}],
                           'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}}
                else:
                    owner.assertEqual(owner.s.row(rid)['text'], 'First section. ')
                    owner.assertFalse(self.thinking)
                    yield {'choices': [{'delta': {'content': 'Final section.'}, 'finish_reason': 'stop'}],
                           'usage': {'prompt_tokens': 120, 'completion_tokens': 12, 'total_tokens': 132}}
        self.local.runtime_factory = Continue
        with patch.object(self.s, 'call', side_effect=AssertionError('Unexpected cloud request')):
            sent = self.send(model=ultra.MODEL, message='Explain merge sort. Long answer, please.')
            self.assertEqual(sent.status_code, 202, sent.json)
            rid = sent.json['runId']
            self.s.work_once()  # No browser stream or polling request is open.
        saved = self.s.snapshot(rid)
        self.assertEqual(saved['status'], 'completed', saved)
        self.assertEqual(saved['text'], 'First section. Final section.')
        self.assertTrue(saved['localQueue']['longResponse'])
        self.assertEqual(saved['localQueue']['metrics']['generation']['completedPasses'], 2)
        self.assertEqual(saved['localQueue']['metrics']['usage']['completion_tokens'], 22)
        self.assertNotIn('PRIVATE', json.dumps(saved))
        self.assertGreater(calls[0][1], 4096)
        reopened = self.client.get('/api/projects/'+self.cid+'/runs/'+rid, headers=self.a)
        self.assertEqual(reopened.json['text'], saved['text'])
        self.assertEqual(self.client.get('/api/projects/'+self.cid+'/runs/'+rid, headers=self.b).status_code, 404)
        with self.local.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ultra_timing_samples').fetchone()[0], 0)

    def test_later_pass_error_preserves_output_without_replay(self):
        calls = []
        class FailLater(local_tests.FakeRuntime):
            def generate(self, messages, budget):
                calls.append(messages)
                if len(calls) > 1:
                    raise LocalFailure('Fixture connection failure')
                yield {'choices': [{'delta': {'content': 'A useful saved section.'}, 'finish_reason': 'length'}]}
        self.local.runtime_factory = FailLater
        rid = self.send(model=ultra.MODEL).json['runId']
        self.s.work_once()
        saved = self.s.snapshot(rid)
        self.assertEqual(saved['status'], 'error', saved)
        self.assertEqual(saved['text'], 'A useful saved section.')
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.s.work_once())

    def test_stop_request_between_passes_preserves_first_section(self):
        owner = self
        calls = []
        class CancelLater(local_tests.FakeRuntime):
            def generate(self, messages, budget):
                calls.append(messages)
                yield {'choices': [{'delta': {'content': 'Saved before stopping.'}, 'finish_reason': 'length'}]}
                owner.s.update(rid, cancel_requested=1)
        self.local.runtime_factory = CancelLater
        rid = self.send(model=ultra.MODEL).json['runId']
        self.s.work_once()
        saved = self.s.snapshot(rid)
        self.assertEqual(saved['status'], 'cancelled', saved)
        self.assertEqual(saved['text'], 'Saved before stopping.')
        self.assertEqual(len(calls), 1)

    def test_larger_context_is_gated_then_not_clamped_to_old_16k_limit(self):
        self.assertNotEqual(self.send(model=ultra.MODEL, qwenUltra={'contextTokens': 32768}).status_code, 202)
        self.receipt['maxContextTokens'] = 32768
        atomic_json(self.receipt_path, self.receipt)
        budgets = []
        class Larger(local_tests.FakeRuntime):
            def generate(self, messages, budget):
                budgets.append(budget)
                yield from super().generate(messages, budget)
        self.local.runtime_factory = Larger
        sent = self.send(model=ultra.MODEL, qwenUltra={'contextTokens': 32768}, request_id='larger')
        self.assertEqual(sent.status_code, 202, sent.json)
        self.s.work_once()
        saved = self.s.snapshot(sent.json['runId'])
        self.assertEqual(saved['status'], 'completed', saved)
        self.assertEqual(saved['localQueue']['metrics']['contextTokens'], 32768)
        self.assertGreater(budgets[0], 16384)

    def test_explicit_single_pass_choice_and_frozen_job_snapshot(self):
        class Exhausted(local_tests.FakeRuntime):
            def generate(self, messages, budget):
                self.assert_budget = budget
                yield {'choices': [{'delta': {'content': 'Partial.'}, 'finish_reason': 'length'}]}
        self.local.runtime_factory = Exhausted
        sent = self.send(model=ultra.MODEL, qwenUltra={'longResponses': False})
        self.assertEqual(sent.status_code, 202, sent.json)
        rid = sent.json['runId']
        profile = json.loads(self.s.row(rid)['local_profile_json'])
        self.assertFalse(profile['longResponses'])
        self.s.work_once()
        saved = self.s.snapshot(rid)
        self.assertEqual(saved['status'], 'incomplete', saved)
        self.assertFalse(saved['localQueue']['longResponse'])
        self.assertNotIn('generation', saved['localQueue']['metrics'])


if __name__ == '__main__':
    unittest.main()
