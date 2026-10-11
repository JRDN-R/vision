"""Real yt-dlp selection under production limits; no live-source claims."""
import copy
import io
import importlib.util
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import tempfile
from pathlib import Path

import yt_dlp
import vortex_worker as worker
import vortex_network as network
from yt_dlp.extractor.twitter import TwitterIE
import vortex_adapters as adapters


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

    def test_x_status_identity_and_syndication_quotes_are_checked_before_extraction(self):
        own, quoted = {'id_str': 'own', 'type': 'video'}, {'id_str': 'quoted', 'type': 'video'}
        payload = dict(id_str='123', mediaDetails=[own], extended_entities={'media': [own, quoted]},
                       quoted_tweet={'id_str': '999'}, quoted_status={'id_str': '999'})
        def status(_self, _id): return payload
        with patch.object(TwitterIE, '_extract_status', status):
            worker.configure_twitter_identity()
            result = TwitterIE._extract_status(None, '123')
            self.assertEqual(result['extended_entities']['media'], [own])
            self.assertNotIn('quoted_status', result)
            self.assertNotIn('quoted_tweet', result)
            with self.assertRaisesRegex(worker.SourceError, 'different media'):
                TwitterIE._extract_status(None, '456')

    @unittest.skipUnless(importlib.util.find_spec('gallery_dl'), 'Specialist CI installs gallery-dl')
    def test_gallery_real_twitter_parser_uses_fitting_variant_and_actual_dimensions(self):
        from gallery_dl import extractor
        instance = extractor.find('https://x.com/u/status/123')
        instance.initialize()
        variants = [dict(content_type='video/mp4', url=f'https://video.twimg.com/vid/{w}x{h}/a.mp4', bitrate=b)
                    for w,h,b in ((640,360,600000),(1920,1080,6000000))]
        item = dict(type='video', original_info={'width':1920,'height':1080},
                    video_info={'duration_millis':8700672,'variants':variants})
        worker.configure_gallery_twitter(instance, {'quality':'balanced','maxBytes':2*1024**3})
        result = []
        instance._extract_media({'id_str':'123'}, [item], result)
        self.assertEqual((result[0]['width'],result[0]['height']), (640,360))
        self.assertEqual(result[0]['url'], variants[0]['url'])
        self.assertEqual(len(item['video_info']['variants']), 2)
        # An unselected oversized attachment must not block the chosen video,
        # nor may gallery-dl's output renumbering change the source position.
        other = copy.deepcopy(item)
        other['video_info']['duration_millis'] = 20_000_000
        worker.configure_gallery_twitter(instance, {'input':'https://x.com/u/status/123/video/2',
                                                     'quality':'balanced','maxBytes':2*1024**3})
        result = []
        instance._extract_media({'id_str':'123'}, [other, item], result)
        self.assertEqual(result[0]['_vortex_attachment'], 2)
        self.assertEqual(len(result), 1)
        instance.session.close()

    def test_original_vimeo_uses_public_player_with_exact_same_id(self):
        calls = []
        def extract(ydl, value, download=False):
            calls.append(value)
            if value == 'https://vimeo.com/76979871':
                raise yt_dlp.utils.DownloadError('HTTP Error 401')
            return ydl.process_ie_result(dict(id='76979871', title='Fixture', duration=10,
                webpage_url=value, formats=[self.video(360)]), download=False)
        with tempfile.TemporaryDirectory() as directory, patch.object(network, 'resolve_public'), \
                patch.object(worker, 'configure_ytdlp_safety'), patch.object(worker, 'EVENT_STREAM', io.StringIO()), \
                patch.object(yt_dlp.YoutubeDL, 'extract_info', extract):
            result = worker.run_ytdlp(dict(input='https://vimeo.com/76979871', directory=directory, kind='inspect'))
        self.assertEqual(calls, ['https://vimeo.com/76979871', 'https://player.vimeo.com/video/76979871'])
        self.assertEqual(result['media']['url'], calls[0])

    def test_direct_adapter_cannot_replace_expected_duration_with_truncated_probe(self):
        probe = dict(streams=[{'codec_type':'video','codec_name':'h264','height':360},
                              {'codec_type':'audio','codec_name':'aac'}], format={'duration':'1'})
        with tempfile.TemporaryDirectory() as directory, patch.object(worker, 'probe_file', return_value=probe), \
                patch.object(worker, 'export_media', side_effect=lambda path, media, request: path):
            artifact = Path(directory)/'source.mp4'
            artifact.write_bytes(b'invalid truncated fixture')
            request = dict(kind='download', directory=directory, maxBytes=1024)
            result = adapters.finish_artifact(artifact, {'mediaType':'video','duration':60}, request)
            with self.assertRaisesRegex(worker.WorkerError, 'incomplete'):
                worker.verify_result(result, request)

    def test_native_http_failure_keeps_category_without_signed_url(self):
        secret_error = yt_dlp.utils.DownloadError('HTTP Error 403: https://cdn.example.com/media?token=secret')
        for value in ('https://vimeo.com/76979871', 'https://www.youtube.com/watch?v=YE7VzlLtp-4'):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory, \
                    patch.object(worker, 'configure_ytdlp_safety'), patch.object(worker, 'EVENT_STREAM', io.StringIO()), \
                    patch.object(yt_dlp.YoutubeDL, 'extract_info', side_effect=secret_error):
                with self.assertRaises(worker.SourceError) as failure:
                    worker.run_ytdlp(dict(input=value, directory=directory, kind='inspect'))
            self.assertEqual(worker.error_category(failure.exception), 'authentication')
            self.assertNotIn('token', str(failure.exception))
            self.assertNotIn('cdn.example.com', str(failure.exception))


if __name__ == '__main__':
    unittest.main()
