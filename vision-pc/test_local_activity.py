"""Stream observability: model output, transport contact and silence are distinct."""
import json
import unittest
from unittest.mock import patch

from local_progress import StreamActivity, activity_snapshot
from local_runtime import LlamaRuntime
from test_local_long_response import Fixture, result, consume as long_consume
from test_local_reasoning import consume as deep_consume, visible


class ActivityTests(unittest.TestCase):
    def test_heartbeats_do_not_fake_output_and_counts_are_not_chunk_counts(self):
        with patch('local_progress.time.time', return_value=100):
            stream = StreamActivity(1)
        self.assertIsNone(stream.snapshot()['lastEventAt'])
        with patch('local_progress.time.time', return_value=105):
            self.assertIsNone(stream.observe()['lastOutputAt'])
        with patch('local_progress.time.time', return_value=110):
            first = stream.observe(result('', finish=None, reasoning='SECRET', usage={}) |
                                   {'timings': {'predicted_n': 17, 'predicted_per_second': 1.37}})
        with patch('local_progress.time.time', return_value=400):
            contact = stream.observe()
        self.assertEqual(first['generatedTokens'], 17)
        self.assertEqual(contact['generatedTokens'], 17)
        self.assertEqual(contact['lastOutputAt'], 110)
        self.assertEqual(contact['receivedCharacters'], 6)
        self.assertNotIn('SECRET', json.dumps(contact))
        ages = activity_snapshot(contact, 420)
        self.assertEqual((ages['secondsSinceEvent'], ages['secondsSinceOutput']), (20, 310))
        self.assertEqual(activity_snapshot(contact, 520)['secondsSinceOutput'], 410)
        with patch('local_progress.time.time', return_value=530):
            stale = stream.observe({'timings': {'predicted_n': 10}})
        self.assertEqual(stale['generatedTokens'], 17)
        self.assertEqual(stale['lastOutputAt'], 110)
        self.assertIsNone(StreamActivity(2).snapshot()['generatedTokens'])

    def test_actual_runtime_stream_reports_contacts_without_copying_text_into_metrics(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def iter_lines(self, **kwargs):
                return iter([b':', b'', b'data: ' + json.dumps({
                    'choices': [{'delta': {'reasoning_content': 'PRIVATE'}}],
                    'timings': {'predicted_n': 4, 'predicted_per_second': 1.5}}).encode(),
                    b'data: ' + json.dumps(result('Answer')).encode(), b'data: [DONE]'])
        runtime = LlamaRuntime({}, lambda: False, lambda _: None)
        self.addCleanup(runtime.http.close)
        with patch.object(runtime, 'request', return_value=Response()) as request:
            events = list(runtime.generate([], 1024))
            self.assertTrue(request.call_args.args[1]['timings_per_token'])
            second = list(runtime.generate([], 1024))
        self.assertEqual(len(events), 4)  # Start, heartbeat, reasoning, answer.
        self.assertIsNone(events[1]['localActivity']['lastOutputAt'])
        self.assertEqual(events[2]['localActivity']['generatedTokens'], 4)
        self.assertTrue(events[-1]['localActivity']['finished'])
        self.assertEqual(second[0]['localActivity']['requestNumber'], 2)
        self.assertNotIn('PRIVATE', json.dumps([e['localActivity'] for e in events]))

    def test_all_deep_stages_and_continuations_forward_only_measurements(self):
        def stage(number, text, finish='stop'):
            return [result(text, finish, reasoning='PRIVATE') | {
                'localActivity': {'version': 1, 'requestNumber': number, 'generatedTokens': 5}}]
        runtime = Fixture([stage(1, 'DRAFT'), stage(2, 'REVIEW'),
                           stage(3, 'Final ', 'length'), stage(4, 'answer.')])
        events = deep_consume(runtime)
        self.assertEqual(visible(events), 'Final answer.')
        self.assertEqual([e['localActivity']['requestNumber'] for e in events if 'localActivity' in e], [1, 2, 3, 4])
        for hidden in ('PRIVATE', 'DRAFT', 'REVIEW'):
            self.assertNotIn(hidden, json.dumps(events))
        events, text = long_consume(Fixture([stage(1, 'Standard')]))
        self.assertEqual(text, 'Standard')
        self.assertTrue(any(e.get('localActivity') for e in events))


if __name__ == '__main__':
    unittest.main()
