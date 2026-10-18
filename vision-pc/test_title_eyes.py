"""Title OCR API boundaries and queued processing; no real model/network calls."""
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
import server
import test_uploaded_media
from title_eyes import prepare_image, parse_title, title_case, VisualRuntime

class TitleEyesTests(unittest.TestCase):
    def setUp(self):
        test_uploaded_media.UploadedMediaTests.setUp(self)
        self.store=server.title_eyes
        server.sessions.local.settings={'enabled':True,'visualEnabled':True}
    def tearDown(self):
        self.store.runtime_factory=VisualRuntime
        self.tmp.cleanup()
    def submit(self,rid='slide-test-1',headers=None,raw=None):
        if raw is None:
            out=io.BytesIO();Image.new('RGB',(160,90),'white').save(out,'PNG');raw=out.getvalue()
        return self.client.post('/api/title-eyes/jobs',data={'requestId':rid,'image':(io.BytesIO(raw),'slide.png')},headers=self.headers if headers is None else headers)
    def work(self):
        with patch('psutil.process_iter',return_value=[]),patch('psutil.virtual_memory',return_value=SimpleNamespace(available=32*1024**3)):
            return self.store.work_once()
    def fake(self,answer):
        class Runtime:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def inspect(self,image,prompt):
                self_test.assertTrue(image.startswith('data:image/jpeg;base64,'))
                self_test.assertIn('Exclude speaker/preacher names',prompt)
                return answer
        self_test=self;self.store.runtime_factory=Runtime
    def test_title_case_and_strict_model_output(self):
        for original,expected in [
            ('THE COST OF A WEDDING','The Cost of a Wedding'),
            ('HERE COMES ESAU','Here Comes Esau'),
            ('THE GOD IN THE MIDDLE','The God in the Middle'),
            ('FAITH: THE WAY THROUGH','Faith: The Way Through'),
            ('WHAT ARE WE WAITING FOR?','What Are We Waiting For?'),
            ('WALKING WITH GOD','Walking with God'),
            ('DNA AND FAITH','DNA and Faith'),
            ('The Power of iPhone','The Power of iPhone'),
            ('THE STATE-OF-THE-ART CHURCH','The State-of-the-Art Church'),
            ("DON’T GIVE UP","Don’t Give Up")]:
            self.assertEqual(title_case(original),expected)
        self.assertEqual(parse_title('{"title":"THE COST\\nOF A WEDDING","uncertain":false}')['title'],'The Cost of a Wedding')
        self.assertIsNone(parse_title('{"title":null,"uncertain":true}')['title'])
        for bad in ('The title is Hello','{}','[]','{"title":"hi","uncertain":"false"}','{"title":"<script>x</script>","uncertain":false}'):
            with self.assertRaises(ValueError):parse_title(bad)
    def test_auth_owner_bounds_and_idempotency(self):
        self.assertEqual(self.submit(headers={}).status_code,401)
        self.assertEqual(self.submit(raw=b'not an image').status_code,400)
        self.assertEqual(self.submit(rid='bad').status_code,400)
        job=self.submit();self.assertEqual(job.status_code,202,job.json)
        self.assertEqual(self.submit().json['id'],job.json['id'])
        self.submit('slide-test-2');self.submit('slide-test-3')
        self.assertEqual(self.submit('slide-test-4').status_code,429)
        path='/api/title-eyes/jobs/'+job.json['id']
        self.assertEqual(self.client.get(path).status_code,401)
        with server.connect_db() as db:db.execute('UPDATE title_eyes_jobs SET uid=? WHERE id=?',('another-account',job.json['id']))
        self.assertEqual(self.client.get(path,headers=self.headers).status_code,404)
        self.assertEqual(self.client.delete(path,headers=self.headers).status_code,404)
    def test_complete_restart_cleanup_and_delete(self):
        job=self.submit().json['id'];path='/api/title-eyes/jobs/'+job
        with server.connect_db() as db:db.execute("UPDATE title_eyes_jobs SET status='processing' WHERE id=?",(job,))
        self.store.initialize();self.assertEqual(self.store.row(job)['status'],'queued')
        self.fake('{"title":"THE COST OF A WEDDING","uncertain":false}')
        self.assertTrue(self.work())
        result=self.client.get(path,headers=self.headers).json
        self.assertEqual(result['title'],'The Cost of a Wedding');self.assertEqual(result['status'],'complete')
        self.assertNotIn('image',result);self.assertIsNone(self.store.row(job)['image'])
        self.assertTrue(self.client.delete(path,headers=self.headers).json['deleted']);self.assertIsNone(self.store.row(job))
    def test_no_title_and_ambiguous_are_not_invented(self):
        for index,uncertain in enumerate((False,True)):
            job=self.submit('ambiguous-'+str(index)).json['id']
            self.fake(json.dumps({'title':None,'uncertain':uncertain}));self.work()
            value=self.store.public(self.store.row(job))
            self.assertEqual(value['status'],'complete');self.assertIsNone(value['title']);self.assertEqual(value['uncertain'],uncertain)
    def test_malformed_output_fails_and_cancellation_does_not_resurrect(self):
        job=self.submit().json['id'];self.fake('This is a picture of a title.');self.work()
        self.assertEqual(self.store.row(job)['status'],'error');self.assertIsNone(self.store.row(job)['image'])
        job=self.submit('cancel-test').json['id'];store=self.store
        class Runtime:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def inspect(self,*args):
                with store.db() as db:db.execute('DELETE FROM title_eyes_jobs WHERE id=?',(job,))
                return '{"title":"Hello","uncertain":false}'
        store.runtime_factory=Runtime;self.work();self.assertIsNone(store.row(job))
    def test_shared_model_lock_and_obs_defer(self):
        from owned_assets import exclusive_file
        job=self.submit().json['id']
        with exclusive_file(server.app.config['DATA_DIR']/'local-inference.lock'):
            self.assertFalse(self.work());self.assertEqual(self.store.row(job)['status'],'queued')
        with patch('psutil.process_iter',return_value=[SimpleNamespace(info={'name':'obs64.exe'})]),patch('psutil.virtual_memory',return_value=SimpleNamespace(available=32*1024**3)):
            self.assertFalse(self.store.work_once());self.assertIn('OBS',self.store.row(job)['phase'])
        self.client.delete('/api/title-eyes/jobs/'+job,headers=self.headers)
        self.assertFalse(self.work())
    def test_image_sanitizing_and_expiry(self):
        out=io.BytesIO();Image.new('RGBA',(2000,1000),(20,30,40,0)).save(out,'PNG')
        clean=Image.open(io.BytesIO(prepare_image(out.getvalue())))
        self.assertEqual(clean.size,(1600,800));self.assertEqual(clean.format,'JPEG');self.assertEqual(clean.getpixel((0,0)),(255,255,255))
        job=self.submit().json['id']
        with server.connect_db() as db:db.execute('UPDATE title_eyes_jobs SET expires=0 WHERE id=?',(job,))
        self.store.sweep();self.assertIsNone(self.store.row(job))
    def test_missing_model_is_explicit(self):
        server.sessions.local.settings={}
        self.assertEqual(self.submit().status_code,503)

if __name__=='__main__':unittest.main()
