"""Real auth/storage/pixel and FFmpeg tests; inference is an explicit fixture.

These tests do NOT assert model quality or real Windows hardware acceptance.
"""
import base64
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
import server
from context_engine import ContextEngine
from local_preferences import MODEL, DISCLOSURE, EPOCH
from local_runtime import LocalFailure
from local_visual_media import image_views, video_frames, requested_times
from local_visual_runtime import VisualRuntime, MODEL_SHA, PROJECTOR_SHA
from owned_assets import exclusive_file
import test_local_inference as local_tests
import local_acceptance
import local_visual_runtime
from types import SimpleNamespace


class VisualProtocolFixture:
    calls = []
    events = []
    peak_rss = 1024
    def __init__(self, settings, cancelled, stage):
        self.cancelled = cancelled
    def __enter__(self):
        self.events.append('visual-start')
        return self
    def __exit__(self, *args):
        self.events.append('visual-stop')
    def inspect(self, url, question):
        raw = base64.b64decode(url.split(',', 1)[1])
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            assert image.width <= 1600 and image.height <= 1600
        self.calls.append((question, raw))
        with unittest.TestCase().assertRaises(BlockingIOError):
            with exclusive_file(server.app.config['DATA_DIR'] / 'local-inference.lock'):
                pass
        return 'Fixture observation: visible label reads SERIAL 42; uncertain small print.'


class AnswerProtocolFixture(local_tests.FakeRuntime):
    def __enter__(self):
        VisualProtocolFixture.events.append('answer-start')
        return self
    def __exit__(self, *args):
        VisualProtocolFixture.events.append('answer-stop')


