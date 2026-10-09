"""Offline adapter/format tests with real yt-dlp selection and local fixtures."""
import builtins
import copy
import io
import json
import os
import shutil
import subprocess
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import yt_dlp

import vortex_worker as worker


class VortexAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.events = io.StringIO()
        self.output = patch.object(worker, 'EVENT_STREAM', self.events)
        self.output.start()
        self.url = patch.object(worker, 'safe_url', side_effect=lambda value: value if isinstance(value, str) and value.startswith('https://') else None)
        self.url.start()

    def tearDown(self):
        self.output.stop()
        self.url.stop()
        self.temp.cleanup()

    def request(self, **changes):
        value = {'input': 'https://media.example.com/video', 'kind': 'download',
                 'quality': 'balanced', 'directory': str(self.root), 'maxBytes': 100000,
                 'maxDuration': 7200, 'ffmpeg': str(self.root / 'ffmpeg')}
        value.update(changes)
        return value

    def choose(self, formats, quality, audio=False):
        # Use yt-dlp's real ranking and fallback implementation, not a string
        # comparison that could pass even when a preset selects the wrong type.
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True,
                              'format': worker.format_selector(quality, audio)}) as ydl:
            return ydl.process_ie_result({'id': 'fixture', 'title': 'Fixture media',
                'webpage_url': 'https://media.example.com/', 'formats': copy.deepcopy(formats)}, download=False)

    @staticmethod
    def video(height):
        return {'format_id': str(height), 'url': 'https://media.example.com/' + str(height),
                'width': height * 16 // 9, 'height': height, 'fps': 30, 'ext': 'mp4',
                'vcodec': 'avc1', 'acodec': 'aac', 'tbr': height}

    @staticmethod
    def audio(bitrate):
        return {'format_id': 'audio' + str(bitrate), 'url': 'https://media.example.com/audio' + str(bitrate),
                'ext': 'm4a', 'vcodec': 'none', 'acodec': 'aac', 'abr': bitrate, 'tbr': bitrate}

    def test_real_quality_selection_caps_video_and_max_preserves_best(self):
        formats = [self.video(height) for height in (360, 480, 720, 1080, 2160)]
        for quality, height in [('small', 480), ('balanced', 1080), ('max', 2160)]:
            with self.subTest(quality=quality):
                self.assertEqual(self.choose(formats, quality)['height'], height)

    def test_no_low_resolution_video_never_silently_downloads_audio_only(self):
        formats = [self.audio(128), self.audio(256), self.video(2160)]
        for quality in ('small', 'balanced', 'max'):
            with self.subTest(quality=quality):
                result = self.choose(formats, quality)
                self.assertEqual(result['height'], 2160)
                self.assertNotEqual(result['vcodec'], 'none')

    def test_audio_sources_keep_highest_original_audio_for_every_preset(self):
        formats = [self.audio(bitrate) for bitrate in (64, 128, 256)]
        for quality in ('small', 'balanced', 'max'):
            for explicit_audio in (False, True):
                with self.subTest(quality=quality, explicit_audio=explicit_audio):
                    self.assertEqual(self.choose(formats, quality, explicit_audio)['abr'], 256)

    def test_metadata_uses_selected_streams_and_probe_without_inventing_values(self):
        info = {'title': 'Live\nfixture', 'extractor_key': 'Example',
                'webpage_url': 'https://media.example.com/video',
                'requested_formats': [dict(self.video(2160), fps=60, acodec='none'), dict(self.audio(256), asr=48000, audio_channels=2)]}
        media = worker.media_from_info(info)
        self.assertEqual((media['height'], media['fps'], media['abr'], media['asr'], media['audioChannels']),
                         (2160, 60, 256, 48000, 2))
        self.assertEqual(media['title'], 'Live fixture')
        empty = worker.media_from_info({'_type': 'url', 'title': 'Search result'})
        self.assertEqual(empty['mediaType'], 'unknown')
        self.assertIsNone(empty['height'])
        self.assertIsNone(empty['fps'])
        probe = {'streams': [{'codec_type': 'video', 'width': 1920, 'height': 1080,
                              'codec_name': 'h264', 'avg_frame_rate': '30000/1001'},
                             {'codec_type': 'audio', 'codec_name': 'aac', 'sample_rate': '44100',
                              'bit_rate': '192000', 'channels': 2}], 'format': {'duration': '61.25'}}
        observed = worker.apply_probe(media, probe)
        self.assertEqual((observed['width'], observed['height'], observed['abr'], observed['asr']),
                         (1920, 1080, 192, 44100))
        self.assertAlmostEqual(observed['fps'], 29.97002997)
        self.assertEqual(observed['duration'], 61.25)
        self.assertIsNone(worker.number(float('inf')))
        self.assertIsNone(worker.number(float('nan')))

    def test_local_probe_cannot_open_external_or_playlist_inputs(self):
        local = self.root / 'media.mp4'
        local.write_bytes(b'fixture')
        probe = self.root / ('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
        probe.touch()
        with patch.object(worker.subprocess, 'run', return_value=SimpleNamespace(stdout=b'{"streams": []}')) as execute:
            self.assertEqual(worker.probe_file(local, str(self.root / 'ffmpeg'), self.root), {'streams': []})
            args = execute.call_args.args[0]
            self.assertEqual(args[args.index('-protocol_whitelist') + 1], 'file,pipe')
            allowed = args[args.index('-format_whitelist') + 1].split(',')
            for blocked in ('hls', 'dash', 'concat', 'image2', 'sdp'):
                self.assertNotIn(blocked, allowed)
            with self.assertRaises(worker.WorkerError):
                worker.probe_file(self.root.parent / 'outside.mp4', str(self.root / 'ffmpeg'), self.root)
            self.assertEqual(execute.call_count, 1)

    def test_specialist_dependencies_fail_with_actionable_messages(self):
        original = builtins.__import__
        def absent(name, *args, **kwargs):
            if name.split('.')[0] in ('spotdl', 'gallery_dl'):
                raise ImportError('simulated missing dependency')
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=absent):
            with self.assertRaisesRegex(worker.WorkerError, 'not installed'):
                worker.collect_gallery('https://instagram.com/p/example', 50)
            with self.assertRaisesRegex(worker.WorkerError, 'not installed'):
                worker.run_spotify(self.request(input='https://open.spotify.com/track/' + 'a' * 22))

    def test_actual_download_event_contract_and_unknown_progress(self):
        media = dict(self.video(1080), id='fixture', title='Real metadata',
                     webpage_url='https://media.example.com/video', duration=30)
        captured = {}
        root = self.root
        class FakeYoutubeDL:
            def __init__(self, options):
                captured.update(options)
            def __enter__(self): return self
            def __exit__(self, *_unused): return False
            def extract_info(self, value, download=False):
                return copy.deepcopy(media)
            def process_ie_result(self, info, download=True):
                hook = captured['progress_hooks'][0]
                hook({'status': 'downloading', 'downloaded_bytes': 4, 'total_bytes_estimate': 10})
                hook({'status': 'finished', 'downloaded_bytes': 10, 'total_bytes': 10})
                (root / 'media.mp4').write_bytes(b'0123456789')
                return info
        with patch.object(worker, 'configure_ytdlp_safety'), patch.object(yt_dlp, 'YoutubeDL', FakeYoutubeDL), \
                patch.object(worker, 'probe_file', return_value={}), \
                patch.object(worker, 'export_media', side_effect=lambda artifact, *_args: artifact):
            result = worker.run_ytdlp(self.request())
        self.assertTrue(result['complete'])
        self.assertEqual(result['filename'], 'media.mp4')
        self.assertEqual(result['results'], [result['media']])
        self.assertEqual(result['media']['title'], 'Real metadata')
        self.assertEqual(result['media']['height'], 1080)
        emitted = [json.loads(line) for line in self.events.getvalue().splitlines()]
        downloading = next(item for item in emitted if item['phase'] == 'Downloading media')
        self.assertIsNone(downloading['progress'], 'Estimated bytes must not become an invented percentage')
        self.assertEqual(captured['proxy'], '')
        self.assertIsNone(captured['cookiefile'])
        self.assertFalse(captured['usenetrc'])
        self.assertFalse(captured['allow_unplayable_formats'])

    def test_real_search_processing_keeps_long_live_results_and_adds_music(self):
        targets = []
        # Keep yt-dlp's real flat-playlist processing and match-filter calls.
        # Only replace the remote extractor response.
        def extract(ydl, value, download=False, **unused):
            targets.append(value)
            music = value.startswith('https://music.youtube.com/search?')
            entries = [dict(_type='url', ie_key='Youtube', id='abcdefghij1' if music else 'abcdefghij2',
                            url='https://www.youtube.com/watch?v=abcdefghij1' if music else 'https://www.youtube.com/watch?v=abcdefghij2',
                            title='Music match' if music else 'Eight hour result', duration=180 if music else 28800),
                       dict(_type='url', ie_key='Youtube', id='abcdefghij3',
                            url='https://www.youtube.com/watch?v=abcdefghij3', title='Live result', is_live=True)]
            return ydl.process_ie_result(dict(_type='playlist', id='search', title='Search', entries=entries,
                                              extractor='youtube:search', extractor_key='YoutubeSearch', webpage_url=value), download=False)
        with patch.object(worker, 'configure_ytdlp_safety'), patch.object(yt_dlp.YoutubeDL, 'extract_info', extract):
            result = worker.run_ytdlp(self.request(input='evening music', kind='inspect'))
        self.assertEqual(result['results'][0]['duration'], 28800)
        self.assertEqual(result['results'][1]['source'], 'YouTube Music')
        self.assertEqual(result['results'][1]['mediaType'], 'audio')
        self.assertEqual(result['results'][1]['url'], 'https://music.youtube.com/watch?v=abcdefghij1')
        self.assertTrue(any(item['title'] == 'Live result' for item in result['results']))
        self.assertEqual(len(targets), 2)
        self.assertIn('#songs', targets[1])

    def test_search_survives_one_provider_failure(self):
        def extract(_ydl, value, download=False):
            if value.startswith('ytsearch'):
                raise yt_dlp.utils.DownloadError('HTTP Error 429')
            return {'entries': [dict(id='abcdefghij1', title='Available song', _type='url',
                                     url='https://www.youtube.com/watch?v=abcdefghij1')]}
        with patch.object(worker, 'configure_ytdlp_safety'), patch.object(yt_dlp.YoutubeDL, 'extract_info', extract):
            result = worker.run_ytdlp(self.request(input='evening music', kind='inspect'))
        self.assertEqual(result['results'][0]['source'], 'YouTube Music')

    def test_real_search_pagination_and_thumbnail_lists(self):
        def extract(ydl, value, download=False, **unused):
            entries = [dict(_type='url', ie_key='Youtube', id=f'video{i:06}', title=f'Result {i}',
                            url=f'https://www.youtube.com/watch?v=video{i:06}',
                            thumbnails=[{'url':f'https://i.ytimg.com/vi/video{i:06}/small.jpg', 'width':120, 'height':90},
                                        {'url':f'https://i.ytimg.com/vi/video{i:06}/hqdefault.jpg', 'width':480, 'height':360}])
                       for i in range(18)]
            return ydl.process_ie_result(dict(_type='playlist', id='search', title='Search', entries=entries,
                extractor='youtube:search', extractor_key='YoutubeSearch', webpage_url=value), download=False)
        with patch.object(worker, 'configure_ytdlp_safety'), patch.object(yt_dlp.YoutubeDL, 'extract_info', extract):
            pages = [worker.run_ytdlp(self.request(input='test clips', kind='inspect', searchPage=page)) for page in range(4)]
        self.assertEqual([len(page['results']) for page in pages], [16, 16, 4, 0])
        self.assertEqual([page['searchNextPage'] for page in pages], [1, 2, None, None])
        self.assertFalse({item['url'] for item in pages[0]['results']} & {item['url'] for item in pages[1]['results']})
        self.assertTrue(all(item['thumbnail'].endswith('/hqdefault.jpg') for page in pages for item in page['results']))
        fallback = worker.media_from_info(dict(id='abcdefghijk', url='https://www.youtube.com/watch?v=abcdefghijk'))
        self.assertEqual(fallback['thumbnail'], 'https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg')
        self.assertIsNone(worker.media_from_info(dict(id='../../private', url='https://www.youtube.com/watch?v=invalid'))['thumbnail'])

    def test_extract_audio_from_combined_video_stream(self):
        result = self.choose([self.video(1080)], 'max', audio=True)
        self.assertEqual(result['acodec'], 'aac')

    def test_selected_long_media_still_obeys_duration_limit(self):
        with patch.object(worker, 'configure_ytdlp_safety'), patch.object(yt_dlp.YoutubeDL, 'extract_info',
                return_value=dict(id='long', title='Long video', duration=28800)):
            with self.assertRaisesRegex(worker.WorkerError, 'two-hour'):
                worker.run_ytdlp(self.request(kind='inspect'))

    def test_youtube_music_selection_preserves_music_url_and_best_audio(self):
        info = dict(self.audio(256), id='abcdefghij1', title='Song', duration=180,
                    webpage_url='https://www.youtube.com/watch?v=abcdefghij1')
        captured = {}
        def extract(ydl, value, download=False):
            captured.update(ydl.params)
            return info
        value = 'https://music.youtube.com/watch?v=abcdefghij1'
        with patch.object(worker, 'configure_ytdlp_safety'), patch.object(yt_dlp.YoutubeDL, 'extract_info', extract):
            result = worker.run_ytdlp(self.request(input=value, kind='inspect', quality='small'))
        self.assertEqual(result['media']['source'], 'YouTube Music')
        self.assertEqual(result['media']['url'], value)
        self.assertEqual(result['media']['mediaType'], 'audio')
        self.assertEqual(captured['format'], worker.format_selector('max', audio=True))

    def test_instagram_reel_enters_parallel_orchestrator(self):
        value = 'https://www.instagram.com/reel/DeOrpiojEW2/?cplk=tracking'
        complete = {'complete': True, 'media': {'title': 'Reel'}}
        import vortex_network
        with patch.object(vortex_network, 'resolve_public'), \
                patch('vortex_race.race', return_value=complete) as race:
            self.assertEqual(worker.run(self.request(input=value)), complete)
            self.assertEqual(race.call_args.args[0]['input'], 'https://www.instagram.com/reel/DeOrpiojEW2/')

    def test_provider_errors_are_specific_without_exposing_signed_urls(self):
        value = 'https://www.instagram.com/reel/DeOrpiojEW2/'
        for detail, expected in [('Login required https://cdn.example/video?secret=private', 'signed-in session'),
                                 ('HTTP Error 429 secret=private', 'limiting requests'),
                                 ('connection timed out secret=private', 'could not be reached')]:
            message = worker.source_error_message(value, RuntimeError(detail))
            self.assertIn(expected, message)
            self.assertNotIn('private', message)
            self.assertIn('Instagram', message)

    def test_gallery_packaging_uses_safe_flat_names_and_size_limits(self):
        class FakeResponse:
            headers = {'Content-Length': '3'}
            def __enter__(self): return self
            def __exit__(self, *_unused): return False
            def raise_for_status(self): pass
            def iter_content(self, _size): yield b'img'
        session = SimpleNamespace(get=lambda *_args, **_kwargs: FakeResponse())
        files = [{'url': 'https://media.example.com/one.jpg', 'extension': 'jpg',
                  'metadata': {'title': '../../not-a-path', 'width': 120, 'height': 80}, 'session': session},
                 {'url': 'https://media.example.com/two.png', 'extension': 'png', 'metadata': {}, 'session': session}]
        with patch.object(worker, 'collect_gallery', return_value=files):
            result = worker.run_gallery(self.request(input='https://instagram.com/p/example'))
        self.assertEqual(result['filename'], 'gallery.zip')
        self.assertEqual(result['media']['itemCount'], 2)
        self.assertEqual(result['results'], [result['media']])
        with zipfile.ZipFile(self.root / result['filename']) as archive:
            self.assertEqual(archive.namelist(), ['gallery-001.jpg', 'gallery-002.png'])
            self.assertEqual(archive.read('gallery-001.jpg'), b'img')
        with patch.object(worker, 'collect_gallery', return_value=files):
            with self.assertRaisesRegex(worker.WorkerError, 'size limit'):
                worker.run_gallery(self.request(input='https://instagram.com/p/example', maxBytes=2))


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg and ffprobe required for real conversion checks')
class VortexConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ffmpeg = shutil.which('ffmpeg')
        self.output = patch.object(worker, 'EVENT_STREAM', io.StringIO())
        self.output.start()
        self.source = self.root / 'source.webm'
        subprocess.run([self.ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=12',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '1',
                        '-c:v', 'libvpx-vp9', '-threads', '1', '-c:a', 'libopus', str(self.source)],
                       check=True, capture_output=True, timeout=30)

    def tearDown(self):
        self.output.stop()
        self.temp.cleanup()

    def request(self, **changes):
        return dict(directory=str(self.root), ffmpeg=self.ffmpeg, quality='max', maxBytes=1000000, **changes)

    def test_real_video_and_audio_exports_have_requested_containers_and_codecs(self):
        for extension, mode, video_codec, audio_codec in [('mp4', 'video', 'h264', 'aac'),
                ('mov', 'video', 'h264', 'aac'), ('m4a', 'audio', None, 'aac'),
                ('mp3', 'audio', None, 'mp3'), ('wav', 'audio', None, 'pcm_s16le')]:
            with self.subTest(extension=extension):
                artifact = self.root / f'input-{extension}.webm'
                shutil.copyfile(self.source, artifact)
                media = dict(mediaType='video', duration=1, url='https://www.youtube.com/watch?v=abcdefghijk')
                path = worker.export_media(artifact, media, self.request(downloadMode=mode,
                    **{'videoFormat' if mode == 'video' else 'audioFormat':extension}), stem='result-' + extension)
                self.assertEqual(path.suffix, '.' + extension)
                self.assertFalse(artifact.exists())
                data = worker.probe_file(path, self.ffmpeg, self.root)
                streams = {stream['codec_type']:stream['codec_name'] for stream in data['streams']}
                self.assertEqual(streams.get('video'), video_codec)
                self.assertEqual(streams['audio'], audio_codec)
                self.assertEqual(media['ext'], extension)
                self.assertEqual(media['acodec'], audio_codec)
                if mode == 'audio':
                    self.assertEqual(media['mediaType'], 'audio')
                    self.assertIsNone(media['width'])
                else:
                    self.assertEqual((media['width'], media['height']), (96, 64))
                self.assertGreater(path.stat().st_size, 0)

    def test_no_audio_and_oversize_exports_fail_instead_of_returning_invalid_files(self):
        silent = self.root / 'silent.webm'
        subprocess.run([self.ffmpeg, '-v', 'error', '-i', str(self.source), '-an', '-c:v', 'copy', str(silent)],
                       check=True, capture_output=True, timeout=30)
        with self.assertRaisesRegex(worker.WorkerError, 'no audio track'):
            worker.export_media(silent, {'mediaType':'video'}, self.request(downloadMode='audio'))
        request = self.request(downloadMode='audio', audioFormat='wav')
        request['maxBytes'] = 100
        with self.assertRaisesRegex(worker.WorkerError, 'size limit'):
            worker.export_media(self.source, {'mediaType':'video'}, request)
        with self.assertRaises(worker.WorkerError):
            worker.export_media(self.root.parent / 'outside.webm', {}, self.request())

    def test_gallery_videos_are_converted_inside_the_zip_too(self):
        content = self.source.read_bytes()
        class Response:
            headers = {'Content-Length':str(len(content)), 'Content-Type':'video/webm'}
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def raise_for_status(self): pass
            def iter_content(self, _size): yield content
        files = [dict(url='https://cdn.example.com/video.webm', metadata={'title':'Gallery video'},
                      extension='webm', session=SimpleNamespace(get=lambda *_args, **_kwargs:Response())) for _ in range(2)]
        with patch.object(worker, 'collect_gallery', return_value=files), patch.object(worker, 'safe_url', side_effect=lambda value:value):
            result = worker.run_gallery(self.request(input='https://instagram.com/p/gallery', kind='download'))
        with zipfile.ZipFile(self.root / result['filename']) as archive:
            self.assertEqual(archive.namelist(), ['export-001.mp4', 'export-002.mp4'])
            artifact = self.root / 'check.mp4'
            artifact.write_bytes(archive.read('export-001.mp4'))
        codecs = {s['codec_name'] for s in worker.probe_file(artifact, self.ffmpeg, self.root)['streams']}
        self.assertEqual(codecs, {'h264', 'aac'})


if __name__ == '__main__':
    unittest.main()
