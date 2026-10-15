"""Deep orchestration protocol tests; fixtures do not measure model accuracy."""
import json
import unittest

import local_ultra as ultra
from local_runtime import LocalFailure
from test_local_long_response import Fixture, MESSAGES, result

PROFILE = dict(modelVariant='ultra', reasoningMode='deep', longResponses=True, thinking=True)


def consume(runtime, **changes):
    return list(ultra.response_events(runtime, MESSAGES, 1024, 32768, {**PROFILE, **changes}))


def visible(events):
    return ''.join((c.get('delta') or {}).get('content', '') for e in events for c in e.get('choices', []))


class DeepTests(unittest.TestCase):
    def test_fresh_review_and_corrected_final_with_original_evidence_and_usage(self):
        live = Fixture([[result('DRAFT_CLAIM: 7 times 8 is 54.', reasoning='PRIVATE_DRAFT')],
                        [result('REVIEW_CLAIM: product should be 56.', reasoning='PRIVATE_REVIEW')],
                        [result('The product is 56.', reasoning='PRIVATE_FINAL')]])
        events = consume(live)
        self.assertEqual(visible(events), 'The product is 56.')
        self.assertEqual(len(live.calls), 3)
        self.assertIn('DRAFT_CLAIM', live.calls[1]['messages'][-1]['content'])
        self.assertIn('REVIEW_CLAIM', live.calls[2]['messages'][-1]['content'])
        for call in live.calls:
            self.assertEqual(call['messages'][1], MESSAGES[1])
            self.assertTrue(call['messages'][0]['content'].startswith(MESSAGES[0]['content']))
            self.assertTrue(call['thinking'])
            self.assertLessEqual(live.count(call['messages']) + call['budget'] + ultra.CONTEXT_MARGIN, 32768)
            self.assertNotIn('PRIVATE_', json.dumps(call['messages']))
        self.assertNotIn('PRIVATE_', json.dumps(events))
        self.assertNotIn('DRAFT_CLAIM', json.dumps(events))
        self.assertNotIn('REVIEW_CLAIM', json.dumps(events))
        self.assertGreater(sum(e.get('localThinkingCharacters', 0) for e in events), 0)
        self.assertTrue(any(e.get('localThinking') for e in events))
        self.assertEqual([call['reasoning'] for call in live.calls], [4096, 2048, 4096])
        self.assertEqual([e['localReasoning']['stage'] for e in events if 'localPhase' in e and 'localReasoning' in e], ['draft', 'review', 'final'])
        self.assertEqual(events[-1]['localReasoning']['completedStages'], 3)
        self.assertEqual([e['usage'] for e in events if 'usage' in e][-1]['completion_tokens'], 60)
        self.assertTrue(live.thinking)
        self.assertIsNone(live.thinking_budget)

    def test_final_continuation_keeps_review_and_totals_without_redoing_hidden_stages(self):
        live = Fixture([[result('candidate')], [result('critique')],
                        [result('Final part one. ', 'length')], [result('Final part two.')]])
        events = consume(live)
        self.assertEqual(visible(events), 'Final part one. Final part two.')
        self.assertEqual(len(live.calls), 4)
        self.assertFalse(live.calls[-1]['thinking'])
        self.assertIn('critique', json.dumps(live.calls[-1]['messages']))
        self.assertEqual([e['usage'] for e in events if 'usage' in e][-1]['completion_tokens'], 80)

    def test_no_continuation_still_does_three_stages_and_preserves_length_result(self):
        live = Fixture([[result('candidate')], [result('critique')], [result('Partial final', 'length')]])
        events = consume(live, longResponses=False)
        self.assertEqual(len(live.calls), 3)
        self.assertEqual(visible(events), 'Partial final')
        self.assertEqual(events[-1]['localReasoning']['stage'], 'incomplete')
        self.assertEqual(events[-1]['localReasoning']['completedStages'], 2)

    def test_partial_review_is_labelled_for_final_and_user(self):
        live = Fixture([[result('candidate', 'length')], [result('partial review', 'length')], [result('Final')]])
        events = consume(live)
        self.assertEqual(events[-1]['localReasoning']['limitedStages'], ['draft', 'review'])
        self.assertIn('"partial": true', live.calls[-1]['messages'][-1]['content'])

    def test_no_thinking_setting_is_preserved_across_all_stages(self):
        live = Fixture([[result('candidate')], [result('critique')], [result('Final')]])
        live.thinking = False
        consume(live, thinking=False)
        self.assertTrue(all(c['reasoning'] == 0 and not c['thinking'] for c in live.calls))
        self.assertFalse(live.thinking)

    def test_stage_failure_never_publishes_unreviewed_draft_or_replays(self):
        for broken in [[result('')], [result('', 'length')], [{'error': 'fixture'}],
                       [{'choices': [{'delta': {'content': 'unfinished review'}}]}]]:
            live = Fixture([[result('SECRET_DRAFT')], broken, [result('must not run')]])
            events = []
            with self.assertRaises(LocalFailure):
                for event in ultra.response_events(live, MESSAGES, 1024, 32768, PROFILE):
                    events.append(event)
            self.assertEqual(visible(events), '')
            self.assertEqual(len(live.calls), 2)
            self.assertIsNone(live.thinking_budget)

    def test_stop_between_stages_prevents_next_request(self):
        live = Fixture([[result('candidate')], [result('must not run')]])
        events = ultra.response_events(live, MESSAGES, 1024, 32768, PROFILE)
        for event in events:
            if event.get('localReasoning', {}).get('completedStages') == 1:
                break
        live.cancelled = lambda: True
        with self.assertRaises(InterruptedError):
            list(events)
        self.assertEqual(len(live.calls), 1)
        self.assertIsNone(live.thinking_budget)

    def test_in_band_private_reasoning_never_becomes_review_evidence(self):
        live = Fixture([[result('<thi', None), result('nk>PRIVATE</think>candidate')]])
        with self.assertRaisesRegex(LocalFailure, 'separate private reasoning'):
            consume(live)
        self.assertEqual(len(live.calls), 1)

    def test_explicit_refusal_and_filter_are_not_retried(self):
        for first in [result('', 'content_filter'),
                      {'choices': [{'delta': {'refusal': 'Cannot assist.'}, 'finish_reason': 'stop'}]}]:
            live = Fixture([[first]])
            events = consume(live)
            self.assertEqual(len(live.calls), 1)
            self.assertEqual(events[-1]['localReasoning']['stage'], 'stopped')

    def test_context_overflow_never_drops_original_evidence_or_silently_falls_back(self):
        live = Fixture([[result('candidate')], [result('critique')]])
        # First stages fit; final with artifacts must fail closed.
        original_count = live.count
        live.count = lambda m: 32500 if 'stage 3' in m[0]['content'] and len(m) > 2 else original_count(m)
        with self.assertRaisesRegex(LocalFailure, 'No unreviewed draft'):
            consume(live)
        self.assertEqual(len(live.calls), 2)
        self.assertTrue(live.thinking)

    def test_default_validation_and_legacy_snapshots(self):
        self.assertEqual(ultra.normalize()['reasoningMode'], 'deep')
        self.assertFalse(ultra.deep_reasoning({'modelVariant': 'ultra'}))
        for value in (True, 'maximum', None, 3, {}):
            with self.assertRaises(LocalFailure):
                ultra.normalize({'reasoningMode': value})
        live = Fixture([[result('Standard')]])
        consume(live, reasoningMode='standard')
        self.assertEqual(len(live.calls), 1)


if __name__ == '__main__':
    unittest.main()
