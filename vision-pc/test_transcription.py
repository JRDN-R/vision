"""Local transcription contract checks; no models, downloads or cloud requests."""
import base64
from contextlib import redirect_stderr
import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import server
import setup_local
import transcription as local


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.model = self.root / 'model'
        self.packages = self.root / 'packages'
        self.model.mkdir()
        (self.packages / 'faster_whisper').mkdir(parents=True)
        (self.packages / 'faster_whisper' / '__init__.py').write_text('')
        for name in ('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt'):
            (self.model / name).write_text('{}')
        self.ffmpeg = self.root / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
        self.ffmpeg.touch()
        self.ffmpeg.with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe').touch()
        config = self.root / 'config.json'
        config.write_text(json.dumps({'token': 'a'*48, 'publicAccess': True, 'ffmpeg': str(self.ffmpeg),
            'localTranscription': {'enabled': True, 'modelPath': str(self.model), 'packagesPath': str(self.packages), 'cpuThreads': 20}}))
        server.configure(config)
        server.app.config['TESTING'] = True
        self.client, self.service = server.app.test_client(), server.transcriptions
        self.service.stop.clear()
        self.headers = {'Authorization': 'Bearer '+'a'*48, 'X-Vision-Project-Key': 'k'*48, 'Origin': 'null'}
        self.base = '/api/projects/project_transcript_01'
        self.client.put(self.base, json={'project': {'nodes': []}, 'revision': 0}, headers=self.headers)
        self.body = {'clientRequestId': 'transcript_01', 'sourceName': 'meeting.mp3', 'sections': [
            {'start': 600, 'end': 630, 'mimeType': 'audio/mpeg', 'audioData': 'data:audio/mpeg;base64,'+base64.b64encode(b'ID3fakeaudio').decode()}]}

    def tearDown(self):
        self.tmp.cleanup()

    def submit(self, body=None, headers=None, base=None):
        return self.client.post((base or self.base)+'/transcriptions', json=body or self.body, headers=headers or self.headers)

    def status(self, job, base=None, headers=None):
        return self.client.get((base or self.base)+'/transcriptions/'+job, headers=headers or self.headers)

    def test_optional_health_and_auth_isolation(self):
        info = self.client.get('/api/health', headers=self.headers).json
        self.assertTrue(info['capabilities']['localTranscription'])
        self.assertTrue(info['localTranscription']['ready'])
        self.assertEqual(info['localTranscription']['cpuThreads'], 4)
        self.assertEqual(info['localTranscription']['maxRequestBytes'], 100*1024*1024)
        self.assertEqual(self.submit(headers={'X-Vision-Project-Key': 'k'*48}).status_code, 401)
        self.assertEqual(self.submit(headers={**self.headers, 'X-Vision-Project-Key': 'z'*48}).status_code, 403)
        job = self.submit().json['id']
        other = '/api/projects/project_transcript_02'
        self.client.put(other, json={'project': {}, 'revision': 0}, headers=self.headers)
        self.assertEqual(self.status(job, base=other).status_code, 404)
        self.assertEqual(self.client.delete(other+'/transcriptions/'+job, headers=self.headers).status_code, 404)
        self.assertEqual(self.status(job, headers={'Authorization': 'Bearer '+'a'*48}).status_code, 403)
        self.assertEqual(self.client.delete(self.base+'/transcriptions/'+job).status_code, 401)
        self.assertNotEqual(self.submit(base=other).json['id'], job)
        self.service.settings['enabled'] = False
        health = self.client.get('/api/health', headers=self.headers).json
        self.assertFalse(health['capabilities']['localTranscription'])
        self.assertEqual(health['localTranscription']['status'], 'disabled')
        self.assertEqual(self.submit({**self.body, 'clientRequestId': 'new'}).status_code, 503)
        self.assertEqual(self.status(job).status_code, 200)

    def test_missing_optional_package_does_not_break_health(self):
        self.service.settings['packagesPath'] = str(self.root / 'missing')
        value = self.client.get('/api/health', headers=self.headers)
        self.assertEqual(value.status_code, 200)
        self.assertFalse(value.json['localTranscription']['ready'])
        self.assertEqual(value.json['localTranscription']['status'], 'not-installed')

    def test_upload_is_durable_and_idempotency_rejects_different_content(self):
        first = self.submit()
        self.assertEqual(first.status_code, 202, first.json)
        job = first.json['id']
        self.assertEqual(self.submit().json['id'], job)
        content = self.service.root / job / 'audio-000.mp3'
        self.assertEqual(content.read_bytes(), b'ID3fakeaudio')
        changed = {**self.body, 'sections': [{**self.body['sections'][0], 'start': 601}]}
        self.assertEqual(self.submit(changed).status_code, 409)
        altered_audio = {**self.body, 'sections': [{**self.body['sections'][0], 'audioData': 'data:audio/mpeg;base64,YQ=='}]}
        self.assertEqual(self.submit(altered_audio).status_code, 409)
        self.assertNotIn(str(self.root), json.dumps(self.status(job).json))
        result = {'text': '[00:10:01.000] Hello', 'sections': [{'start': 600, 'end': 630, 'text': '[00:10:01.000] Hello'}]}
        with patch.object(self.service, 'run_local', return_value=result) as runner:
            self.assertTrue(self.service.work_once())
            self.assertFalse(self.service.work_once())
        runner.assert_called_once()
        self.assertEqual(self.status(job).json['result'], result)
        self.assertEqual(self.status(job).json['status'], 'complete')
        self.service.prune()
        self.assertFalse(content.exists())
        self.assertEqual(self.submit().json['id'], job)  # lost POST/GET replies are safe
        with server.connect_db() as db:
            db.execute('UPDATE local_transcriptions SET created_at=? WHERE id=?', (time.time()-31*86400, job))
        self.service.prune()
        self.assertEqual(self.status(job).json['result'], result)

    def test_recover_preserves_audio_and_chunk_checkpoints(self):
        job = self.submit().json['id']
        directory = self.service.root / job
        local.atomic_json(directory / 'checkpoint.json', {'chunks': {'0:0': ['existing']}})
        self.service.update(job, status='processing', attempts=1)
        orphan = self.service.root / ('f'*24)
        orphan.mkdir()
        self.service.recover()
        self.assertEqual(self.status(job).json['status'], 'queued')
        self.assertTrue((directory / 'audio-000.mp3').is_file())
        self.assertEqual(json.loads((directory / 'checkpoint.json').read_text())['chunks']['0:0'], ['existing'])
        self.assertFalse(orphan.exists())
        self.service.update(job, status='processing', attempts=3)
        self.service.recover()
        self.assertEqual(self.status(job).json['status'], 'error')

    def test_terminal_errors_are_sanitized_without_any_cloud_fallback(self):
        job = self.submit().json['id']
        with self.assertLogs(server.app.logger, level='ERROR'), patch.object(self.service, 'run_local', side_effect=RuntimeError('secret /private/path sk-bad')), patch.object(server.requests, 'request') as network:
            self.assertTrue(self.service.work_once())
        network.assert_not_called()
        state = self.status(job).json
        self.assertEqual(state['status'], 'error')
        self.assertNotIn('secret', state['error'])
        self.assertNotIn('/private', state['error'])
        self.assertEqual(self.submit().json['id'], job)

    def test_windows_progress_sharing_violation_is_retried(self):
        path = self.root / 'progress.json'
        local.atomic_json(path, {'progress': 1})
        replace = os.replace
        count = 0
        def temporarily_locked(source, target):
            nonlocal count
            count += 1
            if count < 3:
                self.assertEqual(json.loads(path.read_text()), {'progress': 1})
                raise PermissionError('Windows sharing violation')
            replace(source, target)
        with patch.object(local.os, 'replace', side_effect=temporarily_locked), patch.object(local.time, 'sleep'):
            local.atomic_json(path, {'progress': 2})
        self.assertEqual(count, 3)
        self.assertEqual(json.loads(path.read_text()), {'progress': 2})
        with patch.object(local.os, 'replace', side_effect=PermissionError('persistent denial')) as denied, patch.object(local.time, 'sleep'):
            with self.assertRaises(PermissionError):
                local.atomic_json(path, {'progress': 3})
        self.assertEqual(denied.call_count, 6)

    def test_real_child_error_is_logged_bounded_and_returned_without_private_details(self):
        # Exercise actual pipes, child exception reporting and parent cleanup.
        (self.packages / 'faster_whisper' / '__init__.py').write_text(
            "import sys\nsys.stderr.write('discard-this-prefix' + 'x'*200000 + '\\n')\n"
            "raise ImportError('missing test DLL /private/path sk-private')\n")
        job = self.submit().json['id']
        with self.assertLogs(server.app.logger, level='ERROR') as logs:
            self.assertTrue(self.service.work_once())
        error = self.status(job).json['error']
        self.assertIn('dependency / dependencies / ImportError', error)
        self.assertIn('exit 0x00000001', error)
        self.assertNotIn('/private', error)
        self.assertNotIn('sk-private', error)
        diagnostic = next(record.getMessage() for record in logs.records if 'worker exited' in record.getMessage())
        self.assertIn('missing test DLL', diagnostic)
        self.assertNotIn('discard-this-prefix', diagnostic)
        self.assertLess(len(diagnostic), local.DIAGNOSTIC_LIMIT + 200)
        self.service.prune()
        self.assertIn('ImportError', self.status(job).json['error'])

    def test_native_exit_keeps_last_stage_and_exit_code(self):
        (self.packages / 'faster_whisper' / '__init__.py').write_text('import os\nos._exit(37)\n')
        job = self.submit().json['id']
        with self.assertLogs(server.app.logger, level='ERROR'):
            self.service.work_once()
        error = self.status(job).json['error']
        self.assertIn('dependencies', error)
        self.assertIn('exit 0x00000025', error)

    def test_stalled_import_is_stopped_before_the_recording_time_limit(self):
        (self.packages / 'faster_whisper' / '__init__.py').write_text('import time\ntime.sleep(60)\n')
        job = self.submit().json['id']
        children = []
        launch = subprocess.Popen
        def remember_child(*args, **kwargs):
            child = launch(*args, **kwargs)
            children.append(child)
            return child
        with patch.object(local, 'DEPENDENCY_TIMEOUT', 2), patch.object(local.subprocess, 'Popen', side_effect=remember_child), self.assertLogs(server.app.logger, level='ERROR'):
            self.service.work_once()
        value = self.status(job).json
        self.assertEqual(value['status'], 'error')
        self.assertIn('dependency-timeout / dependencies', value['error'])
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll(), 'A stalled import must not leave an orphan worker')
        self.assertFalse(self.service._worker_lock.locked())

    def test_check_output_excludes_other_jobs_logs(self):
        log = self.root / 'server.log'
        log.write_text('2026-10-02 00:00:00 ERROR unrelated private information\n'
                       '2026-10-02 00:01:00 ERROR Local transcription job abc worker exited 1. Local diagnostic:\n'
                       'Traceback for generated check\nImportError: test DLL missing\n'
                       '2026-10-02 00:02:00 ERROR other job private information\n')
        output = io.StringIO()
        with redirect_stderr(output):
            setup_local.print_worker_diagnostic(log, 'abc')
        self.assertIn('test DLL missing', output.getvalue())
        self.assertNotIn('private information', output.getvalue())

    def test_validation_size_queue_disk_limits(self):
        for invalid in (float('nan'), float('inf'), True, '1', None):
            body = {**self.body, 'sections': [{**self.body['sections'][0], 'start': invalid}]}
            self.assertEqual(self.submit(body).status_code, 400)
        for changes in ({'end': 8*3600+1}, {'end': 600}, {'mimeType': 'text/plain'}, {'audioData': 'data:audio/wav;base64,YQ=='}, {'audioData': 'data:audio/mpeg;base64,***'}):
            self.assertEqual(self.submit({**self.body, 'sections': [{**self.body['sections'][0], **changes}]}).status_code, 400)
        self.assertEqual(self.submit({**self.body, 'sections': self.body['sections']*2}).status_code, 400)
        self.assertEqual(self.submit({**self.body, 'sections': []}).status_code, 400)
        with patch.object(local, 'BODY_LIMIT', 32):
            response = self.submit()
        self.assertEqual(response.status_code, 413)
        self.assertIn('100 MB', response.json['error'])
        with patch.object(local.shutil, 'disk_usage', return_value=SimpleNamespace(free=1024)):
            self.assertEqual(self.submit().status_code, 507)
        with patch.object(local, 'MAX_QUEUED', 1):
            self.assertEqual(self.submit().status_code, 202)
            self.assertEqual(self.submit({**self.body, 'clientRequestId': 'another'}).status_code, 429)

    def test_cancel_queued_and_running_jobs(self):
        job = self.submit().json['id']
        url = self.base+'/transcriptions/'+job
        self.assertEqual(self.client.delete(url, headers=self.headers).json['status'], 'cancelled')
        with patch.object(self.service, 'run_local') as runner:
            self.assertFalse(self.service.work_once())
        runner.assert_not_called()
        self.assertEqual(self.submit().json['status'], 'cancelled')
        job2 = self.submit({**self.body, 'clientRequestId': 'another'}).json['id']
        def cancel_during_run(value):
            self.client.delete(self.base+'/transcriptions/'+value['id'], headers=self.headers)
            return {'text': 'must not resurrect cancelled job', 'sections': []}
        with patch.object(self.service, 'run_local', side_effect=cancel_during_run):
            self.assertTrue(self.service.work_once())
        self.assertEqual(self.status(job2).json['status'], 'cancelled')
        self.assertNotIn('result', self.status(job2).json)

    def test_runner_timestamp_offsets_chunk_checkpoints_and_offline_cpu_options(self):
        body = {**self.body, 'sections': [{**self.body['sections'][0], 'start': 800, 'end': 1802}]}
        job = self.submit(body).json['id']
        directory = self.service.root / job
        model = Mock()
        model.transcribe.side_effect = lambda *a, **k: (iter([SimpleNamespace(start=1.25, text=' Hello there ')]), None)
        factory = Mock(return_value=model)
        def fake_media(command, **kwargs):
            if 'ffprobe' in str(command[0]):
                return SimpleNamespace(stdout=b'{"format":{"duration":"1002"}}')
            Path(command[-1]).write_bytes(b'wav')
            return SimpleNamespace()
        with patch.object(local.subprocess, 'run', side_effect=fake_media):
            local.process_directory(directory, self.model, 99, str(self.ffmpeg), model_factory=factory)
        factory.assert_called_once_with(str(self.model), device='cpu', compute_type='int8', cpu_threads=4, num_workers=1, local_files_only=True)
        result = json.loads((directory / 'result.json').read_text())
        self.assertEqual(result['sections'][0]['start'], 800)
        self.assertIn('[00:13:21.250] Hello there', result['text'])
        self.assertIn('[00:28:21.250] Hello there', result['text'])
        self.assertEqual(model.transcribe.call_count, 2)
        self.assertFalse((directory / 'working.wav').exists())
        with patch.object(local.subprocess, 'run', side_effect=fake_media):
            local.process_directory(directory, self.model, 4, str(self.ffmpeg), model_factory=factory)
        self.assertEqual(model.transcribe.call_count, 2)  # restart uses durable chunk receipts

    def test_cancel_wins_race_against_worker_terminal_update(self):
        job = self.submit().json['id']
        update = self.service.update
        def concurrent_cancel(job_id, **fields):
            if fields.get('status') == 'complete':
                update(job_id, unless_terminal=True, status='cancelled', phase='Cancelled', cancel_requested=1)
            return update(job_id, **fields)
        with patch.object(self.service, 'update', side_effect=concurrent_cancel), patch.object(self.service, 'run_local', return_value={'text':'late', 'sections': []}):
            self.service.work_once()
        self.assertEqual(self.status(job).json['status'], 'cancelled')
        self.assertNotIn('result', self.status(job).json)
        self.assertEqual(update(job, only_processing=True, status='error', phase='Stopped'), 0)

    def test_runner_rejects_forged_audio_duration(self):
        job = self.submit().json['id']
        with patch.object(local.subprocess, 'run', return_value=SimpleNamespace(stdout=b'{"format":{"duration":"999999"}}')):
            with self.assertRaises(ValueError):
                local.process_directory(self.service.root / job, self.model, 4, str(self.ffmpeg), model_factory=Mock())

    def test_parent_pipe_shutdown_stops_native_worker_process(self):
        # A deliberately sleeping fake model shows that closing the parent pipe
        # stops inference without waiting for the model to yield a segment.
        marker = self.root / 'inference-started'
        (self.packages / 'faster_whisper' / '__init__.py').write_text('import time\nfrom pathlib import Path\nclass WhisperModel:\n def __init__(self,*a,**k):\n  Path('+repr(str(marker))+').touch()\n  time.sleep(60)\n')
        job = self.submit().json['id']
        child = subprocess.Popen([sys.executable, str(Path(local.__file__).resolve()), '--process', str(self.service.root / job),
            '--model', str(self.model), '--ffmpeg', str(self.ffmpeg), '--packages', str(self.packages)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline and child.poll() is None:
                time.sleep(.02)
            self.assertTrue(marker.exists(), 'Fake model did not start')
            self.assertIsNone(child.poll())
            child.stdin.close()
            self.assertEqual(child.wait(timeout=5), 1)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    def test_windows_parent_watch_never_blocks_on_a_crt_input_read(self):
        # Exercise the Windows branch on every test platform. The existing
        # process lifecycle tests also exercise the real Win32 API on Windows.
        peek = Mock(side_effect=[1, 1, 0])  # alive twice, then broken pipe
        windows = SimpleNamespace(name='nt', read=Mock(), _exit=Mock())
        msvcrt = SimpleNamespace(get_osfhandle=Mock(return_value=0x123456789))
        with patch.object(local, 'os', windows), patch.dict(sys.modules, {'msvcrt': msvcrt}), \
                patch.object(ctypes, 'WinDLL', return_value=SimpleNamespace(PeekNamedPipe=peek), create=True), \
                patch.object(local.sys, 'stdin', SimpleNamespace(fileno=lambda: 0)), patch.object(local.time, 'sleep') as pause:
            thread = local.start_parent_watch()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        windows.read.assert_not_called()
        windows._exit.assert_called_once_with(1)
        msvcrt.get_osfhandle.assert_called_once_with(0)
        self.assertEqual(pause.call_count, 2)
        self.assertEqual(peek.call_args.args[0].value, 0x123456789)  # no 64-bit handle truncation
        self.assertEqual(peek.call_args.args[1:], (None, 0, None, None, None))

    def test_child_normal_completion_does_not_abort_with_parent_pipe_open(self):
        (self.packages / 'faster_whisper' / '__init__.py').write_text('class WhisperModel:\n def __init__(self,*a,**k): pass\n')
        job = self.submit().json['id']
        directory = self.service.root / job
        # A completed/empty internal manifest exercises normal interpreter exit
        # without requiring FFmpeg or an actual model in the offline unit suite.
        local.atomic_json(directory / 'manifest.json', {'sections': []})
        child = subprocess.Popen([sys.executable, str(Path(local.__file__).resolve()), '--process', str(directory),
            '--model', str(self.model), '--ffmpeg', str(self.ffmpeg), '--packages', str(self.packages)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            self.assertEqual(child.wait(timeout=5), 0)
            self.assertEqual(json.loads((directory / 'result.json').read_text()), {'text':'', 'sections':[]})
        finally:
            child.stdin.close()
            child.stderr.close()
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    def test_sound_request_requires_installation_and_boolean_option(self):
        health = self.client.get('/api/health', headers=self.headers).json
        self.assertFalse(health['localSoundEvents']['ready'])
        self.assertFalse(health['capabilities']['soundEvents'])
        self.assertEqual(health['localSoundEvents']['engine'], 'PretrainedSED-BEATs')
        self.assertEqual(self.submit({**self.body, 'includeSoundEvents': 'true'}).status_code, 400)
        denied = self.submit({**self.body, 'includeSoundEvents': True})
        self.assertEqual(denied.status_code, 503)
        self.assertIn('InstallSoundEvents', denied.json['error'])
        self.assertEqual(self.submit().status_code, 202)

    def test_disabled_sound_installation_never_reports_ready(self):
        adapter = SimpleNamespace(capability=lambda settings: {'ready': True, 'available': False, 'installed': True, 'status': 'disabled'})
        with patch.dict(sys.modules, {'sound_model': adapter}):
            result = self.service.sound_capability({'enabled': False})
        self.assertFalse(result['ready'])
        self.assertFalse(result['available'])
        self.assertEqual(result['status'], 'disabled')

    def test_sound_request_hash_snapshot_and_account_isolation(self):
        self.service.sound_settings = {'enabled': True, 'pythonPath': '/sound/python', 'assetsPath': '/sound/assets',
                                       'device': 'cpu', 'cpuThreads': 2}
        body = {**self.body, 'includeSoundEvents': True}
        with patch.object(self.service, 'sound_capability', return_value={'ready': True}):
            job = self.submit(body).json['id']
        self.assertEqual(self.submit(body).json['id'], job, 'Accepted receipts survive later installation changes')
        self.assertEqual(self.submit().status_code, 409, 'Speech-only and sound requests cannot share a receipt')
        self.service.sound_settings['assetsPath'] = '/different/assets'
        self.service.settings['cpuThreads'] = 1
        manifest = json.loads((self.service.root / job / 'manifest.json').read_text())
        self.assertTrue(manifest['includeSoundEvents'])
        self.assertEqual(manifest['soundSettings']['assetsPath'], '/sound/assets')
        self.assertEqual(manifest['whisperSettings']['cpuThreads'], 4)
        self.assertEqual(self.status(job, headers={**self.headers, 'X-Vision-Project-Key': 'z'*48}).status_code, 403)

    def test_sound_merge_offsets_gaps_overlaps_and_speech_preserved(self):
        speech = {'text': '[00:10:01.000] Hello\n\n[00:20:00.000] Again',
                  'sections': [{'start': 600, 'end': 610, 'text': 'Hello'}, {'start': 1200, 'end': 1210, 'text': 'Again'}],
                  'speechSegments': [{'start': 601, 'end': 604, 'text': 'Hello'}, {'start': 1200, 'end': 1202, 'text': 'Again'}]}
        sound = {'status': 'completed', 'soundEvents': [{'start': 600, 'end': 602, 'label': 'Meow', 'score': .9},
                                                     {'start': 1203, 'end': 1204, 'label': 'Smash, crash', 'score': .8}]}
        result = local.combine_transcription(speech, sound)
        self.assertEqual(result['speechText'], speech['text'])
        self.assertIn('[00:10:00.000] *cat meows*', result['sections'][0]['text'])
        self.assertIn('[00:20:03.000] *smash/crash*', result['sections'][1]['text'])
        self.assertEqual(result['soundEventStatus'], 'completed')
        self.assertEqual(result['warnings'], [])
        self.assertIn('00:10:01,000 --> 00:10:02,000\n*cat meows*\nHello', result['combinedSrt'])
        self.assertNotIn('00:10:04,000 --> 00:20:00,000', result['combinedSrt'], 'Section gaps must not get captions')
        failed = local.combine_transcription(speech, None)
        self.assertEqual(failed['speechSegments'], speech['speechSegments'])
        self.assertEqual(failed['soundEventStatus'], 'failed')
        self.assertIn('speech transcript is preserved', failed['warnings'][0])
        self.assertIn('Hello', failed['combinedSrt'])
        self.assertNotIn('meows', failed['text'])
        with self.assertRaises(ValueError):
            local.combine_transcription(speech, {'status': 'completed', 'soundEvents': [{'start': 800, 'end': 801, 'label': 'Meow', 'score': .9}]})
        with self.assertRaises(ValueError):
            local.combine_transcription(speech, {'status': 'partial', 'soundEvents': []})

    def test_sound_failure_keeps_speech_and_sanitizes_failure_without_fallback(self):
        with patch.object(self.service, 'sound_capability', return_value={'ready': True}):
            job = self.submit({**self.body, 'includeSoundEvents': True}).json['id']
        speech = {'text': '[00:10:01.000] Hello', 'sections': [{'start': 600, 'end': 630, 'text': 'Hello'}],
                  'speechSegments': [{'start': 601, 'end': 602, 'text': 'Hello'}]}
        with patch.object(self.service, 'run_worker', return_value=speech) as worker, \
                patch.object(self.service, 'run_sound_events', side_effect=RuntimeError('secret /private sk-hidden')), \
                patch.object(server.requests, 'request') as network, self.assertLogs(server.app.logger, level='ERROR'):
            self.service.work_once()
        self.assertEqual(worker.call_count, 1)
        network.assert_not_called()
        result = self.status(job).json
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['result']['speechText'], speech['text'])
        self.assertEqual(result['result']['soundEventStatus'], 'failed')
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('/private', json.dumps(result))
        self.service.prune()
        self.assertFalse((self.service.root / job).exists())
        self.assertEqual(self.status(job).json['result']['speechText'], speech['text'])

    def test_sound_worker_is_sequential_and_restart_reuses_speech(self):
        with patch.object(self.service, 'sound_capability', return_value={'ready': True}):
            job = self.submit({**self.body, 'includeSoundEvents': True}).json['id']
        speech = {'text': 'Hello', 'sections': [{'start': 600, 'end': 630, 'text': 'Hello'}],
                  'speechSegments': [{'start': 601, 'end': 602, 'text': 'Hello'}]}
        calls = []
        def whisper(*args, **kwargs):
            self.assertFalse(kwargs['sound'])
            calls.append('whisper-finished')
            local.atomic_json(self.service.root / job / 'result.json', speech)
            return speech
        def sounds(*args, **kwargs):
            self.assertTrue((self.service.root / job / 'result.json').is_file())
            calls.append('sound-started')
            return {'status': 'completed', 'soundEvents': []}
        with patch.object(self.service, 'run_worker', side_effect=whisper), patch.object(self.service, 'run_sound_events', side_effect=sounds):
            self.service.run_local(self.service.row(job))
        self.assertEqual(calls, ['whisper-finished', 'sound-started'])
        with patch.object(self.service, 'run_worker') as whisper_worker, patch.object(self.service, 'run_sound_events', side_effect=sounds):
            result = self.service.run_local(self.service.row(job))
        whisper_worker.assert_not_called()
        self.assertEqual(result['speechText'], 'Hello')

    def test_sound_cancellation_cannot_publish_completed_result(self):
        with patch.object(self.service, 'sound_capability', return_value={'ready': True}):
            job = self.submit({**self.body, 'includeSoundEvents': True}).json['id']
        speech = {'text': 'Hello', 'sections': [{'start': 600, 'end': 630, 'text': 'Hello'}],
                  'speechSegments': [{'start': 601, 'end': 602, 'text': 'Hello'}]}
        def cancel(*args):
            self.client.delete(self.base+'/transcriptions/'+job, headers=self.headers)
            return {'status': 'completed', 'soundEvents': []}
        with patch.object(self.service, 'run_worker', return_value=speech), patch.object(self.service, 'run_sound_events', side_effect=cancel):
            self.service.work_once()
        result = self.status(job).json
        self.assertEqual(result['status'], 'cancelled')
        self.assertNotIn('result', result)

    def test_sound_enabled_whisper_writes_real_segment_ends_and_durable_offsets(self):
        with patch.object(self.service, 'sound_capability', return_value={'ready': True}):
            job = self.submit({**self.body, 'includeSoundEvents': True}).json['id']
        model = Mock()
        model.transcribe.return_value = (iter([SimpleNamespace(start=1.25, end=3.75, text='Hello')]), None)
        def fake_media(command, **kwargs):
            if 'ffprobe' in str(command[0]):
                return SimpleNamespace(stdout=b'{"format":{"duration":"30"}}')
            Path(command[-1]).write_bytes(b'wav')
            return SimpleNamespace()
        directory = self.service.root / job
        with patch.object(local.subprocess, 'run', side_effect=fake_media):
            local.process_directory(directory, self.model, 2, str(self.ffmpeg), model_factory=Mock(return_value=model))
            local.process_directory(directory, self.model, 2, str(self.ffmpeg), model_factory=Mock(return_value=model))
        self.assertEqual(model.transcribe.call_count, 1)
        result = json.loads((directory / 'result.json').read_text())
        self.assertEqual(result['speechSegments'], [{'start': 601.25, 'end': 603.75, 'text': 'Hello'}])
        self.assertFalse((directory / 'working.wav').exists())

    def test_real_sound_subprocess_lifecycle_and_cancellation(self):
        job = self.submit().json['id']
        value = self.service.row(job)
        directory = self.service.root / job
        adapter = self.root / 'sound_model.py'
        adapter.write_text("import sys,os,json\nfrom pathlib import Path\n"
                          "p=Path(sys.argv[sys.argv.index('--process')+1])\n"
                          "assert os.environ['HF_HUB_OFFLINE']=='1'\n"
                          "assert os.environ['OMP_NUM_THREADS']=='2'\n"
                          "(p/'sound-result.json').write_text(json.dumps({'status':'completed','soundEvents':[]}))\n")
        settings = {'enabled': True, 'pythonPath': sys.executable, 'assetsPath': str(self.root), 'cpuThreads': 2, 'device': 'cpu'}
        with patch.object(local, '__file__', str(self.root / 'transcription.py')), \
                patch.object(self.service, 'sound_capability', return_value={'ready': True}):
            result = self.service.run_sound_events(value, settings)
        self.assertEqual(result, {'status': 'completed', 'soundEvents': []})
        (directory / 'sound-result.json').unlink()
        adapter.write_text("import time,sys\nfrom pathlib import Path\n"
                          "p=Path(sys.argv[sys.argv.index('--process')+1])\n"
                          "(p/'started').touch()\ntime.sleep(60)\n")
        children = []
        launch = subprocess.Popen
        def remember(*args, **kwargs):
            child = launch(*args, **kwargs)
            children.append(child)
            return child
        def request_cancel():
            deadline = time.monotonic() + 5
            while not (directory / 'started').is_file() and time.monotonic() < deadline:
                time.sleep(.02)
            self.service.update(job, status='cancelled', cancel_requested=1)
        thread = threading.Thread(target=request_cancel)
        thread.start()
        try:
            with patch.object(local, '__file__', str(self.root / 'transcription.py')), \
                    patch.object(self.service, 'sound_capability', return_value={'ready': True}), \
                    patch.object(local.subprocess, 'Popen', side_effect=remember):
                self.assertIsNone(self.service.run_sound_events(value, settings))
        finally:
            thread.join(timeout=6)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll(), 'Cancelling sound recognition must terminate its worker')
        self.assertFalse((directory / 'sound-result.json').exists())


if __name__ == '__main__':
    unittest.main()
