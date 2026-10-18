"""Upload once, disconnect, and finish downstream work without a browser."""
import io
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import server
from uploaded_media import atomic_json
import test_uploaded_media
import test_frame_analysis
from local_visual_runtime import IncompleteVisualInspection


class BackgroundUploadTests(unittest.TestCase):
    setUp = test_uploaded_media.UploadedMediaTests.setUp
    tearDown = test_uploaded_media.UploadedMediaTests.tearDown

    def upload(self, multipart=False):
        options = {'provider': 'local', 'includeSoundEvents': True, 'requesterUid': 'forged'}
        if multipart:
            response = self.client.post(self.base+'/media', data={'requestId':'background',
                'transcription':json.dumps(options), 'file':(io.BytesIO(b'video'), 'clip.mov')}, headers=self.headers)
        else:
            response = self.client.post(self.base+'/media/upload/background', json={'name':'clip.mov','size':5,
                'processing':{'describeFrames':True}, 'transcription':options}, headers=self.headers)
            self.assertEqual(response.status_code, 202, response.json)
            response = self.client.put(self.base+'/media/upload/background?offset=0', data=b'video',
                content_type='application/octet-stream', headers=self.headers)
        self.assertEqual(response.status_code, 202, response.json)
        return response.json['id']

    def process(self, value):
        directory = self.service.root/value['id']
        (directory/'audio-000.mp3').write_bytes(b'ID3prepared-audio')
        atomic_json(directory/'result.json', {'frames':[], 'audioSections':[{'start':0,'end':1}],
            'transcription':{'status':'waiting'}})

    def receipt(self, job):
        return json.loads((self.service.root/job/'result.json').read_text())['transcription']

    def test_both_upload_paths_handoff_without_browser_and_restore_receipt(self):
        for multipart in (False, True):
            if multipart:
                self.tmp.cleanup();self.setUp()
            job = self.upload(multipart)
            # Closing the browser means there are no further client requests.
            # Exercise the real durable transcription enqueue, with readiness mocked.
            with patch.object(self.service,'process',side_effect=self.process), \
                 patch.object(server.transcriptions,'capability',return_value={'ready':True}), \
                 patch.object(server.transcriptions,'sound_capability',return_value={'ready':True}):
                self.service.recover()
                self.assertTrue(self.service.work_once())
                receipt = self.receipt(job)
                self.assertEqual(receipt['status'], 'queued')
                self.assertEqual(receipt['provider'], 'local')
                self.assertTrue(receipt['includeSoundEvents'])
                row = server.transcriptions.row(receipt['id'])
                self.assertNotEqual(row['requester_uid'], 'forged')
                self.assertEqual(row['client_id'], 'media-'+job)
                # Simulate crash after enqueue but before persisting its receipt.
                result_path=self.service.root/job/'result.json'
                result=json.loads(result_path.read_text());result['transcription']={'status':'waiting'};atomic_json(result_path,result)
                self.service.recover();self.service.handoff_transcriptions()
                self.assertEqual(self.receipt(job)['id'], receipt['id'])
                with server.connect_db() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM local_transcriptions').fetchone()[0],1)
                server.transcriptions.stop.clear()
                with patch.object(server.transcriptions,'run_local',return_value={'sections':[{'start':0,'end':1,'text':'Completed while away.'}]}):
                    self.assertTrue(server.transcriptions.work_once())
                self.assertEqual(server.transcriptions.row(receipt['id'])['status'],'complete')
                # A returning client receives the same project-owned receipt.
                with self.client.get(self.base+'/media/'+job+'/result',headers=self.headers) as response:
                    self.assertEqual(response.json['transcription']['id'], receipt['id'])

    def test_full_queue_retries_on_server_after_restart(self):
        job=self.upload()
        with patch.object(self.service,'process',side_effect=self.process), \
             patch.object(server.transcriptions,'enqueue',side_effect=server.APIError('Queue full',429)):
            self.service.work_once()
        self.assertEqual(self.service.row(job)['status'],'complete')
        self.assertEqual(self.receipt(job)['status'],'waiting')
        self.assertTrue((self.service.root/job/'audio-000.mp3').exists())
        self.service.recover()
        with patch('uploaded_media.time.time',return_value=self.receipt(job)['retryAt']+1), \
             patch.object(server.transcriptions,'capability',return_value={'ready':True}), \
             patch.object(server.transcriptions,'sound_capability',return_value={'ready':True}):
            self.service.work_once()
        self.assertEqual(self.receipt(job)['status'],'queued')

    def test_restart_after_outputs_committed_never_reruns_video(self):
        job=self.upload()
        self.process(self.service.row(job))
        # A crash after saving outputs but before sealing keeps the preset.
        directory = self.service.root/job
        result = json.loads((directory/'result.json').read_text())
        result['frameCollection'] = job
        atomic_json(directory/'result.json', result)
        atomic_json(directory/'processing.json', {'describeFrames':True, 'screenshotDetail':'medium'})
        server.frame_analysis.create('project_video_test1', job)
        self.service.update(job,status='processing')
        (self.service.root/job/'source.input').unlink()
        self.service.recover()
        self.assertEqual(self.service.row(job)['status'],'complete')
        self.assertEqual(server.frame_analysis.row(job)['analysis_step'], 2)
        self.assertEqual(server.frame_analysis.row(job)['status'], 'queued')
        with patch.object(server.transcriptions,'capability',return_value={'ready':True}), \
             patch.object(server.transcriptions,'sound_capability',return_value={'ready':True}), \
             patch.object(self.service,'process',side_effect=AssertionError('must not rerun video')):
            self.service.work_once()
        self.assertEqual(self.receipt(job)['status'],'queued')

    def test_cannot_forge_gemini_authorization(self):
        response=self.client.post(self.base+'/media/upload/background',json={'name':'clip.mov','size':5,
            'transcription':{'provider':'gemini','authKind':'firebase-google','requesterUid':'forged'}},headers=self.headers)
        self.assertEqual(response.status_code,403)


