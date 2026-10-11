"""Offline tests for signed Instagram CDN/download-link fallback and its network limits."""
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import requests

import vortex_adapters as adapters
import vortex_network as network
import vortex_urls as urls
import vortex_worker as worker


CDN = ('https://scontent.cdninstagram.com/o1/v/t2/f2/m86/example.mp4'
       '?oe=FFFFFFFF&oh=signature&efg=example')
RELAY = 'https://dl.videodropper.app/?' + urlencode({'url': CDN})


class InstagramSignedMediaTests(unittest.TestCase):
    def test_direct_and_explicit_relay_urls_dispatch_without_rewriting_signatures(self):
        direct = adapters.Adapter('instagram-direct')
        for value in (CDN, RELAY):
            with self.subTest(value=value):
                self.assertEqual(urls.instagram_signed_source(value), CDN)
                self.assertEqual(network.validate_input(value, 'inspect', resolve=False), value)
                self.assertEqual(urls.normalize_url(value), value)
                self.assertTrue(direct.compatible(value))
                self.assertTrue(direct.configured({}))
        self.assertFalse(direct.compatible('https://www.instagram.com/reel/Demo123/'))
        self.assertFalse(direct.compatible('https://dl.videodropper.app/?url=https://example.com/movie.mp4'))
        self.assertFalse(direct.compatible('https://scontent.cdninstagram.com.evil.invalid/movie.mp4'))
        self.assertFalse(urls.is_instagram_cdn_video('http://scontent.cdninstagram.com/movie.mp4'))

    def test_relay_must_embed_exactly_one_https_instagram_mp4(self):
        bad_sources = [
            'http://127.0.0.1/private',
            'https://169.254.169.254/latest/meta-data/',
            'https://example.com/a.mp4',
            'https://scontent.cdninstagram.com.evil.invalid/a.mp4',
            'https://scontent.cdninstagram.com/a.jpg',
            'https://user:password@scontent.cdninstagram.com/a.mp4',
            'http://scontent.cdninstagram.com/a.mp4',
        ]
        invalid = ['https://dl.videodropper.app/?url=' + urlencode({'url': source})[4:]
                   for source in bad_sources]
        invalid += [
            RELAY + '&url=' + urlencode({'url': CDN})[4:],
            'https://dl.videodropper.app/?url=',
            'https://dl.videodropper.app:444/?' + urlencode({'url': CDN}),
            'http://dl.videodropper.app/?' + urlencode({'url': CDN}),
            'https://dl.videodropper.app/other?' + urlencode({'url': CDN}),
            'https://dl.videodropper.app/?' + urlencode({'url': CDN}) + '#fragment',
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                network.validate_input(value, 'inspect', resolve=False)

    def test_direct_first_then_explicit_relay_if_unavailable(self):
        request = dict(input=RELAY, kind='download', quality='max',
                       directory='/tmp/unused', maxBytes=1000000)
        with tempfile.TemporaryDirectory() as directory:
            request['directory'] = directory
            with patch.object(adapters, 'finish_stream',
                              side_effect=[requests.HTTPError('403 from CDN'),
                                           {'complete': True, 'filename': 'export.mp4'}]) as finish:
                with patch.object(worker, 'EVENT_STREAM', io.StringIO()):
                    result = adapters.run_instagram_direct(request)
            self.assertEqual(result['filename'], 'export.mp4')
            self.assertEqual([call.args[0] for call in finish.call_args_list], [CDN, RELAY])
            self.assertEqual(finish.call_args_list[0].args[1]['mediaType'], 'video')
            self.assertEqual(finish.call_args_list[0].args[1]['source'], 'Instagram')
            self.assertEqual(finish.call_args_list[0].args[1]['url'], RELAY)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_real_mp4_download_is_bounded_and_fully_verified(self):
        ffmpeg = shutil.which('ffmpeg')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / 'fixture.mp4'
            subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=96x64:rate=12', '-f', 'lavfi', '-i',
                            'sine=frequency=440', '-t', '0.5', '-c:v', 'libx264',
                            '-c:a', 'aac', '-y', str(fixture)],
                           check=True, timeout=60)
            data = fixture.read_bytes()
            class Response:
                headers = {'Content-Type': 'video/mp4', 'Content-Length': str(len(data))}
                def raise_for_status(self): pass
                def close(self): pass
                def iter_content(self, size):
                    for offset in range(0, len(data), size):
                        yield data[offset:offset + size]
            session = SimpleNamespace(get=lambda *args, **kwargs: Response(), trust_env=True)
            request = dict(input=CDN, kind='download', quality='balanced',
                           directory=str(root), maxBytes=1000000, maxDuration=7200,
                           downloadMode='video', videoFormat='mp4', ffmpeg=ffmpeg)
            with patch.object(network, 'resolve_public'), patch('requests.Session', return_value=session):
                with patch.object(worker, 'EVENT_STREAM', io.StringIO()):
                    result = adapters.run_instagram_direct(request)
                    worker.verify_result(result, request)
            self.assertEqual(result['media']['source'], 'Instagram')
            self.assertEqual(result['media']['mediaType'], 'video')
            self.assertEqual(Path(result['filename']).suffix, '.mp4')
            self.assertTrue((root / result['filename']).is_file())
            self.assertFalse(session.trust_env)


if __name__ == '__main__':
    unittest.main()