class CombinedLocalTests(unittest.TestCase):
    setUpClass = classmethod(local_tests.LocalInferenceTests.setUpClass.__func__)
    token = local_tests.LocalInferenceTests.token
    headers = local_tests.LocalInferenceTests.headers
    new = local_tests.LocalInferenceTests.new
    send = local_tests.LocalInferenceTests.send

    def setUp(self):
        local_tests.LocalInferenceTests.setUp(self)
        self.local.runtime_factory = AnswerProtocolFixture
        self.old_visual = self.local.visual.runtime_factory
        self.local.visual.runtime_factory = VisualProtocolFixture
        self.addCleanup(setattr, self.local.visual, 'runtime_factory', self.old_visual)
        self.local.settings['visualEnabled'] = True
        self.local.visual.confirmed = None
        self.signature = patch.object(self.local.visual, 'signature', return_value='fixture-profile')
        self.signature.start()
        self.addCleanup(self.signature.stop)
        VisualProtocolFixture.calls = []
        VisualProtocolFixture.events = []

    def tearDown(self):
        local_tests.LocalInferenceTests.tearDown(self)

    @staticmethod
    def screenshot(width=600, height=320):
        image = Image.new('RGB', (width, height), '#fafafa')
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, width-20, height-20), outline='#1144aa', width=5)
        draw.text((50, 100), 'SERIAL 42', fill='black', font_size=32)
        output = io.BytesIO()
        image.save(output, 'PNG')
        return output.getvalue()

    def upload(self, data=None, name='screenshot.png', message='Read the label.', **options):
        body = dict(clientRequestId='visual-'+str(len(VisualProtocolFixture.calls)), model=MODEL,
                    message=message, maxOutputTokens=512, runOptions={'codeInterpreter':False}, **options)
        result = self.client.post('/api/projects/'+self.cid+'/runs', headers=self.a,
                    data={'options':json.dumps(body), 'attachments':(io.BytesIO(data or self.screenshot()), name)})
        self.assertEqual(result.status_code, 202, result.json)
        return result.json['runId']

    def run_job(self, rid):
        with patch.object(self.s, 'call', side_effect=AssertionError('No cloud requests')), \
                patch.object(self.s.venture.funding, 'bind', side_effect=AssertionError('No cloud funding')):
            self.s.work_once()
        return self.s.snapshot(rid)

    def test_pixels_then_answer_source_links_and_capability_are_account_safe(self):
        self.assertFalse(self.local.catalog()['capabilities']['vision'])
        rid = self.upload()
        result = self.run_job(rid)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(VisualProtocolFixture.events, ['visual-start','visual-stop','answer-start','answer-stop'])
        self.assertTrue(self.local.catalog()['capabilities']['vision'])
        self.assertFalse(self.local.catalog()['defaultMigrationEligible'])
        self.assertIn('SERIAL 42', json.dumps(local_tests.FakeRuntime.requests))
        record = result['localVisualEvidence'][0]
        self.assertIn(record['reference'], json.dumps(local_tests.FakeRuntime.requests))
        self.assertEqual(record['name'], 'screenshot.png')
        self.assertNotIn(str(self.root), json.dumps(result))
        response = self.client.get(record['sourceURL'], headers=self.a)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        response.close()
        self.assertEqual(self.client.get(record['sourceURL'], headers=self.b).status_code, 404)
        self.assertEqual(self.client.get(record['sourceURL']).status_code, 401)
        stats = result['localQueue']['metrics']['visual']
        self.assertEqual(stats['inspectedViews'], 1)
        self.assertGreater(stats['evidenceBytes'], 0)

    def test_same_question_reuses_evidence_but_followup_reexamines_pixels(self):
        rid = self.upload()
        self.assertEqual(self.run_job(rid)['status'], 'completed')
        same = self.send(request_id='retry-same', message='Read the label.', maxOutputTokens=512,
                         retryOf=rid).json['runId']
        result = self.run_job(same)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['localQueue']['metrics']['visual']['cachedViews'], 1)
        self.assertEqual(len(VisualProtocolFixture.calls), 1)
        changed = self.send(request_id='new-question', previousRunId=rid,
                            message='What color is the border?', maxOutputTokens=512).json['runId']
        self.assertEqual(self.run_job(changed)['status'], 'completed')
        self.assertEqual(len(VisualProtocolFixture.calls), 2)
        self.assertIn('What color is the border?', VisualProtocolFixture.calls[-1][0])

    def test_new_pixels_and_missing_original_do_not_reuse_old_findings(self):
        rid = self.upload()
        self.run_job(rid)
        source = Path(json.loads(self.s.row(rid)['inputs_json'])[0]['path'])
        source.write_bytes(self.screenshot(640, 400))
        next_id = self.send(request_id='changed-bytes', message='Read the label.', retryOf=rid, maxOutputTokens=512).json['runId']
        self.assertEqual(self.run_job(next_id)['status'], 'completed')
        self.assertEqual(len(VisualProtocolFixture.calls), 2)
        source.unlink()
        result = self.send(request_id='missing-source', message='Read the label.', retryOf=rid)
        self.assertEqual(result.status_code, 409)

    def test_visual_cancel_stops_before_answer_and_does_not_cache_incomplete_findings(self):
        rid = self.upload()
        def cancel(*args):
            self.s.update(rid, cancel_requested=1)
            return 'Do not cache this cancelled finding.'
        with patch.object(VisualProtocolFixture, 'inspect', side_effect=cancel):
            result = self.run_job(rid)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(local_tests.FakeRuntime.requests, [])
        self.assertFalse(list(self.s.venture.root(self.cid).glob('local-visual/cache/*.json')))
        self.assertEqual(VisualProtocolFixture.events, ['visual-start','visual-stop'])

    def test_restart_preserves_receipts_and_requires_fresh_backend_confirmation(self):
        rid = self.upload()
        self.run_job(rid)
        follow = self.send(request_id='interrupted-visual', message='Read the label.', retryOf=rid).json['runId']
        self.s.update(follow, status='preparing')
        self.local.initialize(self.local.settings)
        self.local.recover()
        self.assertEqual(self.s.row(follow)['status'], 'error')
        self.assertFalse(self.local.catalog()['capabilities']['vision'])
        record = self.s.snapshot(rid)['localVisualEvidence'][0]
        with self.client.get(record['sourceURL'], headers=self.a) as response:
            self.assertEqual(response.status_code, 200)
        retried = self.send(request_id='after-restart', message='Read the label.', retryOf=rid, maxOutputTokens=512).json['runId']
        self.assertEqual(self.run_job(retried)['status'], 'completed')
        self.assertEqual(len(VisualProtocolFixture.calls), 2)

    def test_queued_combined_request_survives_backend_reinitialization(self):
        rid = self.upload()
        self.local.initialize(self.local.settings)
        self.local.recover()
        self.assertEqual(self.s.row(rid)['status'], 'queued')
        self.assertEqual(self.run_job(rid)['status'], 'completed')

    def test_disabled_visuals_fail_without_cloud_or_false_claim(self):
        self.local.settings['visualEnabled'] = False
        rid = self.upload()
        result = self.run_job(rid)
        self.assertEqual(result['status'], 'error')
        self.assertIn('disabled', result['error'])
        self.assertEqual(local_tests.FakeRuntime.requests, [])

    def test_old_disclosure_and_default_epoch_cannot_bypass_acceptance(self):
        response = self.client.post('/api/venture/local/disclosure', headers=self.a, json={'version':'qwen-local-v1'})
        self.assertEqual(response.status_code, 409)
        self.local.settings['defaultEpoch'] = EPOCH
        self.assertNotEqual(self.local.preferences.get('firebase:bob')['settings']['model'], MODEL)

    def test_readable_screenshot_preparation_keeps_question_crops(self):
        rid = self.upload(self.screenshot(2000, 1400))
        result = self.run_job(rid)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len(result['localVisualEvidence']), 5)
        self.assertEqual(result['localVisualEvidence'][1]['crop'], 'top left')

    def test_real_photo_bytes_reach_visual_protocol_before_answer(self):
        path = Path(__file__).parents[1] / 'tests/fixtures/local-visual/hopper.jpg'
        rid = self.upload(path.read_bytes(), 'photo.jpg', 'Describe the visible person.')
        result = self.run_job(rid)
        self.assertEqual(result['status'], 'completed', result)
        self.assertGreater(len(VisualProtocolFixture.calls[0][1]), 1000)

    def test_pinned_board_frames_keep_timestamp_and_reject_cross_account_binding(self):
        engine = self.s.context.engine
        pid = 'board_visual_0000000001'
        data = 'data:image/png;base64,'+base64.b64encode(self.screenshot()).decode()
        value = dict(nodes=[dict(id='video', attachments=[dict(id='frame', name='frame.png', data=data,
                     mime='image/png', role='video-frame', timestamp=12.25, videoOf='original')])])
        saved = self.client.put('/api/projects/'+pid, headers=self.a, json={'revision':0,'project':value})
        self.assertEqual(saved.status_code, 200, saved.json)
        engine.index_project('firebase:alice', pid, 1, value)
        body = dict(clientRequestId='board-visual', model=MODEL, message='Read the label at 0:12.',
                    maxOutputTokens=512, boardContext=dict(projectId=pid, revision=1, mode='adaptive'))
        accepted = self.client.post('/api/projects/'+self.cid+'/runs', headers=self.a, data={'options':json.dumps(body)})
        self.assertEqual(accepted.status_code, 202, accepted.json)
        rid = accepted.json['runId']
        result = self.run_job(rid)
        self.assertEqual(result['status'], 'completed', result)
        record = result['localVisualEvidence'][0]
        self.assertEqual(record['timestamp'], '12.25')
        self.assertEqual(record['timestampBasis'], 'saved-frame-label')
        self.assertIn('ctx:'+pid+':1:', record['reference'])
        # Durable worker binding is revalidated after ownership changes.
        next_id = self.send(request_id='stale-board', retryOf=rid, message='Read the label at 0:12.').json['runId']
        with self.s.db() as db:
            db.execute('UPDATE projects SET owner_uid=? WHERE id=?', ('firebase:bob', pid))
        self.assertEqual(self.run_job(next_id)['status'], 'error')


