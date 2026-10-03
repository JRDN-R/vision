"""Temporal and asset-boundary regression tests; no model download required."""
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sound_model as sound


def event(start, end, label='Meow', score=0.9):
    return {'start': start, 'end': end, 'label': label, 'score': score}


class SoundTimelineTests(unittest.TestCase):
    def test_padded_tail_keeps_training_grid_and_section_offset(self):
        row = [0.0] * 250
        row[10:80] = [0.8] * 70
        output = sound.decode_window([row], ['Meow'], offset=120, duration=1.1, median_frames=1)
        self.assertEqual(output, [event(120.4, 121.1, score=0.8)])

    def test_predictions_entirely_in_padding_disappear(self):
        row = [0.0] * 200 + [0.9] * 50
        self.assertEqual(sound.decode_window([row], ['Meow'], duration=2, median_frames=1), [])

    def test_median_removes_impulse_without_removing_sustained_sound(self):
        row = [0.0] * 250
        row[5] = 0.99
        row[25:50] = [0.9] * 25
        output = sound.decode_window([row], ['Meow'])
        self.assertEqual(output, [event(1, 2)])

    def test_chunk_boundary_same_event_merges(self):
        output = sound.postprocess_events([event(9.8, 10), event(10, 10.6, score=0.8)])
        self.assertEqual(output, [event(9.8, 10.6)])

    def test_short_fragments_merge_before_duration_filter(self):
        output = sound.postprocess_events([event(1, 1.08), event(1.12, 1.2)])
        self.assertEqual(output, [event(1, 1.2)])

    def test_nonverbal_voice_is_retained_speech_removed(self):
        labels = ['Speech', 'Male speech, man speaking', 'Human voice', 'Wail, moan', 'Groan', 'Laughter', 'Meow']
        output = sound.decode_window([[0.8] * 250 for _ in labels], labels)
        self.assertEqual([e['label'] for e in output], ['Wail, moan', 'Groan', 'Laughter', 'Meow'])

    def test_hierarchy_suppresses_only_matching_coverage(self):
        ancestors = {'Meow': {'Cat', 'Animal'}, 'Cat': {'Animal'}}
        self.assertEqual(sound.postprocess_events([event(1, 2, 'Cat', .85), event(1, 2)], ancestors), [event(1, 2)])
        retained = sound.postprocess_events([event(0, 10, 'Cat', .85), event(4, 5)], ancestors)
        self.assertEqual(len(retained), 2)

    def test_low_confidence_child_does_not_erase_parent(self):
        events = [event(1, 2, 'Cat', .95), event(1, 2, 'Meow', .55)]
        self.assertEqual(len(sound.postprocess_events(events, {'Meow': {'Cat'}})), 2)

    def test_ontology_handles_multiple_parents_and_cycle_defensively(self):
        ontology = [{'id': 'a', 'name': 'Animal', 'child_ids': ['b']},
                    {'id': 'b', 'name': 'Cat', 'child_ids': ['c']},
                    {'id': 'c', 'name': 'Meow', 'child_ids': []},
                    {'id': 'v', 'name': 'Vocalization', 'child_ids': ['c']}]
        self.assertEqual(sound.ontology_ancestors(ontology)['Meow'], {'Animal', 'Cat', 'Vocalization'})

    def test_srt_uses_absolute_clock_and_nonverbal_markers(self):
        text = sound.sound_events_srt([event(3661.125, 3662.5)])
        self.assertEqual(text, '1\n01:01:01,125 --> 01:01:02,500\n*cat meows*')

    def test_malformed_predictions_fail_instead_of_shifted_timestamps(self):
        for row in ([0.9] * 249, [float('nan')] * 250, [1.1] * 250):
            with self.assertRaises(ValueError):
                sound.decode_window([row], ['Meow'])
        for bad in (event(-1, 2), event(2, 1), event(0, float('inf'))):
            with self.assertRaises(ValueError):
                sound.postprocess_events([bad])

    def test_capability_needs_actual_success_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = root / 'python.exe'
            python.touch()
            (root / 'BEATs_strong_1.pt').touch()
            settings = {'enabled': True, 'pythonPath': str(python), 'assetsPath': str(root), 'device': 'cpu'}
            with patch.object(sound, '_manifest', return_value={'files': [{'path': 'BEATs_strong_1.pt'}]}):
                self.assertFalse(sound.capability(settings)['available'])
                (root / 'ready.json').write_text(json.dumps({'engine': sound.ENGINE,
                    'upstreamCommit': sound.UPSTREAM_COMMIT, 'checkpointSha256': sound.CHECKPOINT_SHA256, 'device': 'cpu'}))
                self.assertTrue(sound.capability(settings)['available'])
                self.assertFalse(sound.capability(settings | {'enabled': False})['available'])
                self.assertFalse(sound.capability(settings | {'device': 'cuda'})['available'])

    def test_asset_checksum_and_path_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'model').write_bytes(b'wrong')
            with patch.object(sound, '_manifest', return_value={'files': [{'path': 'model', 'sha256': '0' * 64}]}):
                with self.assertRaisesRegex(RuntimeError, 'checksum'):
                    sound.verify_assets(root)
            with patch.object(sound, '_manifest', return_value={'files': [{'path': '../outside', 'sha256': '0' * 64}]}):
                with self.assertRaisesRegex(RuntimeError, 'missing'):
                    sound.verify_assets(root)


if __name__ == '__main__':
    unittest.main()
