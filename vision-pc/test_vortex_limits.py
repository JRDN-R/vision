"""Real yt-dlp selection under production limits; no live-source claims."""
import copy
import io
import unittest
from unittest.mock import patch

import yt_dlp
import vortex_worker as worker


class LimitsTests(unittest.TestCase):
    def select(self, formats, duration=8700.672, audio=False):
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'logger': worker.QuietLogger()}) as ydl:
            ydl.format_selector = worker.bounded_selector(
                ydl, worker.format_selector('balanced', audio), [duration], 2 * 1024**3, audio=audio)
            return ydl.process_ie_result(dict(id='fixture', title='Fixture', duration=duration,
                formats=copy.deepcopy(formats)), download=False)

    def video(self, height, **extra):
        return dict(format_id=str(height), url=f'https://cdn.example.com/{height}.mp4',
                    ext='mp4', vcodec='h264', acodec='aac', height=height, **extra)

    def test_long_video_chooses_complete_smaller_stream_without_raising_cap(self):
        result = self.select([self.video(360, tbr=600), self.video(720, tbr=2500), self.video(1080, tbr=6000)])
        self.assertEqual(result['height'], 360)
        self.assertEqual(result['duration'], 8700.672)

    def test_separate_audio_and_video_share_one_byte_budget(self):
        big, small = self.video(1080, filesize=2_000_000_000), self.video(720, filesize=1_000_000_000)
        big['acodec'] = small['acodec'] = 'none'
        audio = dict(format_id='audio', url='https://cdn.example.com/a.m4a', ext='m4a',
                     vcodec='none', acodec='aac', filesize=200_000_000)
        result = self.select([small, big, audio])
        self.assertEqual([f['format_id'] for f in result['requested_formats']], ['720', 'audio'])

    def test_unknown_size_is_attempted_and_audio_only_sources_still_work(self):
        self.assertEqual(self.select([self.video(360), self.video(1080)])['height'], 1080)
        audio = dict(format_id='audio', url='https://cdn.example.com/a.m4a', ext='m4a', vcodec='none', acodec='aac')
        self.assertEqual(self.select([audio])['vcodec'], 'none')

    def test_oversized_video_does_not_turn_into_audio_success(self):
        audio = dict(format_id='audio', url='https://cdn.example.com/a.m4a', ext='m4a', vcodec='none', acodec='aac')
        with self.assertRaises(worker.WorkerError) as failure:
            self.select([self.video(360, filesize=3 * 1024**3), audio])
        self.assertEqual(failure.exception.category, 'file_size_limit')

    def test_duration_and_bytes_are_distinct_from_memory_and_disk(self):
        for error, category in ((worker.WorkerError('This file exceeds the server download size limit.'), 'file_size_limit'),
                                (worker.WorkerError('This media exceeds the configured duration limit.'), 'duration_limit'),
                                (MemoryError(), 'memory_limit'), (OSError(28, 'secret path'), 'disk_limit')):
            self.assertEqual(worker.error_category(error), category)


if __name__ == '__main__':
    unittest.main()
