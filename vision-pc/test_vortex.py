"""Offline Vortex integration checks using real, locally signed Firebase tokens.

No source site, media service, or paid provider is contacted by these tests.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

import server
import vortex


class VortexAccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ffmpeg = self.root / 'ffmpeg'
        self.ffmpeg.touch()
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({
            'token': 'a' * 48, 'publicAccess': True, 'ffmpeg': str(self.ffmpeg),
            'firebaseAuth': {'enabled': True, 'projectId': 'visionboard-api'},
        }), encoding='utf-8')
        server.configure(self.config)
        server.app.config['TESTING'] = True
        self.service = server.vortex
        self.client = server.app.test_client()
        identity = server.app.config['FIREBASE_IDENTITY']
        identity._keys = {'vortex-test': self.signing_key.public_key()}
        identity._expires = time.monotonic() + 3600
        self.alice, self.bob = self.headers('alice'), self.headers('bob')
        self.legacy = {'Authorization': 'Bearer ' + 'a' * 48}
        self.dns = patch.object(socket, 'getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443)),
        ])
        self.dns.start()
        self.disk = patch('vortex.shutil.disk_usage', return_value=SimpleNamespace(free=100 * 1024 ** 3))
        self.disk.start()

    def tearDown(self):
        self.service.close()
        self.dns.stop()
        self.disk.stop()
        self.temp.cleanup()

    def headers(self, uid):
        now = int(time.time())
        claims = {
            'sub': uid, 'aud': 'visionboard-api',
            'iss': 'https://securetoken.google.com/visionboard-api',
            'exp': now + 3600, 'iat': now - 1, 'auth_time': now - 10,
            'firebase': {'sign_in_provider': 'google.com'},
        }
        token = jwt.encode(claims, self.signing_key, algorithm='RS256', headers={'kid': 'vortex-test'})
        return {'Authorization': 'Bearer ' + token, 'Origin': 'https://jrdn-r.github.io'}

    def submit(self, headers=None, **changes):
        payload = {'input': 'https://www.youtube.com/watch?v=abcdefghijk',
                   'kind': 'download', 'quality': 'balanced', 'requestId': 'vortex-test-1'}
        payload.update(changes)
        return self.client.post('/api/vortex/jobs', json=payload,
                                headers=self.alice if headers is None else headers)

    def accepted(self, **changes):
        response = self.submit(**changes)
        self.assertEqual(response.status_code, 202, response.json)
        return response.json['id']

    def complete(self, job, content=b'private-media-bytes', title='Private media'):
        directory = self.service.root / job
        directory.mkdir(exist_ok=True)
        (directory / 'media.mp4').write_bytes(content)
        with server.connect_db() as db:
            db.execute("UPDATE vortex_jobs SET status='processing' WHERE id=?", (job,))
        self.service._event(job, {'media': {'title': title, 'source': 'YouTube'}})
        self.assertTrue(self.service._finish(job, {'filename': 'media.mp4'}))
        return self.service.row(job)

    def test_all_routes_require_account_and_reject_installation_access(self):
        trial = self.client.post('/api/trial/start', json={'deviceId': 'd' * 64},
                                 headers={'Origin': 'https://jrdn-r.github.io'},
                                 environ_base={'REMOTE_ADDR': '198.51.100.4'})
        self.assertEqual(trial.status_code, 201, trial.json)
        guest = {'Authorization': 'Bearer ' + trial.json['token']}
        endpoints = [
            ('get', '/api/vortex/capabilities', None),
            ('get', '/api/vortex/jobs', None),
            ('post', '/api/vortex/jobs', {'input': 'test', 'kind': 'inspect', 'requestId': 'blocked'}),
            ('get', '/api/vortex/jobs/' + 'a' * 24, None),
            ('post', '/api/vortex/jobs/' + 'a' * 24 + '/cancel', {}),
            ('delete', '/api/vortex/jobs/' + 'a' * 24, None),
            ('post', '/api/vortex/jobs/' + 'a' * 24 + '/ticket', {}),
            ('get', '/api/vortex/jobs/' + 'a' * 24 + '/file', None),
        ]
        for method, path, body in endpoints:
            for headers in ({}, self.legacy, guest):
                with self.subTest(method=method, path=path, legacy=bool(headers)):
                    response = getattr(self.client, method)(path, headers=headers,
                        **({'json': body} if body is not None else {}))
                    self.assertIn(response.status_code, (401, 403), response.json)
        # A private-PC installation without Firebase must not silently create
        # shared Vortex history under the installation's connection key.
        with patch.dict(server.app.config, FIREBASE_IDENTITY=None):
            self.assertIn(self.submit(headers=self.legacy).status_code, (401, 403))

    def test_release_version_matches_sidebar_and_cached_assets(self):
        self.assertRegex(vortex.VORTEX_VERSION, r'^\d{1,4}\.\d{1,4}\.\d{1,4}$')
        html = (Path(__file__).resolve().parents[1] / 'vortex' / 'index.html').read_text(encoding='utf-8')
        label = re.search(r'id="vortexVersion"[^>]*>([^<]+)</span>', html)
        self.assertIsNotNone(label)
        self.assertEqual(label.group(1), 'v' + vortex.VORTEX_VERSION)
        self.assertIn('aria-label="Vortex web app version ' + vortex.VORTEX_VERSION + '"', html)
        for asset in ('app.js', 'sidebar-brand.css', 'vortex.css'):
            self.assertIn('../web/vortex/' + asset + '?v=' + vortex.VORTEX_VERSION + '"', html)
        app = (Path(__file__).resolve().parents[1] / 'web' / 'vortex' / 'app.js').read_text(encoding='utf-8')
        self.assertIn("from './auth.js?v=" + vortex.VORTEX_VERSION + "';", app)

    def test_authenticated_version_reporting_does_not_enqueue_work(self):
        for route in ('/api/vortex/jobs?kind=download', '/api/vortex/capabilities'):
            response = self.client.get(route, headers=self.alice)
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json['vortexVersion'], vortex.VORTEX_VERSION)
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vortex_jobs').fetchone()[0], 0)

    def test_history_status_mutations_and_file_routes_are_uid_isolated(self):
        job = self.accepted()
        own = self.client.get('/api/vortex/jobs', headers=self.alice)
        self.assertEqual(own.status_code, 200, own.json)
        self.assertIn(job, json.dumps(own.json))
        other = self.client.get('/api/vortex/jobs', headers=self.bob)
        self.assertEqual(other.status_code, 200, other.json)
        self.assertNotIn(job, json.dumps(other.json))
        for method, suffix in [('get', ''), ('post', '/cancel'), ('delete', ''),
                               ('post', '/ticket'), ('get', '/file')]:
            with self.subTest(method=method, suffix=suffix):
                response = getattr(self.client, method)('/api/vortex/jobs/' + job + suffix,
                                                       headers=self.bob)
                self.assertEqual(response.status_code, 404, response.json)
                self.assertNotIn('abcdefghijk', response.get_data(as_text=True))
        self.assertEqual(self.client.get('/api/vortex/jobs/' + job, headers=self.alice).status_code, 200)

    def test_request_receipts_are_idempotent_conflict_aware_and_uid_scoped(self):
        first = self.accepted()
        again = self.submit()
        self.assertEqual(again.status_code, 202, again.json)
        self.assertEqual(again.json['id'], first)
        for change in ({'quality': 'max'}, {'kind': 'inspect'},
                       {'downloadMode':'audio'}, {'videoFormat':'mov'}, {'audioFormat':'mp3'},
                       {'input': 'https://www.youtube.com/watch?v=other123456'}):
            with self.subTest(change=change):
                self.assertEqual(self.submit(**change).status_code, 409)
        other = self.submit(headers=self.bob)
        self.assertEqual(other.status_code, 202, other.json)
        self.assertNotEqual(other.json['id'], first)

    def test_invalid_requests_do_not_create_jobs(self):
        payloads = [None, [], 'text', {}, {'input': ' '},
                    {'input': 'x', 'kind': 'unknown', 'requestId': 'x'},
                    {'input': 'x', 'quality': 'lossless', 'requestId': 'x'},
                    {'input': 'x', 'kind': 'download', 'requestId': '../escape'},
                    {'input': ['https://youtube.com'], 'requestId': 'x'},
                    {'input': 'x' * 9000, 'requestId': 'x'}]
        payloads += [dict(input='test', kind='inspect', **change) for change in (
            {'videoFormat':'mkv'}, {'videoFormat':'best'}, {'audioFormat':'opus'}, {'downloadMode':'any'},
            {'videoFormat':'../../private'}, {'searchPage':True}, {'searchPage':-1}, {'searchPage':50}, {'searchPage':'1'})]
        payloads += [dict(input='https://youtube.com/watch?v=abcdefghijk', kind='inspect', searchPage=1)]
        for payload in payloads:
            with self.subTest(payload=str(payload)[:80]):
                response = self.client.post('/api/vortex/jobs', json=payload, headers=self.alice)
                self.assertEqual(response.status_code, 400, response.json)
        self.assertNotIn('abcdefghijk', json.dumps(self.client.get('/api/vortex/jobs', headers=self.alice).json))

    def test_output_options_and_search_page_survive_restart_with_account_isolation(self):
        response = self.submit(kind='inspect', input='test clips', searchPage=1, videoFormat='mov', audioFormat='mp3')
        self.assertEqual(response.status_code, 202, response.json)
        job = response.json['id']
        self.service.initialize()  # Repeated migrations preserve accepted jobs.
        with server.connect_db() as db:
            db.execute("UPDATE vortex_jobs SET status='processing' WHERE id=?", (job,))
        self.service._event(job, {'searchNextPage':2, 'results':[{'title':'Next result'}]})
        self.service._finish(job, {})
        result = self.client.get('/api/vortex/jobs/' + job, headers=self.alice).json
        self.assertEqual((result['searchPage'], result['searchNextPage'], result['videoFormat'], result['audioFormat']), (1, 2, 'mov', 'mp3'))
        self.assertEqual(self.client.get('/api/vortex/jobs/' + job, headers=self.bob).status_code, 404)
        self.assertEqual(self.submit(kind='inspect', input='test clips', searchPage=2, videoFormat='mov', audioFormat='mp3').status_code, 409)

    def test_cancel_queued_job_persists_after_recovery_and_keeps_receipt(self):
        job = self.accepted()
        response = self.client.post('/api/vortex/jobs/' + job + '/cancel', headers=self.alice)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['status'], 'cancelled')
        self.service.recover()
        current = self.client.get('/api/vortex/jobs/' + job, headers=self.alice)
        self.assertEqual(current.json['status'], 'cancelled')
        self.assertEqual(self.submit().json['id'], job)
        self.assertEqual(self.submit().json['status'], 'cancelled')

    def test_completion_retains_for_exactly_five_days_and_stale_bytes_are_blocked(self):
        job = self.accepted()
        row = self.complete(job)
        self.assertEqual(row['expires_at'] - row['completed_at'], 5 * 24 * 60 * 60)
        self.assertEqual(row['output_size'], len(b'private-media-bytes'))
        ticket = self.client.post('/api/vortex/jobs/' + job + '/ticket', headers=self.alice)
        self.assertEqual(ticket.status_code, 200, ticket.json)
        # The expiry check must run on reads even before the cleanup worker wakes.
        with server.connect_db() as db:
            db.execute('UPDATE vortex_jobs SET expires_at=? WHERE id=?', (time.time() - 1, job))
        for headers, path in [(self.alice, '/api/vortex/jobs/' + job + '/file'), ({}, ticket.json['url'])]:
            self.assertEqual(self.client.get(path, headers=headers).status_code, 410)
        state = self.client.get('/api/vortex/jobs/' + job, headers=self.alice).json
        self.assertEqual(state['status'], 'expired')
        self.assertFalse(state['resultReady'])
        self.assertTrue((self.service.root / job / 'media.mp4').exists())
        self.service.sweep()
        self.assertFalse((self.service.root / job).exists())
        self.assertEqual(self.service.row(job)['status'], 'expired')
        self.assertIsNone(self.service.row(job)['filename'])

    def test_file_tickets_are_scoped_short_lived_and_invalidated_by_deletion(self):
        job = self.accepted()
        self.complete(job, title='Example\r\nInjected: bad/../../title')
        minted = self.client.post('/api/vortex/jobs/' + job + '/ticket', headers=self.alice)
        self.assertEqual(minted.status_code, 200, minted.json)
        self.assertLessEqual(minted.json['expiresAt'] - time.time(), 300)
        url = minted.json['url']
        query = url.split('?', 1)[1]
        with self.client.get(url, headers={'Range': 'bytes=0-6'}) as response:
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.data, b'private')
            self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
            self.assertNotIn('Injected', response.headers)
            self.assertNotIn('\r', response.headers['Content-Disposition'])
        with self.client.head(url) as response:
            self.assertEqual(response.status_code, 200)
        other = self.accepted(requestId='second-ticket-job')
        self.complete(other)
        self.assertEqual(self.client.get('/api/vortex/jobs/' + other + '/file?' + query).status_code, 401)
        for method, path in [('get', '/api/vortex/jobs'), ('get', '/api/vortex/jobs/' + job),
                             ('post', '/api/vortex/jobs/' + job + '/cancel'),
                             ('delete', '/api/vortex/jobs/' + job), ('get', '/api/projects')]:
            self.assertEqual(getattr(self.client, method)(path + '?' + query).status_code, 401)
        changed = url[:-1] + ('0' if url[-1] != '0' else '1')
        self.assertEqual(self.client.get(changed).status_code, 401)
        with patch('vortex.time.time', return_value=minted.json['expiresAt'] + 1):
            self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.delete('/api/vortex/jobs/' + job, headers=self.alice).status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertFalse((self.service.root / job).exists())

    def test_output_traversal_and_symlink_cannot_serve_external_files(self):
        job = self.accepted()
        self.complete(job)
        secret = self.root / 'outside.txt'
        secret.write_text('OUTSIDE_SECRET', encoding='utf-8')
        for filename in ('../../outside.txt', str(secret), '..\\outside.txt'):
            with self.subTest(filename=filename), server.connect_db() as db:
                db.execute('UPDATE vortex_jobs SET filename=? WHERE id=?', (filename, job))
            response = self.client.get('/api/vortex/jobs/' + job + '/file', headers=self.alice)
            self.assertEqual(response.status_code, 410)
            self.assertNotIn('OUTSIDE_SECRET', response.get_data(as_text=True))
        try:
            (self.service.root / job / 'linked.txt').symlink_to(secret)
        except OSError:
            return  # Windows may not permit developer symlinks.
        with server.connect_db() as db:
            db.execute("UPDATE vortex_jobs SET filename='linked.txt' WHERE id=?", (job,))
        self.assertEqual(self.client.get('/api/vortex/jobs/' + job + '/file', headers=self.alice).status_code, 410)

    def test_restart_recovers_partial_work_but_preserves_completed_downloads(self):
        completed = self.accepted()
        self.complete(completed)
        interrupted = self.accepted(requestId='interrupted')
        abandoned = self.service.root / interrupted
        abandoned.mkdir()
        (abandoned / 'partial.mp4').write_bytes(b'partial')
        with server.connect_db() as db:
            db.execute("UPDATE vortex_jobs SET status='processing',attempts=1,progress=47 WHERE id=?", (interrupted,))
        server.configure(self.config)
        self.service.recover()
        row = self.service.row(interrupted)
        self.assertEqual(row['status'], 'queued')
        self.assertIsNone(row['progress'])
        self.assertFalse(abandoned.exists())
        self.assertEqual(self.service.row(completed)['status'], 'complete')
        self.assertEqual((self.service.root / completed / 'media.mp4').read_bytes(), b'private-media-bytes')
        with server.connect_db() as db:
            db.execute("UPDATE vortex_jobs SET status='processing',attempts=3 WHERE id=?", (interrupted,))
        self.service.recover()
        self.assertEqual(self.service.row(interrupted)['status'], 'error')

    def test_late_completion_cannot_overwrite_cancel_or_delete(self):
        job = self.accepted()
        directory = self.service.root / job
        directory.mkdir()
        (directory / 'media.mp4').write_bytes(b'late-file')
        with server.connect_db() as db:
            db.execute("UPDATE vortex_jobs SET status='processing' WHERE id=?", (job,))
        self.assertEqual(self.client.post('/api/vortex/jobs/' + job + '/cancel', headers=self.alice).status_code, 200)
        self.assertFalse(self.service._finish(job, {'filename': 'media.mp4'}))
        self.assertEqual(self.client.get('/api/vortex/jobs/' + job + '/file', headers=self.alice).status_code, 410)
        self.assertEqual(self.client.delete('/api/vortex/jobs/' + job, headers=self.alice).status_code, 200)
        self.assertFalse(self.service._finish(job, {'filename': 'media.mp4'}))
        self.service.recover()
        self.assertFalse(directory.exists())

    def test_unknown_progress_and_specialist_unavailability_are_truthful(self):
        job = self.accepted()
        self.assertIsNone(self.service.snapshot(self.service.row(job))['progress'])
        with server.connect_db() as db:
            db.execute("UPDATE vortex_jobs SET status='processing' WHERE id=?", (job,))
        for progress in (None, float('nan'), float('inf'), True, '50%'):
            self.service._event(job, {'phase': 'Resolving source', 'progress': progress})
            self.assertIsNone(self.service.snapshot(self.service.row(job))['progress'])
        self.service._event(job, {'progress': 32.25})
        self.assertEqual(self.service.snapshot(self.service.row(job))['progress'], 32.25)
        unavailable = {'ready': True, 'engines': {'yt-dlp': True, 'gallery-dl': False, 'spotdl': False}}
        with patch.object(self.service, 'capability', return_value=unavailable):
            for value in ('https://open.spotify.com/track/example',):
                response = self.submit(input=value, requestId='missing-tool')
                self.assertEqual(response.status_code, 503, response.json)
                self.assertIn('InstallVortexTools', response.json['error'])
            response = self.submit(input='https://instagram.com/p/example', requestId='alternative-tool')
            self.assertEqual(response.status_code, 202, response.json)

    def test_normalized_receipt_is_one_job_and_account_limits_still_apply(self):
        first = self.submit(input='youtu.be/abcdefghijk?t=30', requestId='same')
        again = self.submit(input='https://www.youtube.com/watch?v=abcdefghijk&t=30', requestId='same')
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json['id'], again.json['id'])
        for i in range(2):
            self.assertEqual(self.submit(requestId='extra-' + str(i)).status_code, 202)
        self.assertEqual(self.submit(requestId='over-limit').status_code, 429)
        other = self.submit(headers=self.bob, requestId='same')
        self.assertEqual(other.status_code, 202)
        self.assertNotEqual(other.json['id'], first.json['id'])
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vortex_jobs').fetchone()[0], 4)

    def test_inspection_receipts_expose_canonical_input_without_duplicate_jobs(self):
        # Queue/HTTP contract only: the fixture mocks DNS and never runs a provider.
        cases = (
            ('https://x.com/moviehub222/status/2104675740168155503/video/1?s=46',
             'https://x.com/moviehub222/status/2104675740168155503'),
            ('https://youtu.be/abcdefghijk?t=30',
             'https://www.youtube.com/watch?v=abcdefghijk&t=30'),
        )
        for index, (submitted, canonical) in enumerate(cases):
            with self.subTest(submitted=submitted):
                request_id = f'normalized-inspection-{index}'
                accepted = self.submit(kind='inspect', input=submitted, requestId=request_id)
                self.assertEqual(accepted.status_code, 202, accepted.json)
                job_id = accepted.json['id']
                self.assertEqual(accepted.json['input'], canonical)
                self.assertEqual(accepted.json['requestId'], request_id)
                self.assertEqual(accepted.json['kind'], 'inspect')
                polled = self.client.get('/api/vortex/jobs/' + job_id, headers=self.alice)
                self.assertEqual(polled.status_code, 200, polled.json)
                self.assertEqual(polled.json['id'], job_id)
                self.assertEqual(polled.json['input'], canonical)
                self.assertEqual(polled.json['requestId'], request_id)
                for value in (submitted, canonical):
                    repeated = self.submit(kind='inspect', input=value, requestId=request_id)
                    self.assertEqual(repeated.status_code, 202, repeated.json)
                    self.assertEqual(repeated.json['id'], job_id)
                    self.assertEqual(repeated.json['input'], canonical)
                with server.connect_db() as db:
                    self.assertEqual(db.execute(
                        'SELECT COUNT(*) FROM vortex_jobs WHERE request_id=?',
                        (request_id,)).fetchone()[0], 1)
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vortex_jobs').fetchone()[0], len(cases))

    def test_shared_global_queue_and_storage_limits_are_not_per_engine(self):
        with patch.object(vortex, 'MAX_GLOBAL_ACTIVE', 1):
            self.accepted()
            self.assertEqual(self.submit(headers=self.bob, requestId='bob').status_code, 429)
        with patch.object(vortex, 'ACCOUNT_BYTES', vortex.MAX_BYTES):
            self.assertEqual(self.submit(requestId='quota').status_code, 507)

    def test_sanitized_diagnostics_never_become_public_job_state(self):
        job = self.accepted()
        with server.connect_db() as db:
            db.execute("UPDATE vortex_jobs SET status='processing' WHERE id=?", (job,))
        before = self.service.snapshot(self.service.row(job))
        with patch.object(server.app.logger, 'info') as log:
            self.service._event(job, {'diagnostic': {'engine': 'yt-dlp', 'category': 'timeout', 'elapsedMs': 9,
                                                    'url': 'https://example.com/?signed=secret', 'cookie': 'secret'}})
            self.assertEqual(log.call_count, 1)
            self.assertNotIn('secret', str(log.call_args))
            self.service._event(job, {'diagnostic': {'engine': 'secret', 'category': 'oops', 'elapsedMs': 1}})
            self.assertEqual(log.call_count, 1)
        self.assertEqual(before, self.service.snapshot(self.service.row(job)))


class ProcessTreeCancellationTests(unittest.TestCase):
    @staticmethod
    def descendant_is_alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        state = Path('/proc') / str(pid) / 'stat'
        return state.exists() and state.read_text().split()[2] != 'Z'

    @unittest.skipUnless(os.name == 'posix' and Path('/proc').exists(), 'POSIX process-group runtime check')
    def test_cleanup_kills_descendants_after_extractor_parent_has_exited(self):
        script = ("import subprocess,sys; "
                  "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],"
                  "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(child.pid,flush=True)")
        process = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            descendant = int(process.stdout.readline().strip())
            process.wait(timeout=5)
            vortex.VortexJobs.terminate(process)
            deadline = time.monotonic() + 2
            while self.descendant_is_alive(descendant) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(self.descendant_is_alive(descendant), 'Orphaned extractor child remains alive')
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.stdout.close()

    @unittest.skipUnless(os.name == 'posix' and Path('/proc').exists(), 'POSIX process-group runtime check')
    def test_termination_kills_extractor_and_its_descendant(self):
        script = ("import subprocess,sys,time; "
                  "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                  "print(child.pid,flush=True); time.sleep(30)")
        process = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            descendant = int(process.stdout.readline().strip())
            vortex.VortexJobs.terminate(process)
            self.assertIsNotNone(process.poll())
            deadline = time.monotonic() + 2
            alive = True
            while time.monotonic() < deadline:
                try:
                    os.kill(descendant, 0)
                except ProcessLookupError:
                    alive = False
                    break
                state = Path('/proc') / str(descendant) / 'stat'
                if not state.exists() or state.read_text().split()[2] == 'Z':
                    alive = False
                    break
                time.sleep(0.01)
            self.assertFalse(alive, 'Extractor descendant survived cancellation')
        finally:
            vortex.VortexJobs.terminate(process)
            process.stdout.close()


if __name__ == '__main__':
    unittest.main()
