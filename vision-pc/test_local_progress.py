import json
import unittest

from local_progress import SummaryParser, ProgressNotes, summary_events
from test_local_inference import LocalInferenceTests, FakeRuntime
from unittest.mock import patch


class ParserTests(unittest.TestCase):
    def test_split_markers_and_raw_reasoning_never_exposed(self):
        parser = SummaryParser()
        result = []
        text = 'PRIVATE before <progress_summary>Checking the supplied information before choosing an approach.</progress_summary> PRIVATE after'
        for char in text:
            result += parser.feed(char)
        self.assertEqual(result, ['Checking the supplied information before choosing an approach.'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_invalid_and_overlong_notes_are_not_clipped(self):
        parser = SummaryParser()
        for text in ['One two', 'word '*31, '<script>bad bad bad</script>', 'word '*10000]:
            self.assertEqual(parser.feed('<progress_summary>'+text+'</progress_summary>'), [])
            self.assertLessEqual(len(parser.buffer), 2048)
        self.assertEqual(parser.feed('<progress_summary>Checking this information.</progress_summary>'), ['Checking this information.'])
        self.assertEqual(parser.feed('<progress_summary>Unfinished notes are never shown'), [])

    def test_stream_preserves_answers_and_strips_reasoning(self):
        events = [dict(choices=[dict(delta=dict(reasoning_content='PRIVATE <progress_summary>Comparing the available approaches.</progress_summary>', content='Answer'), finish_reason='stop')])]
        result = list(summary_events(events))
        self.assertEqual(result[0]['localProgressSummaries'], ['Comparing the available approaches.'])
        self.assertEqual(result[0]['choices'][0]['delta'], {'content':'Answer'})
        self.assertEqual(result[0]['choices'][0]['finish_reason'], 'stop')
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_extended_response_keeps_only_public_summaries(self):
        from test_local_long_response import Fixture, result, consume
        runtime = Fixture([[result('First part. ', 'length', 'PRIVATE <progress_summary>Comparing the available options.</progress_summary>')], [result('Final part.')]])
        events, answer = consume(runtime)
        self.assertEqual(answer, 'First part. Final part.')
        self.assertEqual([note for e in events for note in e.get('localProgressSummaries', [])], ['Comparing the available options.'])
        self.assertNotIn('PRIVATE', json.dumps(events))

    def test_notes_deduplicate_and_bound_history(self):
        notes = ProgressNotes()
        self.assertTrue(notes.add('Working through your request.', key='thinking'))
        self.assertFalse(notes.add('Working through your request.', key='thinking'))
        self.assertTrue(notes.add('Checking supplied facts.', source='model'))
        self.assertFalse(notes.add('Checking supplied facts.', source='model'))
        for index in range(400):
            notes.add('Checking section '+str(index)+'.', source='model')
        self.assertEqual(len(notes.steps), 128)
        self.assertEqual(notes.steps[-1]['id'], 402)
        self.assertLessEqual(len(notes.seen_notes), 256)


class ProgressIntegrationTests(LocalInferenceTests):
    def test_public_notes_persist_without_private_reasoning_and_are_owner_scoped(self):
        class Runtime(FakeRuntime):
            def generate(self, messages, max_tokens):
                self.test_messages = messages
                yield {'choices':[{'delta':{'reasoning_content':'PRIVATE <progress_sum'}}]}
                yield {'choices':[{'delta':{'reasoning_content':'mary>Checking the measurements in the attached reference.</progress_summary> PRIVATE'}}]}
                yield {'choices':[{'delta':{'content':'The answer is ready.'}}]}
                yield {'choices':[{'delta':{},'finish_reason':'stop'}]}
        self.local.runtime_factory = Runtime
        with patch.object(self.s, 'call', side_effect=AssertionError('No extra cloud summarizer')):
            rid = self.send().json['runId']
            self.s.work_once()
        saved = self.s.snapshot(rid)
        self.assertEqual(saved['status'], 'completed', saved)
        steps = saved['localQueue']['progress']['steps']
        self.assertIn('Checking the measurements in the attached reference.', [s['text'] for s in steps])
        self.assertNotIn('PRIVATE', json.dumps(saved))
        self.assertNotIn('PRIVATE', self.s.row(rid)['local_progress_json'])
        self.assertTrue(all(3 <= len(s['text'].split()) <= 30 for s in steps))
        path='/api/projects/'+self.cid+'/runs/'+rid
        self.assertEqual(self.client.get(path, headers=self.b).status_code, 404)
        self.assertEqual(self.client.get(path, headers=self.a).json['localQueue']['progress']['steps'], steps)
