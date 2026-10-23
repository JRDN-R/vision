"""Offline frame preset, ownership, checkpoint and real FFmpeg checks."""
import io,json,math,shutil,subprocess,unittest
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
import server
import test_uploaded_media
from frame_analysis import PRESETS,frame_interval,image_metadata,VisualRuntime

class FrameAnalysisTests(unittest.TestCase):
    setUp=test_uploaded_media.UploadedMediaTests.setUp
    def tearDown(self):
        server.frame_analysis.runtime_factory=VisualRuntime
        self.tmp.cleanup()

    def collection(self,count=3):
        store=server.frame_analysis;cid='a'*24
        store.create('project_video_test1',cid)
        picture=Image.new('RGB',(64,36),'red');out=io.BytesIO();picture.save(out,'JPEG')
        for index in range(count):store.add(cid,index,index*.25,out.getvalue(),image_metadata(picture))
        store.seal(cid,True)
        return store,cid

    def test_presets_duration_caps(self):
        self.assertEqual([math.ceil(300/frame_interval(300,p)) for p in PRESETS],[1200,300,100,30])
        for duration in (.1,5,300,600,7200):
            for preset,(_,limit) in PRESETS.items():self.assertLessEqual(math.ceil(duration/frame_interval(duration,preset)),limit)

    def test_ownership_pause_and_delete(self):
        store,cid=self.collection();path=self.base+'/frames/'+cid
        self.assertEqual(self.client.get(path).status_code,401)
        self.assertEqual(self.client.get(path,headers={**self.headers,'X-Vision-Project-Key':'x'*48}).status_code,403)
        other='/api/projects/project_video_test2';self.client.put(other,json={'project':{},'revision':0},headers=self.headers)
        self.assertEqual(self.client.get(other+'/frames/'+cid,headers=self.headers).status_code,404)
        with self.client.get(path+'/0',headers=self.headers) as response:self.assertEqual(response.mimetype,'image/jpeg')
        self.assertEqual(self.client.get(path+'/1200',headers=self.headers).status_code,404)
        self.assertEqual(self.client.post(path,json={'action':'pause'},headers=self.headers).json['status'],'paused')
        self.assertEqual(self.client.post(path,json={'action':'resume'},headers=self.headers).json['status'],'queued')
        self.assertTrue(self.client.delete(path,headers=self.headers).json['deleted'])
        self.assertFalse((store.root/cid).exists())

    def test_checkpoint_duplicate_provenance_and_restart(self):
        store,cid=self.collection(18);calls=[]
        class FakeRuntime:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def inspect(self,image,question):calls.append(question);return 'A red surface fills the frame.'
        store.runtime_factory=FakeRuntime;server.sessions.local.settings={'enabled':True,'visualEnabled':True}
        with patch('psutil.process_iter',return_value=[]),patch('psutil.virtual_memory',return_value=SimpleNamespace(available=32*1024**3)):
            self.assertTrue(store.work_once());self.assertEqual(store.row(cid)['completed'],16)
            store.initialize();self.assertTrue(store.work_once())
        report=store.report(store.row(cid))
        self.assertEqual(report['status'],'complete');self.assertEqual(len(calls),1)
        self.assertEqual(report['frames'][1]['reusedFrom'],0);self.assertFalse(report['frames'][1]['independentlyInspected'])
        self.assertEqual(report['frames'][0]['aspectRatio'],'16:9');self.assertEqual(len(report['timeline']),1)
        self.assertEqual(report['timeline'][0]['endFrame'],17)

    def test_every_other_snapshot_is_explicitly_unanalyzed(self):
        store,cid=self.collection(19)
        with server.connect_db() as db:db.execute("UPDATE frame_collections SET analysis_step=2 WHERE id=?",(cid,))
        calls=[]
        class FakeRuntime:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def inspect(self,image,question):calls.append(question);return 'A red surface.'
        store.runtime_factory=FakeRuntime;server.sessions.local.settings={'enabled':True,'visualEnabled':True}
        with patch('psutil.process_iter',return_value=[]),patch('psutil.virtual_memory',return_value=SimpleNamespace(available=32*1024**3)):
            store.work_once();store.initialize();store.work_once()
        report=store.report(store.row(cid))
        self.assertEqual(report['status'],'complete');self.assertEqual(report['completed'],19)
        self.assertEqual(report['analyzed'],10);self.assertEqual(report['planned'],10)
        self.assertEqual(len(calls),1)
        self.assertNotIn('Previous observation',calls[0])
        for frame in report['frames'][1::2]:
            self.assertEqual(frame['analysisStatus'],'skipped');self.assertIsNone(frame['description']);self.assertNotIn('reusedFrom',frame)
        self.assertEqual(report['frames'][18]['reusedFrom'],0)

    def test_title_transcript_and_context_reach_visual_worker(self):
        store,cid=self.collection(2);calls=[]
        class FakeRuntime:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def inspect(self,image,question):calls.append(question);return 'A hinge is visible.'
        store.runtime_factory=FakeRuntime;server.sessions.local.settings={'enabled':True,'visualEnabled':True}
        source=('Samsung Dryer Door Reversal.mp4',[(0.0,'Use a Phillips screwdriver on the hinge.')])
        with patch.object(store,'caption_material',return_value=source),patch('psutil.process_iter',return_value=[]),patch('psutil.virtual_memory',return_value=SimpleNamespace(available=32*1024**3)):
            self.assertTrue(store.work_once())
        self.assertEqual(len(calls),1)
        self.assertIn('Samsung Dryer Door Reversal',calls[0])
        self.assertIn('Phillips screwdriver',calls[0])
        observed=store.report(store.row(cid))['frames'][0]
        self.assertEqual(observed['context']['videoTitle'],'Samsung Dryer Door Reversal')
        self.assertEqual(observed['context']['version'],'bounded-transcript-v1')

    def test_pending_transcript_leaves_collection_queued(self):
        store,cid=self.collection()
        server.sessions.local.settings={'enabled':True,'visualEnabled':True}
        with patch.object(store,'caption_material',return_value=None):
            self.assertFalse(store.work_once())
        row=store.row(cid)
        self.assertEqual(row['status'],'queued')
        self.assertEqual(row['completed'],0)
        self.assertGreater(row['retry_at'],0)

    def test_missing_model_preserves_frames(self):
        store,cid=self.collection();server.sessions.local.settings={}
        self.assertTrue(store.work_once());self.assertEqual(store.row(cid)['status'],'paused')
        self.assertTrue((store.root/cid/'0000.jpg').exists())

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_maximum_real_video_manifest(self):
        server.app.config['FFMPEG']=shutil.which('ffmpeg');source=self.root/'sample.mp4'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc2=size=800x450:rate=24','-t','2','-c:v','libx264',str(source)],check=True)
        data=source.read_bytes()
        response=self.client.post(self.base+'/media/upload/maximum-test',json={'name':'sample.mp4','size':len(data),'processing':{'screenshotDetail':'maximum','describeFrames':True}},headers=self.headers)
        self.assertEqual(response.status_code,202)
        response=self.client.put(self.base+'/media/upload/maximum-test?offset=0',data=data,content_type='application/octet-stream',headers=self.headers)
        job=response.json['id'];self.assertTrue(self.service.work_once())
        status=self.client.get(self.base+'/media/'+job,headers=self.headers).json
        self.assertEqual(status['status'],'complete',status)
        with self.client.get(self.base+'/media/'+job+'/result',headers=self.headers) as response:result=response.json
        self.assertEqual(len(result['frames']),8);self.assertEqual(result['snapshotInterval'],.25)
        self.assertLess(len(json.dumps(result)),60000);self.assertNotIn('data',result['frames'][0])
        self.assertEqual(result['frames'][1]['timestamp'],.25);self.assertEqual(server.frame_analysis.row(job)['status'],'queued')
        with self.client.get(self.base+'/frames/'+job+'/7',headers=self.headers) as response:self.assertEqual(response.status_code,200)
        self.assertFalse((self.service.root/job/'source.input').exists())
        self.assertEqual(server.frame_analysis.row(job)['analysis_step'],2)
        metadata=json.loads((server.frame_analysis.root/job/'0000.json').read_text())
        self.assertEqual(metadata['width'],800)  # Not reduced to the preview's 640 pixels.

if __name__=='__main__':unittest.main()