class FrameBackgroundTests(unittest.TestCase):
    setUp=test_frame_analysis.FrameAnalysisTests.setUp
    tearDown=test_frame_analysis.FrameAnalysisTests.tearDown
    collection=test_frame_analysis.FrameAnalysisTests.collection

    def runtime(self, inspect):
        class Runtime:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
        Runtime.inspect=inspect
        server.frame_analysis.runtime_factory=Runtime
        server.sessions.local.settings={'enabled':True,'visualEnabled':True}
        for mock in [patch('psutil.process_iter',return_value=[]),patch('psutil.virtual_memory',return_value=SimpleNamespace(available=32*1024**3))]:
            mock.start();self.addCleanup(mock.stop)

    def due(self,cid):
        with server.connect_db() as db:db.execute('UPDATE frame_collections SET retry_at=0 WHERE id=?',(cid,))

    def test_incomplete_retries_without_client_and_never_caches_partial(self):
        store,cid=self.collection()
        def fail(*args):raise IncompleteVisualInspection('incomplete')
        self.runtime(fail)
        self.assertTrue(store.work_once());self.assertEqual(store.row(cid)['status'],'queued')
        self.assertEqual(store.row(cid)['completed'],0)
        self.assertFalse((store.root/cid/'0000.analysis.json').exists())
        self.assertFalse(store.work_once(),'retry backoff must be honored')
        store.initialize();self.due(cid)
        self.runtime(lambda *args:'A red surface.')
        self.assertTrue(store.work_once());self.assertEqual(store.row(cid)['status'],'complete')
        self.assertEqual(store.row(cid)['failures'],0)

    def test_repeated_incomplete_is_bounded_and_manual_pause_is_preserved(self):
        store,cid=self.collection()
        def fail(*args):raise IncompleteVisualInspection('incomplete')
        self.runtime(fail)
        for _ in range(3):self.due(cid);store.work_once()
        self.assertEqual(store.row(cid)['status'],'paused')
        store.initialize();self.assertEqual(store.row(cid)['status'],'paused')
        self.assertEqual(store.row(cid)['failures'],3)

    def test_shutdown_requeues_without_losing_checkpoint(self):
        store,cid=self.collection()
        self.runtime(lambda *args: (_ for _ in ()).throw(InterruptedError()))
        store.work_once();self.assertEqual(store.row(cid)['status'],'queued')

if __name__=='__main__':unittest.main()
