"""Offline regression checks; no claim of live provider availability."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

import requests
import yt_dlp
from yt_dlp.extractor.twitter import TwitterIE

import vortex_adapters as adapters
import vortex_network as network
import vortex_race as race
import vortex_services as services
import vortex_urls as urls
import vortex_worker as worker

X = 'https://x.com/iam_scholar/status/2109036692065649078/video/1?s=46'
IG = 'https://www.instagram.com/reel/Example123/'


class Response:
    def __init__(self, body=b'', mime='text/html', status=200, headers=None):
        self.body, self.status_code, self.closed = body, status, False
        self.headers = {'Content-Type': mime, 'Content-Length': str(len(body)), **(headers or {})}
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)
    def close(self): self.closed = True
    def iter_content(self, size):
        for offset in range(0, len(self.body), size):
            yield self.body[offset:offset + size]


class RetrievalTests(unittest.TestCase):
    def test_x_first_media_selector_reaches_real_extractor(self):
        normalized = network.validate_input(X, resolve=False)
        self.assertTrue(normalized.endswith('/video/1'))
        self.assertNotIn('?s=', normalized)
        self.assertEqual(TwitterIE._match_valid_url(normalized).group('index'), '1')
        self.assertEqual(urls.normalize_url(normalized), normalized)
        self.assertEqual(urls.normalize_url(X.replace('/video/1', '/photo/2')).split('/')[-2:], ['photo', '2'])

    def test_x_all_documented_native_recipes_are_eligible(self):
        with patch.object(adapters.importlib.util, 'find_spec', return_value=object()):
            names = [a.name for a in adapters.ADAPTERS if a.compatible(X) and a.configured({})]
        for name in ('yt-dlp', 'yt-dlp-twitter-syndication', 'yt-dlp-twitter-legacy', 'gallery-dl', 'page-media'):
            self.assertIn(name, names)
        self.assertNotIn('fxembed', names)
        self.assertNotIn('cobalt', names)

    def test_twitter_recipe_sets_real_library_options_and_restores_class(self):
        original = yt_dlp.YoutubeDL
        def inspect_options(request, **unused):
            with yt_dlp.YoutubeDL({'quiet': True}) as instance:
                return instance.params['extractor_args']
        for api in ('syndication', 'legacy'):
            with patch.object(worker, 'run_ytdlp', side_effect=inspect_options):
                self.assertEqual(adapters.run_twitter_api({'input': X}, api), {'twitter': {'api': [api]}})
            self.assertIs(yt_dlp.YoutubeDL, original)
        with patch.object(worker, 'run_ytdlp', side_effect=RuntimeError('fixture')):
            with self.assertRaises(RuntimeError):
                adapters.run_twitter_api({'input': X}, 'syndication')
        self.assertIs(yt_dlp.YoutubeDL, original)

    def test_cobalt_not_limited_to_original_six_platforms(self):
        adapter = next(a for a in adapters.ADAPTERS if a.name == 'cobalt')
        self.assertTrue(adapter.compatible('https://vimeo.com/123456'))
        self.assertTrue(adapter.compatible('https://bsky.app/profile/example/post/abc'))
        self.assertFalse(adapter.compatible('https://open.spotify.com/track/example'))
        client = Mock()
        client.json.side_effect = [{'cobalt': {'services': ['vimeo']}},
                                   {'status': 'redirect', 'url': 'https://cdn.example.com/v.mp4', 'filename': 'v.mp4'}]
        with patch.object(adapters, 'ServiceClient', return_value=client), patch.object(adapters, 'finish_stream', return_value={'complete': True}) as finish:
            adapters.run_cobalt({'input': 'https://vimeo.com/123456', 'services': {'cobalt': {}}, 'quality': 'max'})
        self.assertEqual(finish.call_args.args[0], 'https://cdn.example.com/v.mp4')
        self.assertEqual(client.json.call_args.kwargs['body']['url'], 'https://vimeo.com/123456')

    def test_cobalt_picker_selects_only_unambiguous_or_explicit_video(self):
        items = [{'type': 'photo', 'url': 'https://cdn.example.com/a.jpg'},
                 {'type': 'video', 'url': 'https://cdn.example.com/b.mp4'}]
        self.assertEqual(adapters.choose_item(items, IG), items[1])
        self.assertEqual(adapters.choose_item(items, X.replace('/video/1', '/video/2')), items[1])
        with self.assertRaises(ValueError): adapters.choose_item(items, X)
        with self.assertRaises(ValueError): adapters.choose_item(items + [items[1]], IG)
        client = Mock()
        client.parsed = urlsplit('http://127.0.0.1:9000/')
        client.json.side_effect = [{'cobalt': {'services': ['instagram']}}, {'status': 'picker', 'picker': items}]
        with patch.object(adapters, 'ServiceClient', return_value=client), patch.object(adapters, 'finish_stream', return_value={'complete': True}) as finish:
            adapters.run_cobalt({'input': IG, 'services': {'cobalt': {}}})
        self.assertEqual(finish.call_args.args[0], items[1]['url'])
        self.assertIsNone(finish.call_args.args[3])

    def test_fxembed_first_selection_no_longer_rejected(self):
        post = {'code': 200, 'status': {'id': '2109036692065649078',
                'media': {'videos': [{'type': 'video', 'url': 'https://video.twimg.com/a.mp4'}]}}}
        self.assertEqual(adapters.fx_streams(post, X)[2][0]['url'], 'https://video.twimg.com/a.mp4')
        with self.assertRaises(ValueError): adapters.fx_streams(post, X.replace('/video/1', '/video/2'))
        with self.assertRaises(ValueError): adapters.fx_streams(post, X.replace('2109036692065649078', '999'))
        post['status']['quote'] = post['status'].pop('media')
        with self.assertRaises(ValueError): adapters.fx_streams(post, X)

    def test_instaloader_uses_original_shortcode_no_cookies_or_other_downloader(self):
        post = SimpleNamespace(shortcode='Example123', typename='GraphVideo', is_video=True,
                               video_url='https://scontent.cdninstagram.com/v.mp4', caption='Fixture')
        loader = Mock()
        module = SimpleNamespace(Instaloader=Mock(return_value=loader), Post=SimpleNamespace(from_shortcode=Mock(return_value=post)),
                                 exceptions=SimpleNamespace(InstaloaderException=type('FixtureError', (Exception,), {})))
        with patch.dict(sys.modules, {'instaloader': module}), patch.object(adapters, 'finish_stream', return_value={'complete': True}) as finish:
            adapters.run_instaloader({'input': IG})
        module.Post.from_shortcode.assert_called_once_with(loader.context, 'Example123')
        loader.close.assert_called_once()
        loader.login.assert_not_called()
        loader.load_session_from_file.assert_not_called()
        self.assertEqual(finish.call_args.args[0], post.video_url)
        post.shortcode = 'DifferentPost'
        with patch.dict(sys.modules, {'instaloader': module}):
            with self.assertRaisesRegex(ValueError, 'identity_mismatch'):
                adapters.run_instaloader({'input': IG})

    def test_page_fallback_follows_declared_media_and_preserves_signature(self):
        source = 'https://media.example.com/watch/123'
        target = 'https://cdn.example.com/video.mp4?sig=a%2Fb&x=1'
        response = Response(('<meta property="og:video" content="' + target.replace('&', '&amp;') + '">').encode())
        session = Mock()
        session.get.return_value = response
        with patch.object(network, 'resolve_public'), patch('requests.Session', return_value=session), \
                patch.object(adapters, 'finish_stream', return_value={'complete': True}) as finish:
            adapters.run_page_media({'input': source, 'maxBytes': 100000})
        self.assertEqual(finish.call_args.args[0], target)
        self.assertTrue(response.closed)
        session.close.assert_called_once()
        self.assertFalse(session.trust_env)

    def test_social_login_or_wrong_post_metadata_never_wins(self):
        body = b'<link rel="canonical" href="https://www.instagram.com/accounts/login/"><meta property="og:video" content="https://cdn.example.com/wrong.mp4">'
        session = Mock()
        session.get.return_value = Response(body)
        with patch.object(network, 'resolve_public'), patch('requests.Session', return_value=session), patch.object(adapters, 'finish_stream') as finish:
            with self.assertRaisesRegex(ValueError, 'identity_mismatch'):
                adapters.run_page_media({'input': IG})
        finish.assert_not_called()

    def test_direct_media_response_without_mp4_extension_can_be_used(self):
        response = Response(b'fixture binary bytes', 'video/mp4')
        session = Mock()
        session.get.return_value = response
        with tempfile.TemporaryDirectory() as directory, patch.object(network, 'resolve_public'), \
                patch('requests.Session', return_value=session), patch.object(worker, 'EVENT_STREAM', io.StringIO()), \
                patch.object(adapters, 'finish_artifact', return_value={'complete': True}) as finish:
            adapters.run_page_media({'input': 'https://cdn.example.com/download/opaque?signature=keep%2Fthis',
                                    'directory': directory, 'maxBytes': 100000})
            self.assertEqual(finish.call_args.args[0].read_bytes(), response.body)
        self.assertTrue(response.closed)

    def test_private_redirect_rejected_before_second_request(self):
        response = Response(status=302, headers={'Location': 'http://169.254.169.254/latest/meta-data/'})
        session = Mock()
        session.get.return_value = response
        with patch.object(network, 'resolve_public'):
            with self.assertRaises(network.UnsafeDestination):
                adapters.guarded_get(session, 'https://cdn.example.com/file')
        self.assertEqual(session.get.call_count, 1)
        self.assertTrue(response.closed)

    def test_failed_stream_closes_handles_and_deletes_partial(self):
        response = Response(b'too large', 'video/mp4', headers={'Content-Length': ''})
        session = Mock()
        session.get.return_value = response
        with tempfile.TemporaryDirectory() as directory, patch.object(network, 'resolve_public'), \
                patch('requests.Session', return_value=session), patch.object(worker, 'EVENT_STREAM', io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'size_limit'):
                adapters.download_stream('https://cdn.example.com/file', {'directory': directory, 'maxBytes': 4})
            self.assertFalse((Path(directory) / 'source.mp4').exists())
        self.assertTrue(response.closed)
        session.close.assert_called_once()

    def test_service_errors_are_classified_without_raw_response_data(self):
        client = services.ServiceClient({'enabled': True, 'url': 'https://own.example.com', 'allowExternal': True})
        body = json.dumps({'status': 'error', 'error': {'code': 'error.api.rate_limit', 'context': {'secret': 'not-for-logs'}}}).encode()
        response = SimpleNamespace(status=429, read=lambda n: body, close=Mock())
        connection = SimpleNamespace(close=Mock())
        with patch.object(client, 'open', return_value=(connection, response)):
            with self.assertRaisesRegex(ValueError, '^service_rate_limited$'):
                client.json(body={'url': X})
        response.close.assert_called_once()
        connection.close.assert_called_once()
        self.assertEqual(services.service_error(403), 'service_authentication')
        self.assertEqual(services.service_error(502), 'service_network')

    def test_local_processing_cannot_send_remote_manifests_to_ffmpeg(self):
        for result in ({'type': 'merge', 'tunnel': ['https://a', 'https://b'], 'isHLS': True},
                       {'type': 'audio', 'tunnel': ['file:///private']},
                       {'type': 'merge', 'tunnel': []}):
            client = services.ServiceClient({'enabled': True, 'url': 'http://127.0.0.1:9000', 'publicEgressOnly': True})
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    adapters.cobalt_local(result, {}, {'directory': directory, 'maxBytes': 100000}, client)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class LocalMergeTests(unittest.TestCase):
    def test_cobalt_progressive_tunnels_merge_and_full_decode_locally(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(worker, 'EVENT_STREAM', io.StringIO()):
            root = Path(directory)
            ffmpeg = shutil.which('ffmpeg')
            video, audio = root / 'fixture-video.mp4', root / 'fixture-audio.m4a'
            subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=12',
                            '-t', '0.5', '-c:v', 'libx264', '-y', str(video)], check=True, timeout=60)
            subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440',
                            '-t', '0.5', '-c:a', 'aac', '-y', str(audio)], check=True, timeout=60)
            sources = {'http://127.0.0.1:9000/tunnel?id=v': video, 'http://127.0.0.1:9000/tunnel?id=a': audio}
            def download(url, request, client, *, stem):
                path = root / (stem + '.mp4')
                shutil.copyfile(sources[url], path)
                return path
            request = dict(directory=directory, maxBytes=1000000, maxDuration=7200, ffmpeg=ffmpeg,
                           kind='download', quality='balanced', videoFormat='mp4')
            with patch.object(adapters, 'download_stream', side_effect=download):
                result = adapters.cobalt_local({'type': 'merge', 'tunnel': list(sources)},
                                              {'mediaType': 'unknown'}, request, object())
            worker.verify_result(result, request)
            self.assertTrue((root / result['filename']).exists())
            self.assertEqual(result['media']['ext'], 'mp4')
            self.assertFalse((root / 'part-0.mp4').exists())


if __name__ == '__main__':
    unittest.main()