class VisualMediaTests(unittest.TestCase):
    def test_time_selection_is_bounded_and_supports_seconds_and_hms(self):
        self.assertEqual(requested_times('At 1:02.5 and 0:01:03, then 4 seconds and 2 minutes'), [62.5,63.,4.,120.])

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg unavailable')
    def test_real_video_seeking_keeps_decoded_timestamp_not_requested_label(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'video.mp4'
            subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=320x180:rate=10:duration=3', '-c:v', 'mpeg4', str(path)], check=True)
            frames = list(video_frames(path, 'Inspect 1.05 seconds', shutil.which('ffmpeg'), root, lambda:False))
            self.assertEqual(len(frames), 1)
            self.assertEqual(frames[0]['requestedTimestamp'], 1.05)
            self.assertAlmostEqual(frames[0]['timestamp'], 1.1, places=5)
            self.assertEqual(frames[0]['timestampBasis'], 'decoded-presentation-time')
            with self.assertRaisesRegex(LocalFailure, 'outside'):
                list(video_frames(path, 'At 4 seconds', shutil.which('ffmpeg'), root, lambda:False))

    def test_visual_runtime_cpu_and_projector_are_explicit(self):
        runtime = VisualRuntime({'gpuLayers':16,'contextTokens':16384}, lambda:False, lambda phase:None)
        try:
            runtime.projector = Path('matching-projector.gguf')
            command = runtime.command(Path('llama.exe'), Path('visual.gguf'))
            self.assertEqual(command[command.index('--n-gpu-layers')+1], '0')
            self.assertEqual(command[command.index('--ctx-size')+1], '4096')
            self.assertIn('--no-mmproj-offload', command)
            self.assertEqual(runtime.settings['maxResidentGiB'], 4)
            self.assertFalse(runtime.http.trust_env)
        finally:
            runtime.http.close()

    def test_visual_projector_mismatch_fails_before_launch_without_replacing_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            main = root/'main.gguf';main.write_bytes(b'main')
            executable = root/'llama.exe';executable.write_bytes(b'executable')
            installed = root/'main.json';installed.write_text('{}')
            model = root/local_visual_runtime.MODEL_FILE;model.write_bytes(b'model')
            projector = root/local_visual_runtime.PROJECTOR_FILE;projector.write_bytes(b'projector')
            visual = root/'visual-worker-manifest.json'
            visual.write_text(json.dumps(dict(schema='vision-visual-worker-assets-v1',
                modelRepository=local_visual_runtime.REPOSITORY, modelRevision=local_visual_runtime.REVISION,
                runtimeRevision=local_visual_runtime.RUNTIME_COMMIT, llamaServerPath=str(executable),
                languageModel=dict(path=str(model),bytes=5,sha256=MODEL_SHA),
                projector=dict(path=str(projector),bytes=9,sha256='wrong-projector'))))
            runtime = VisualRuntime(dict(modelPath=str(main),manifestPath=str(installed),runtimePath=str(executable),
                                         visualManifestPath=str(visual)),lambda:False,lambda phase:None)
            try:
                with patch('local_runtime.LlamaRuntime.verify', return_value=(executable,main)), \
                     patch.object(local_visual_runtime,'MODEL_BYTES',5), patch.object(local_visual_runtime,'PROJECTOR_BYTES',9), \
                     patch.object(local_visual_runtime,'digest_file',return_value=MODEL_SHA):
                    with self.assertRaisesRegex(LocalFailure, 'provenance'):
                        runtime.verify()
                self.assertEqual(projector.read_bytes(), b'projector')
            finally:
                runtime.http.close()

    def test_runtime_resource_reserve_stops_its_child(self):
        runtime = VisualRuntime({}, lambda:False, lambda phase:None)
        runtime.process = SimpleNamespace(pid=1234)
        process = SimpleNamespace(memory_info=lambda:SimpleNamespace(rss=100),
                                  cpu_times=lambda:SimpleNamespace(user=1.,system=.5))
        try:
            with patch('psutil.Process',return_value=process), \
                 patch('psutil.virtual_memory',return_value=SimpleNamespace(available=7*1024**3)), \
                 patch.object(runtime,'stop_child') as stop:
                runtime.monitor()
            stop.assert_called_once_with(runtime.process)
            self.assertIn('memory reserve', runtime.stop_reason)
            self.assertEqual(runtime.minimum_available_ram, 7*1024**3)
            self.assertEqual(runtime.cpu_seconds, 1.5)
        finally:
            runtime.http.close()

    def test_acceptance_requires_every_real_host_check_exact_identity_and_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'acceptance.json'
            settings = dict(acceptancePath=str(path), reserveRamGiB=10)
            report = dict(schema='qwen-local-acceptance-v1', approvedForDefault=True,
                          reviewer='Operator', reviewedAt='2026-10-12', identity={'fixture':'identity'},
                          checks={key:dict(passed=True,evidence='Fixture receipt, not real acceptance') for key in local_acceptance.CHECKS},
                          measurements=dict(peakResidentBytes=1024,minimumAvailableRamGiB=12,elapsedSeconds=60))
            with patch.object(local_acceptance, 'identity', return_value=report['identity']):
                path.write_text(json.dumps(report))
                self.assertTrue(local_acceptance.eligible(settings))
                for name in local_acceptance.CHECKS:
                    report['checks'][name]['passed'] = False
                    path.write_text(json.dumps(report))
                    self.assertFalse(local_acceptance.eligible(settings), name)
                    report['checks'][name]['passed'] = True
                report['measurements']['minimumAvailableRamGiB'] = 7
                path.write_text(json.dumps(report))
                self.assertFalse(local_acceptance.eligible(settings))


if __name__ == '__main__':
    unittest.main()
