"""Offline orchestration fixtures are NOT evidence of live provider support."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import psutil
import vortex_adapters as adapters
import vortex_network as network
import vortex_race as race
import vortex_services as services
import vortex_urls as urls
import vortex_worker as worker


class URLTests(unittest.TestCase):
    def test_provider_urls_and_idempotence(self):
        cases = {
            'youtu.be/abcdefghijk?t=30&utm_source=share': 'https://www.youtube.com/watch?v=abcdefghijk&t=30',
            'https:/m.youtube.com//shorts/abcdefghijk?si=abc': 'https://www.youtube.com/watch?v=abcdefghijk',
            'https://music.youtube.com/watch?v=abcdefghijk&list=album&t=9': 'https://music.youtube.com/watch?v=abcdefghijk&list=album&t=9',
            'https://www.youtube.com/watch?v=abcdefghijk#t=30': 'https://www.youtube.com/watch?v=abcdefghijk&t=30',
            'https://mobile.twitter.com/user//status/123/video/1?s=46': 'https://x.com/user/status/123',
            'x.com/user/status/123/video/2?s=46': 'https://x.com/user/status/123/video/2',
            'https://fixupx.com/user/status/123': 'https://x.com/user/status/123',
            'https://m.instagram.com/reels/Ab_C/?igsh=token': 'https://www.instagram.com/reel/Ab_C/',
            'https://m.facebook.com/watch/?v=123&fbclid=tracking': 'https://www.facebook.com/watch/?v=123',
            'www.tiktok.com/@user/video/123?is_from_webapp=1': 'https://www.tiktok.com/@user/video/123',
            'https://old.reddit.com/r/test/comments/abc/title/?utm_source=share': 'https://www.reddit.com/r/test/comments/abc/title/',
            'open.spotify.com/intl-en/track/' + 'a' * 22 + '?si=123': 'https://open.spotify.com/track/' + 'a' * 22,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(network.validate_input(value, resolve=False), expected)
                self.assertEqual(urls.normalize_url(expected), expected)

    def test_unknown_signed_urls_searches_and_missing_ids_unchanged(self):
        value = 'https://cdn.example.com/a//b?sig=abcd%2Fef&si=meaningful&v=1&v=2'
        self.assertEqual(urls.normalize_url(value), value)
        self.assertEqual(urls.normalize_url('hello world'), 'hello world')
        self.assertEqual(urls.normalize_url('https://youtu.be/missing'), 'https://youtu.be/missing')
        self.assertEqual(urls.platform('https://youtube.com.attacker.example/watch?v=abcdefghijk'), 'generic')
        for value in ('https://a:b@youtube.com/watch?v=x', 'https://x.com\\@127.0.0.1/', 'https://x.com/\nabc'):
            with self.assertRaises(ValueError):
                urls.normalize_url(value)

    def test_normalization_does_not_remove_unsafe_ports_or_destinations(self):
        for value in ('https://x.com:8765/user/status/1', 'http://127.0.0.1/', 'http://[::1]/', 'https://metadata.google.internal'):
            with self.assertRaises(ValueError):
                network.validate_input(value, resolve=False)

    def test_redirect_private_destination_rejected_before_following(self):
        class Response:
            status_code = 302
            headers = {'Location': 'http://127.0.0.1/private'}
            def __enter__(self): return self
            def __exit__(self, *args): pass
        session = SimpleNamespace(get=lambda *args, **kwargs: Response())
        with patch.object(network, 'resolve_public'):
            with self.assertRaises(network.UnsafeDestination):
                urls.resolve_shared_url('https://t.co/shared', session)

    def test_short_spotify_link_is_classified_after_redirect(self):
        class Response:
            def __init__(self, status, location=None):
                self.status_code, self.headers = status, {'Location': location}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def raise_for_status(self): pass
        target = 'https://open.spotify.com/track/' + 'a' * 22
        with patch.object(network, 'resolve_public'), patch('requests.Session') as session:
            session.return_value.get.side_effect = [Response(302, target + '?si=tracking'), Response(200)]
            self.assertEqual(urls.resolve_shared_url('https://spoti.fi/abc'), target)


class ServiceTests(unittest.TestCase):
    def test_services_are_opt_in_and_external_authorization_is_required(self):
        for config in ({}, {'enabled': True, 'url': 'https://api.cobalt.tools/'},
                       {'enabled': True, 'url': 'http://127.0.0.1:9000/'},
                       {'enabled': True, 'url': 'http://192.168.1.1/', 'allowExternal': True},
                       {'enabled': True, 'url': 'https://user:password@example.com/', 'allowExternal': True}):
            with self.assertRaises(ValueError): services.service_config(config)
        services.service_config({'enabled': True, 'url': 'http://127.0.0.1:9000/', 'publicEgressOnly': True})
        services.service_config({'enabled': True, 'url': 'https://own-instance.example.com/', 'allowExternal': True})

    def test_tunnel_cannot_redirect_to_another_local_service(self):
        client = services.ServiceClient({'enabled': True, 'url': 'http://127.0.0.1:9000/', 'publicEgressOnly': True})
        for url in ('http://127.0.0.1:8765/tunnel', 'http://127.0.0.1:9000/admin',
                    'http://localhost:9000/tunnel', 'https://example.com/tunnel'):
            with self.assertRaises(ValueError): client.tunnel(url)

    def test_fxtwitter_identity_variants_and_no_quote_fallback(self):
        video = dict(type='video', url='https://video.twimg.com/a.mp4', height=1080, width=1920,
                     formats=[dict(url='https://video.twimg.com/vid/640x360/a.mp4', bitrate=100),
                              dict(url='https://video.twimg.com/vid/1920x1080/a.mp4', bitrate=1000)])
        payload = {'code': 200, 'status': {'id': '123', 'media': {'videos': [video]}}}
        _, _, streams = adapters.fx_streams(payload, 'https://x.com/u/status/123')
        self.assertEqual(adapters.select_stream(streams, 'max')['height'], 1080)
        self.assertEqual(adapters.select_stream(streams, 'small')['height'], 360)
        with self.assertRaisesRegex(ValueError, 'identity_mismatch'):
            adapters.fx_streams(payload, 'https://x.com/u/status/456')
        payload['status'] = {'id': '123', 'quote': {'media': {'videos': [video]}}}
        with self.assertRaises(ValueError): adapters.fx_streams(payload, 'https://x.com/u/status/123')

    def test_unavailable_engine_does_not_remove_other_compatible_engines(self):
        request = {'input': 'https://x.com/u/status/123'}
        installed = [a.name for a in adapters.ADAPTERS if a.compatible(request['input']) and a.configured(request)]
        self.assertIn('yt-dlp', installed)
        self.assertNotIn('cobalt', installed)
        self.assertNotIn('fxembed', installed)
        for a in adapters.ADAPTERS:
            if a.name in ('gallery-dl', 'fxembed', 'spotdl'):
                self.assertFalse(a.compatible('https://www.youtube.com/watch?v=abcdefghijk'))

    def test_egress_proxy_rejects_private_and_mixed_dns_before_connect(self):
        import vortex_egress
        for address in ('127.0.0.1:443', '169.254.169.254:80', 'example.com:8765', 'user@example.com:443'):
            with self.assertRaises(ValueError): vortex_egress.connect_public(address)
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443)),
                   (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with patch.object(socket, 'getaddrinfo', return_value=answers), patch.object(socket.socket, 'connect') as connect:
            with self.assertRaises(ValueError): vortex_egress.connect_public('media.example.com:443')
            connect.assert_not_called()


FIXTURE = r'''
import argparse,json,pathlib,time,subprocess,sys
p=argparse.ArgumentParser();p.add_argument('--request');p.add_argument('--adapter');a=p.parse_args()
r=json.loads(pathlib.Path(a.request).read_text());d=pathlib.Path(r['directory'])
spec=r['fixtures'][a.adapter];kind=r['kind']
(d.parent / (a.adapter+'-'+kind+'.started')).write_text(str(time.monotonic()))
if spec.get('child') and kind=='inspect':
 child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
 (d.parent.parent/(a.adapter+'.pid')).write_text(str(child.pid))
time.sleep(spec.get(kind+'Delay',0.05))
if spec.get(kind+'Fail'):raise SystemExit(1)
media={'title':a.adapter,'url':r['input'],'source':'X','mediaType':'video','height':spec.get('height',1080),'engine':a.adapter}
result={'complete':True,'media':media,'results':[media]}
if kind=='download':
 (d/'export.mp4').write_bytes(b'orchestration-only-fixture');result['filename']='export.mp4'
print(json.dumps(result),flush=True)
'''


class RaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = self.root / 'fixture.py'
        self.fixture.write_text(FIXTURE)
        self.events = io.StringIO()
        self.output = patch.object(worker, 'EVENT_STREAM', self.events)
        self.output.start()
        self.worker_patch = patch.object(race, 'WORKER', self.fixture)
        self.worker_patch.start()

    def tearDown(self):
        self.worker_patch.stop()
        self.output.stop()
        self.temp.cleanup()

    def run_race(self, fixtures, **options):
        registry = [SimpleNamespace(name=name, compatible=lambda url: True, configured=lambda request: True) for name in fixtures]
        request = dict(input='https://x.com/u/status/123', kind='download', quality='balanced',
                       directory=str(self.root), maxBytes=100000, fixtures=fixtures)
        request.update(options)
        return race.race(request, adapters=registry)

    def test_concurrent_execution_fastest_success_single_artifact_no_engine_leak(self):
        started = time.monotonic()
        result = self.run_race({'slow': {'inspectDelay': .1, 'downloadDelay': 3},
                                'fast': {'inspectDelay': .1, 'downloadDelay': .1}})
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual(result['media']['title'], 'fast')
        self.assertNotIn('engine', result['media'])
        self.assertEqual(list(self.root.glob('*.mp4')), [self.root / 'download.mp4'])
        self.assertFalse((self.root / 'race').exists())
        public = [e for e in map(json.loads, self.events.getvalue().splitlines()) if 'diagnostic' not in e]
        self.assertTrue(all('engine' not in json.dumps(e) for e in public))

    def test_download_failure_after_inspection_retains_alternate(self):
        result = self.run_race({'fast_broken': {'downloadFail': True},
                                'valid': {'downloadDelay': .2}, 'inspect_broken': {'inspectFail': True}})
        self.assertEqual(result['media']['title'], 'valid')

    def test_max_quality_waits_for_higher_quality_candidate(self):
        result = self.run_race({'fast_low': {'height': 360}, 'slow_high': {'inspectDelay': .3, 'height': 2160}}, quality='max')
        self.assertEqual(result['media']['height'], 2160)

    def test_all_fail_one_safe_error_and_cleanup(self):
        with self.assertRaisesRegex(worker.WorkerError, '^' + race.PUBLIC_ERROR.replace('.', r'\.') + '$'):
            self.run_race({'one': {'inspectFail': True}, 'two': {'downloadFail': True}})
        self.assertFalse((self.root / 'race').exists())
        self.assertFalse(list(self.root.glob('*.mp4')))

    def test_inspection_cancels_losing_worker_and_descendant(self):
        self.run_race({'slow': {'inspectDelay': 30, 'child': True}, 'fast': {'inspectDelay': .3}}, kind='inspect')
        pid = int((self.root / 'slow.pid').read_text())
        self.assertFalse(psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE)
        self.assertFalse((self.root / 'race').exists())

    def test_failed_higher_quality_falls_back_without_new_job(self):
        result = self.run_race({'high': {'height': 2160, 'downloadFail': True}, 'lower': {'height': 1080}}, quality='max')
        self.assertEqual(result['media']['height'], 1080)

    def test_bounded_download_concurrency(self):
        active, peak = [], [0]
        original = race.Attempt
        class CountingAttempt(original):
            def __init__(self, *args):
                super().__init__(*args)
                if self.kind == 'download':
                    active[:] = [a for a in active if a.process.poll() is None]
                    active.append(self); peak[0] = max(peak[0], len(active))
        registry = [SimpleNamespace(name=str(i), compatible=lambda value: True, configured=lambda request: True) for i in range(5)]
        fixtures = {a.name: {'downloadFail': True, 'downloadDelay': .1} for a in registry}
        with self.assertRaises(worker.WorkerError):
            race.race(dict(input='https://x.com/u/status/123', kind='download', quality='max', directory=str(self.root),
                           maxBytes=100000, fixtures=fixtures), adapters=registry, attempt_factory=CountingAttempt)
        self.assertEqual(peak[0], 2)

    def test_resource_limit_cleanup(self):
        with self.assertRaises(worker.WorkerError):
            self.run_race({'one': {'inspectDelay': 30}}, maxBytes=1)
        self.assertFalse((self.root / 'race').exists())


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'real FFmpeg required')
class VerificationTests(unittest.TestCase):
    def test_gallery_verifies_each_video_before_zip_and_rejects_truncation(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(worker, 'EVENT_STREAM', io.StringIO()):
            root = Path(temporary)
            source = root / 'fixture.mp4'
            ffmpeg = shutil.which('ffmpeg')
            subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=12',
                            '-t', '0.4', '-c:v', 'libx264', '-y', str(source)], check=True)
            data = source.read_bytes()
            class Response:
                headers = {'Content-Type': 'video/mp4', 'Content-Length': str(len(data))}
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def raise_for_status(self): pass
                def iter_content(self, size): yield data
            session = SimpleNamespace(get=lambda *args, **kwargs: Response())
            files = [dict(url='https://cdn.example.com/video', extension='mp4', session=session,
                          metadata={'title': 'Gallery', 'duration': duration}) for duration in (.4, 60)]
            request = dict(directory=str(root), input='https://www.instagram.com/p/example', kind='download',
                           maxBytes=1000000, maxDuration=7200, quality='max', ffmpeg=ffmpeg)
            with patch.object(worker, 'collect_gallery', return_value=files), patch.object(worker, 'safe_url', side_effect=lambda value: value):
                with self.assertRaisesRegex(worker.WorkerError, 'incomplete'):
                    worker.run_gallery(request)
            self.assertFalse((root / 'gallery.zip').exists())

    def test_real_video_audio_exports_and_corrupt_file_rejection(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(worker, 'EVENT_STREAM', io.StringIO()):
            root = Path(temporary)
            request = dict(directory=str(root), kind='download', maxBytes=1000000, maxDuration=7200,
                           quality='max', ffmpeg=shutil.which('ffmpeg'))
            for extension in ('mp4', 'mov', 'mp3', 'wav', 'm4a'):
                source = root / 'input.mp4'
                subprocess.run([request['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=12',
                                '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '0.4', '-c:v', 'libx264', '-c:a', 'aac', '-y', str(source)], check=True)
                audio = extension in ('mp3', 'wav', 'm4a')
                spec = dict(request, downloadMode='audio' if audio else 'video', audioFormat=extension if audio else 'm4a', videoFormat=extension if not audio else 'mp4')
                media = {'mediaType': 'audio' if audio else 'video', 'duration': .4}
                path = worker.export_media(source, media, spec)
                result = dict(complete=True, filename=path.name, media=media)
                worker.verify_result(result, spec)
                self.assertEqual(media['ext'], extension)
                path.unlink()
            (root / 'bad.mp4').write_bytes(b'<html>not a video</html>')
            with self.assertRaises(worker.WorkerError):
                worker.verify_result(dict(complete=True, filename='bad.mp4', media={'mediaType': 'video'}), request)


if __name__ == '__main__':
    unittest.main()
