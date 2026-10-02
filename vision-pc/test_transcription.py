"""Local transcription contract checks; no models, downloads or cloud requests."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import server
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
        with patch.object(self.service, 'run_local', side_effect=RuntimeError('secret /private/path sk-bad')), patch.object(server.requests, 'request') as network:
            self.assertTrue(self.service.work_once())
        network.assert_not_called()
        state = self.status(job).json
        self.assertEqual(state['status'], 'error')
        self.assertNotIn('secret', state['error'])
        self.assertNotIn('/private', state['error'])
        self.assertEqual(self.submit().json['id'], job)

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


if __name__ == '__main__':
    unittest.main()
