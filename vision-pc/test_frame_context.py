"""Offline deterministic tests for bounded transcript-grounded frame captions."""
import unittest
from frame_context import (video_title, timed_cues, narration, caption_prompt,
                           MAX_PROMPT_CHARS)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.title = 'Samsung Dryer Door Reversal Quick Guide.mp4'
        self.speech = {'text': (
            '[00:05:55.000] Move to the inner door panel.\n'
            '[00:06:00.000] Remove the ten screws from the latch.\n'
            '[00:06:06.000] Keep the two longer screws separate.'
        )}

    def test_source_title_is_not_project_name(self):
        self.assertEqual(video_title(r'C:\temp\Samsung Dryer Door.mp4'), 'Samsung Dryer Door')

    def test_timed_transcript_and_nearby_context(self):
        cues = timed_cues(self.speech)
        self.assertEqual(len(cues), 3)
        self.assertIn('ten screws', narration(cues, 360))
        self.assertEqual(narration(cues, 300), '')

    def test_speech_segments_preferred_over_sound_tracks(self):
        cues = timed_cues({'speechSegments': [{'start': 360, 'text': 'Remove the hinge screws.'}],
                           'text': '[00:06:00.000] SOUND: explosion'})
        self.assertEqual(cues, [(360, 'Remove the hinge screws.')])

    def test_context_budget_and_prior_guard(self):
        cues = timed_cues(self.speech)
        prior = {'index': 17, 'description': 'A plastic door is resting flat on the workbench.'}
        prompt, context = caption_prompt(self.title, cues, 360, prior, True)
        self.assertIn('Samsung Dryer', prompt)
        self.assertIn('ten screws', prompt)
        self.assertIn('Prior caption', prompt)
        self.assertIn('screws', context['keywords'])
        self.assertEqual(context['previousFrame'], 17)
        self.assertLessEqual(len(prompt), MAX_PROMPT_CHARS)
        prompt, context = caption_prompt(self.title, cues, 360, prior, False)
        self.assertNotIn('Prior caption', prompt)
        self.assertIsNone(context['previousFrame'])

    def test_untrusted_text_cannot_overflow_context(self):
        title = 'x' * 300
        cues = [(100, 'cat ' * 500)] * 60
        prompt, _ = caption_prompt(title, cues, 100,
                                   {'index': 1, 'description': 'z' * 1000}, True)
        self.assertLessEqual(len(prompt), MAX_PROMPT_CHARS)
        self.assertIn('Reference clues below are NOT image evidence', prompt)


if __name__ == '__main__':
    unittest.main()
