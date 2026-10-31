import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yt_dlp
import vortex_worker as worker
import vortex_spotify as spotify


class SpotifyRecoveryTests(unittest.TestCase):
    track_id = '0VjIjW4GlUZAMYd2vXMi3b'

    def embed(self, **changes):
        entity = dict(type='track', uri='spotify:track:' + self.track_id, name='Example song',
                      artists=[{'name': 'Example artist'}], duration=200040,
                      audioPreview={'url': 'https://preview.example/30-seconds.mp3'})
        entity.update(changes)
        return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(
            {'props': {'pageProps': {'state': {'data': {'entity': entity}}}}}) + '</script>'

    def test_public_metadata_preserves_identity_and_full_duration_not_preview(self):
        metadata = spotify.parse_track(self.embed(), self.track_id)
        self.assertEqual(metadata['duration'], 200.04)
        self.assertEqual(metadata['artists'], ['Example artist'])
        self.assertNotIn('preview', json.dumps(metadata))
        for changes in [{'uri': 'spotify:track:' + 'a' * 22}, {'type': 'episode'},
                        {'duration': None}, {'artists': []}, {'name': ''}]:
            with self.subTest(changes=changes), self.assertRaises(worker.WorkerError) as raised:
                spotify.parse_track(self.embed(**changes), self.track_id)
            self.assertEqual(raised.exception.category, 'spotify_metadata')

    def test_public_track_uses_matcher_without_spotify_api_credentials(self):
        metadata = spotify.parse_track(self.embed(), self.track_id)
        source = 'https://www.youtube.com/watch?v=abcdefghijk'
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'VORTEX_SPOTIFY_CLIENT_ID': '', 'VORTEX_SPOTIFY_CLIENT_SECRET': ''}), \
                patch.object(worker, 'EVENT_STREAM', io.StringIO()), \
                patch.object(worker, 'configure_ytdlp_safety'), \
                patch.object(worker, 'safe_url', side_effect=lambda value: value), \
                patch.object(spotify, 'public_track', return_value=metadata), \
                patch('spotdl.Spotdl') as api_client, \
                patch('spotdl.download.downloader.Downloader') as matcher, \
                patch.object(worker, 'run_ytdlp', return_value={'complete': True, 'media': {}}) as download:
            matcher.return_value.search.return_value = source
            result = worker.run_spotify(dict(input=metadata['url'], kind='download', directory=directory))
            api_client.assert_not_called()
            song = matcher.return_value.search.call_args.args[0]
            self.assertEqual(song.name, 'Example song')
            self.assertEqual(song.duration, 200.04)
            self.assertEqual(download.call_args.kwargs['input_value'], source)
            self.assertTrue(download.call_args.kwargs['audio'])
            self.assertEqual(result['media']['url'], metadata['url'])
            self.assertIn('matched audio', result['media']['source'])
            matcher.reset_mock()
            matcher.return_value.search.side_effect = [RuntimeError('YouTube Music unavailable'), source]
            worker.run_spotify(dict(input=metadata['url'], kind='download', directory=directory))
            self.assertEqual(matcher.call_count, 2)
            self.assertEqual(matcher.call_args.kwargs['settings']['audio_providers'], ['youtube'])

    def test_search_without_credentials_and_unsupported_links_are_actionable(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'VORTEX_SPOTIFY_CLIENT_ID': '', 'VORTEX_SPOTIFY_CLIENT_SECRET': ''}), \
                patch.object(worker, 'EVENT_STREAM', io.StringIO()), patch.object(worker, 'configure_ytdlp_safety'):
            for value, category in [('spotify:example song', 'spotify_configuration'),
                                    ('https://open.spotify.com/playlist/' + self.track_id, 'spotify_link')]:
                with self.subTest(value=value), self.assertRaises(worker.WorkerError) as raised:
                    worker.run_spotify(dict(input=value, kind='inspect', directory=directory))
                self.assertEqual(raised.exception.category, category)

    def test_metadata_http_is_bounded_and_does_not_follow_redirects(self):
        import requests
        with patch.object(requests, 'Session') as factory:
            session = factory.return_value.__enter__.return_value
            response = session.get.return_value.__enter__.return_value
            response.status_code = 200
            response.iter_content.return_value = [self.embed().encode()]
            self.assertEqual(spotify.public_track(self.track_id)['name'], 'Example song')
            self.assertFalse(session.trust_env)
            self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
            for status, chunks in [(302, []), (200, [b'x' * (2 * 1024 * 1024 + 1)])]:
                response.status_code = status
                response.iter_content.return_value = chunks
                with self.assertRaises(worker.WorkerError):
                    spotify.public_track(self.track_id)


class LongVideoRecoveryTests(unittest.TestCase):
    def select(self, quality='balanced', duration=10503, avc=True):
        # Actual sizes/codecs observed for the reported 2h55m YouTube recording.
        # Fixture URLs are synthetic: never persist signed CDN capabilities.
        formats = [
            dict(format_id='299', height=1080, fps=60, vcodec='avc1.64002A', acodec='none', ext='mp4', filesize=2899018152),
            dict(format_id='298', height=720, fps=60, vcodec='avc1.640020', acodec='none', ext='mp4', filesize=1450261780),
            dict(format_id='303', height=1080, fps=60, vcodec='vp9', acodec='none', ext='webm', filesize=921394955),
            dict(format_id='140', vcodec='none', acodec='mp4a.40.2', ext='m4a', filesize=169973909, abr=129),
        ]
        if not avc:
            formats = [f for f in formats if not f['vcodec'].startswith('avc')]
        for f in formats:
            f['url'] = 'https://media.example.com/' + f['format_id']
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'format_sort': worker.MP4_FORMAT_SORT}) as ydl:
            ydl.format_selector = worker.bounded_selector(ydl, worker.format_selector(quality), [duration],
                                                         2 * 1024**3, prefer_copy=quality != 'max')
            return ydl.process_ie_result(dict(id='fixture', title='Long recording', duration=duration,
                                              formats=formats), download=False)

    def test_reported_recording_uses_complete_phone_ready_streams_that_fit(self):
        selected = self.select()
        self.assertEqual(selected['format_id'], '298+140')
        self.assertEqual(selected['duration'], 10503)
        self.assertLess(sum(f['filesize'] for f in selected['requested_formats']), 2 * 1024**3)

    def test_max_short_and_no_avc_sources_retain_video_quality(self):
        self.assertEqual(self.select('max')['format_id'], '303+140')
        self.assertEqual(self.select(duration=300)['format_id'], '303+140')
        self.assertEqual(self.select(avc=False)['format_id'], '303+140', 'AAC alone is never a substitute for video')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_transcoding_respects_byte_budget_without_truncating_duration(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(worker, 'EVENT_STREAM', io.StringIO()):
            source = Path(directory) / 'source.webm'
            subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=320x180:rate=24:duration=20', '-c:v', 'libvpx-vp9',
                            '-threads', '2', '-b:v', '150k', str(source)], check=True, capture_output=True, timeout=60)
            request = dict(directory=directory, kind='download', downloadMode='video', quality='balanced',
                           ffmpeg=shutil.which('ffmpeg'), maxBytes=550000)
            media = dict(mediaType='video', duration=20)
            output = worker.export_media(source, media, request)
            self.assertLessEqual(output.stat().st_size, request['maxBytes'])
            self.assertGreaterEqual(media['duration'], 19.9)
            result = worker.verify_result(dict(complete=True, media=media, filename=output.name),
                                          dict(request, expectedDuration=20))
            self.assertTrue(result['complete'])


if __name__ == '__main__':
    unittest.main()
