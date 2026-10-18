"""Local title queue: ownership, cancellation, restart and bounded model input."""
import base64
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
import server
import test_uploaded_media
from project_naming import overview, eligible, clean_title
from local_visual_runtime import VisualRuntime

class ProjectNamingTests(unittest.TestCase):
    setUp = test_uploaded_media.UploadedMediaTests.setUp
    def tearDown(self):
        server.frame_analysis.runtime_factory=VisualRuntime
        self.tmp.cleanup()

    def save(self, **changes):
        project={'title':'Untitled timeline','settings':{'autoNameProject':True},'nodes':[{'id':'image1','kind':'image','title':'Church building','caption':'Exterior renovation'}]}
        project.update(changes)
        old=self.client.get(self.base,headers=self.headers).json
        self.assertEqual(self.client.put(self.base,json={'revision':old['revision'],'project':project},headers=self.headers).status_code,200)
        return project

    def submit(self):
        return self.client.post(self.base+'/auto-name',json={'requestId':'name-test'},headers=self.headers)

    def test_auth_opt_in_and_request_identity(self):
        self.assertEqual(self.client.post(self.base+'/auto-name',json={'requestId':'name-test'}).status_code,401)
        self.assertEqual(self.submit().status_code,409)
        self.save();self.assertEqual(self.submit().json['status'],'queued')
        self.assertEqual(self.submit().json['requestId'],'name-test')
        self.assertEqual(self.client.get(self.base+'/auto-name',headers={**self.headers,'X-Vision-Project-Key':'x'*48}).status_code,403)
        self.assertEqual(self.client.post(self.base+'/auto-name',json={'requestId':'second'},headers=self.headers).status_code,409)
        self.client.delete(self.base+'/auto-name?requestId=stale',headers=self.headers)
        self.assertEqual(self.submit().json['status'],'queued')
        self.client.delete(self.base+'/auto-name?requestId=name-test',headers=self.headers)
        self.assertEqual(server.frame_analysis.naming.row('project_video_test1')['status'],'cancelled')

    def test_restart_completion_and_manual_name_protection(self):
        self.save();self.submit();store=server.frame_analysis.naming
        class FakeRuntime:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def generate(self,messages,limit):
                self_test.assertIn('Exterior renovation',messages[-1]['content'])
                yield {'choices':[{'delta':{'content':'Church Exterior Renovation'},'finish_reason':'stop'}]}
        self_test=self
        server.frame_analysis.runtime_factory=FakeRuntime
        server.sessions.local.settings={'enabled':True,'visualEnabled':True}
        with server.connect_db() as db:db.execute("UPDATE project_naming SET status='processing'")
        store.initialize();self.assertEqual(store.row('project_video_test1')['status'],'queued')
        with patch('psutil.process_iter',return_value=[]),patch('psutil.virtual_memory',return_value=SimpleNamespace(available=32*1024**3)):
            self.assertTrue(store.work_once())
        self.assertEqual(store.row('project_video_test1')['title'],'Church Exterior Renovation')
        # The worker proposes a name; normal revision-checked client saving applies it.
        self.assertEqual(self.client.get(self.base,headers=self.headers).json['project']['title'],'Untitled timeline')
        with server.connect_db() as db:db.execute("UPDATE project_naming SET status='queued'")
        self.save(title='My own name');self.assertTrue(store.work_once())
        self.assertEqual(store.row('project_video_test1')['status'],'cancelled')

    def test_missing_worker_and_disabled_setting(self):
        self.save();self.submit();store=server.frame_analysis.naming
        server.sessions.local.settings={};self.assertTrue(store.work_once())
        self.assertEqual(store.row('project_video_test1')['status'],'paused')
        self.assertFalse(eligible({'title':'Untitled timeline','settings':{'autoNameProject':False}}))
        self.assertFalse(eligible({'title':'Untitled timeline','settings':{'autoNameProject':True,'autoNameManual':True}}))

    def test_overview_reads_pixels_and_text_with_bounds(self):
        out=io.BytesIO();Image.new('RGB',(1800,900),'red').save(out,'PNG')
        picture='data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()
        text,images=overview({'nodes':[{'kind':'image','title':f'Node {i}','src':picture,'attachments':[{'name':'Notes.txt','data':'data:text/plain;base64,'+base64.b64encode(b'Renovating the sanctuary').decode()}]} for i in range(12)]})
        self.assertIn('Node 11',text);self.assertIn('Renovating the sanctuary',text);self.assertEqual(len(images),6)
        self.assertTrue(all(url.startswith('data:image/jpeg;base64,') for _,url in images));self.assertLessEqual(len(text),7000)
        self.assertEqual(clean_title('"Sanctuary Renovation"'),'Sanctuary Renovation')
        for bad in ('','<think>test','One\nTwo'):
            with self.assertRaises(ValueError):clean_title(bad)

if __name__=='__main__':unittest.main()
