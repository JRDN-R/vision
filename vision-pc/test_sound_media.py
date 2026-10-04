"""Keep source audio for sound recognition even when YouTube captions exist."""
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from PIL import Image
import media


class YouTubeSoundMediaTests(unittest.TestCase):
    def test_captioned_video_retains_audio_only_when_sounds_requested(self):
        jpeg = io.BytesIO()
        Image.new('RGB', (160, 90), 'black').save(jpeg, format='JPEG')
        for include_sounds, provider in ((False, 'local'), (True, 'local'), (False, 'gemini'), (True, 'gemini')):
            with self.subTest(include_sounds=include_sounds, provider=provider):
                formats, conversions, updates = [], [], []

                class FakeYouTubeDL:
                    def __init__(self, options):
                        self.options = dict(options)

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                    def extract_info(self, url, download):
                        if download:
                            formats.append(self.options['format'])
                            Path(self.options['outtmpl'] % {'ext': 'mkv'}).write_bytes(b'fake video')
                        return {'duration': 1, 'title': 'Captioned sample'}

                def run(command, **kwargs):
                    if command[-1] == '-':
                        return subprocess.CompletedProcess(command, 0, jpeg.getvalue(), b'')
                    conversions.append(command)
                    Path(command[-1]).write_bytes(b'fake audio')
                    return subprocess.CompletedProcess(command, 0, b'', b'')

                captions = {'text': '[00:00:00.000] Hello', 'language': 'en', 'source': 'manual'}
                with patch('yt_dlp.YoutubeDL', FakeYouTubeDL), \
                     patch.object(media, 'caption_result', return_value=captions), \
                     patch.object(media.subprocess, 'run', side_effect=run):
                    media.process_job('job', 'https://youtu.be/abcdefghijk', 'ffmpeg',
                                      lambda job, **values: updates.append(values),
                                      include_sound_events=include_sounds, provider=provider)

                self.assertEqual(updates[-1]['status'], 'complete', updates[-1])
                result = json.loads(updates[-1]['result'])
                self.assertEqual(result['transcript'], None if provider == 'gemini' else captions)
                self.assertEqual(result['includeSoundEvents'], include_sounds)
                needs_audio = include_sounds or provider == 'gemini'
                self.assertEqual(bool(result['audio']), needs_audio)
                self.assertEqual(len(conversions), int(needs_audio))
                self.assertEqual('+ba' in formats[0], needs_audio)
                if needs_audio:
                    self.assertTrue(result['audio']['data'].startswith('data:audio/mp4;base64,'))


if __name__ == '__main__':
    unittest.main()
