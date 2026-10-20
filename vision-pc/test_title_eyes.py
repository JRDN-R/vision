"""Title OCR API boundaries and queued processing; no real model/network calls."""
from datetime import datetime
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
        self.store.now=lambda: datetime(2026,10,18,12)
        server.sessions.local.settings={'enabled':True,'visualEnabled':True}
    def tearDown(self):
        self.store.standby.close()
        self.store.now=datetime.now
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
        self.store.standby.close()
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
    def test_visible_text_gate_rejects_artwork_captions_without_losing_stylized_text(self):
        from title_eyes import PROMPT
        self.assertIn('Set hasVisibleText=false ONLY',PROMPT)
        self.assertIn('unfamiliar-font',PROMPT)
        self.assertIn('non-Latin scripts',PROMPT)
        self.assertIn('do not call a wordless polygonal image "Low Poly"',PROMPT)

        # Even if the tiny visual model supplied a style caption, its explicit
        # zero-glyph observation must result in an empty, certain title.
        wordless=parse_title('{"hasVisibleText":false,"title":"Low Poly","uncertain":true}')
        self.assertEqual(wordless,{'title':None,'rawTitle':None,'uncertain':False})
        self.assertEqual(parse_title('{"hasVisibleText":false,"title":null,"uncertain":false}'),wordless)

        # Text can be visible without a headline, or visible but unreadable.
        self.assertIsNone(parse_title('{"hasVisibleText":true,"title":"","uncertain":false}')['title'])
        unclear=parse_title('{"hasVisibleText":true,"title":null,"uncertain":true}')
        self.assertEqual(unclear['uncertain'],True)

        # Typography and script must not be categorically filtered out.
        for lettering in ('HERE COMES ESAU','When Methuselah Is Gone','ΕΛΠΙΔΑ','እምነት'):
            result=parse_title(json.dumps({'hasVisibleText':True,'title':lettering,'uncertain':False}))
            self.assertEqual(result['rawTitle'],lettering)
            self.assertIsNotNone(result['title'])

        # Bad detection flags fail validation rather than silently suppressing text.
        with self.assertRaises(ValueError):
            parse_title('{"hasVisibleText":"false","title":"REAL TITLE","uncertain":false}')

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
    def test_shared_model_lock_and_live_obs_title_mode(self):
        from owned_assets import exclusive_file
        job=self.submit().json['id']
        with exclusive_file(server.app.config['DATA_DIR']/'local-inference.lock'):
            self.assertFalse(self.work());self.assertEqual(self.store.row(job)['status'],'queued')
        with patch('psutil.process_iter',return_value=[SimpleNamespace(info={'name':'obs64.exe'})]),patch('psutil.virtual_memory',return_value=SimpleNamespace(available=32*1024**3)):
            self.fake('{"title":"LIVE TITLE","uncertain":false}')
            self.assertTrue(self.store.work_once());self.assertEqual(self.store.row(job)['status'],'complete')
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


class StandbyScheduleTests(unittest.TestCase):
    setUp=TitleEyesTests.setUp
    tearDown=TitleEyesTests.tearDown
    submit=TitleEyesTests.submit
    work=TitleEyesTests.work
    def test_temporary_window_is_only_october_19_and_20_2026(self):
        from title_eyes_standby import scheduled, temporary_warmup
        for now,expected in [(datetime(2026,10,18,23,59,59),False),
                             (datetime(2026,10,19),True),
                             (datetime(2026,10,20,23,59,59),True),
                             (datetime(2026,10,21),False),
                             (datetime(2026,10,26),False),
                             (datetime(2027,10,19),False)]:
            self.assertEqual(temporary_warmup(now),expected)
        self.assertTrue(scheduled(datetime(2026,10,21,23,59,59)))
        self.assertFalse(scheduled(datetime(2026,10,22)))

    def test_temporary_preload_continues_through_wednesday_without_reload(self):
        events=self.counting_runtime()
        for day in (19,20,21):
            self.store.now=lambda day=day:datetime(2026,10,day,0)
            self.assertFalse(self.work())
            value=self.client.get('/api/title-eyes/standby',headers=self.headers).json
            self.assertTrue(value['scheduled']);self.assertTrue(value['loaded'])
            self.assertEqual(value['phase'],'Ready')
            self.assertEqual(value['temporaryWarmup'],day<21)
            self.assertEqual(value['temporaryWarmupEndsLocal'],'2026-10-21T00:00:00' if day<21 else None)
            self.assertEqual(events.count('load'),1)
            self.assertNotIn('stop',events)
        self.store.now=lambda:datetime(2026,10,22)
        self.assertFalse(self.work());self.assertEqual(events.count('stop'),1)
        self.store.now=lambda:datetime(2026,10,26)
        self.assertFalse(self.work());self.assertEqual(events.count('load'),1)

    def test_exact_weekdays_and_midnight(self):
        from title_eyes_standby import scheduled, next_start
        from datetime import timedelta
        monday=datetime(2026,10,26)
        self.assertEqual([scheduled(monday+timedelta(days=n)) for n in range(7)], [False,False,True,False,False,False,True])
        self.assertTrue(scheduled(datetime(2026,10,25,23,59,59)))
        self.assertFalse(scheduled(datetime(2026,10,26,0,0,0)))
        self.assertEqual(next_start(monday),'2026-10-28T00:00:00')
        self.assertEqual(next_start(datetime(2026,10,21,23,59)),'2026-10-25T00:00:00')

    def test_off_days_accept_titles_but_do_not_preload_without_work(self):
        self.store.now=lambda:datetime(2026,10,26,12)
        events=self.counting_runtime()
        self.assertFalse(self.work())
        self.assertEqual(events,[])
        self.assertEqual(self.client.get('/api/title-eyes/standby').status_code,401)
        value=self.client.get('/api/title-eyes/standby',headers=self.headers).json
        self.assertFalse(value['scheduled']);self.assertFalse(value['loaded']);self.assertEqual(value['residentBytes'],0)
        self.assertIn('on demand',value['phase'].lower())
        first=self.submit('offday-one')
        second=self.submit('offday-two')
        self.assertEqual(first.status_code,202,first.json)
        self.assertEqual(second.status_code,202,second.json)
        self.assertTrue(self.work())
        self.assertEqual(self.store.row(first.json['id'])['status'],'complete')
        self.assertEqual(events.count('load'),1)
        self.assertEqual(events.count('stop'),0)  # Another slide is waiting.
        self.assertTrue(self.work())
        self.assertEqual(self.store.row(second.json['id'])['status'],'complete')
        self.assertEqual(events.count('load'),1)  # Reused until queue drained.
        self.assertEqual(events.count('stop'),1)
        self.assertFalse(self.work())
        self.assertEqual(events.count('load'),1)

    def counting_runtime(self):
        events=[]
        class Runtime:
            resident_bytes=950_000_000
            def __init__(self,*args):events.append('create')
            def __enter__(self):events.append('load');return self
            def __exit__(self,*args):events.append('stop')
            def retain(self):events.append('retain')
            def reusable(self):return True
            def begin_request(self,*args):events.append('begin')
            def finish_request(self):events.append('idle')
            def inspect(self,*args):events.append('inspect');return '{"title":"THE COST OF A WEDDING","uncertain":false}'
        self.store.runtime_factory=Runtime
        return events

    def test_proactive_warmup_reuses_one_process_for_multiple_slides(self):
        events=self.counting_runtime()
        self.assertFalse(self.work()) # No slide: starts on schedule anyway.
        self.submit('warm-test-1');self.work()
        self.submit('warm-test-2');self.work()
        self.assertEqual(events.count('load'),1);self.assertEqual(events.count('retain'),1)
        self.assertEqual(events.count('inspect'),2);self.assertNotIn('stop',events)
        value=self.client.get('/api/title-eyes/standby',headers=self.headers).json
        self.assertTrue(value['loaded']);self.assertEqual(value['residentBytes'],950_000_000)
        self.assertEqual(value['memoryLimitBytes'],4*1024**3)
        with self.assertRaises(BlockingIOError):
            from owned_assets import exclusive_file
            with exclusive_file(server.app.config['DATA_DIR']/'local-inference.lock'):pass

    def test_idle_standby_unloads_at_midnight_then_cold_starts_new_work(self):
        self.store.now=lambda:datetime(2026,10,25,23,59)
        events=self.counting_runtime();self.work()
        self.assertEqual(events.count('load'),1)
        self.store.now=lambda:datetime(2026,10,26,0)
        self.assertFalse(self.work())
        self.assertEqual(events.count('stop'),1)
        from owned_assets import exclusive_file
        with exclusive_file(server.app.config['DATA_DIR']/'local-inference.lock'):pass
        job=self.submit().json['id']
        self.assertTrue(self.work())
        self.assertEqual(self.store.row(job)['status'],'complete')
        self.assertEqual(events.count('load'),2)
        self.assertEqual(events.count('stop'),2)
        self.store.now=lambda:datetime(2026,10,28,0)
        self.assertFalse(self.work())
        self.assertEqual(events.count('load'),3)

    def test_obs_preloads_off_schedule_and_reuses_without_inference(self):
        self.store.now=lambda:datetime(2026,10,26,12)
        events=self.counting_runtime()
        with patch('title_eyes_standby.obs_active',return_value=True):
            for _ in range(4):self.assertFalse(self.work())
            value=self.client.get('/api/title-eyes/standby',headers=self.headers).json
            self.assertTrue(value['obsActive']);self.assertTrue(value['loaded'])
            self.assertEqual(value['timezone'],'America/New_York')
            self.assertEqual(value['warmReason'],'OBS is open')
            self.assertFalse(value['scheduled'])
            self.assertEqual(events.count('load'),1);self.assertNotIn('inspect',events)
            self.submit('obs-title-one');self.assertTrue(self.work())
            self.assertEqual(events.count('load'),1);self.assertEqual(events.count('inspect'),1)
            self.assertNotIn('stop',events)
            # A pending background job does not repeatedly evict/reload it.
            with self.store.db() as db:
                db.execute("UPDATE title_eyes_jobs SET status='complete'")
                db.execute("INSERT INTO project_naming(project_id,request_id,status,created_at) VALUES('obs-pending','obs-pending','queued',0)")
            self.assertFalse(self.store.standby.competing_work())
            self.work();self.assertEqual(events.count('load'),1)
        with patch('title_eyes_standby.obs_active',return_value=False):
            self.work();self.assertEqual(events.count('stop'),1)

    def test_obs_survives_scheduled_boundary_and_server_restart(self):
        events=self.counting_runtime()
        with patch('title_eyes_standby.obs_active',return_value=True):
            self.store.now=lambda:datetime(2026,10,25,23,59)
            self.work()
            self.store.now=lambda:datetime(2026,10,26,0)
            self.work();self.assertEqual(events.count('load'),1)
            self.store.initialize();self.work()
            self.assertEqual(events.count('stop'),1);self.assertEqual(events.count('load'),2)
            self.assertEqual(self.store.standby.phase,'Ready')

    def test_obs_does_not_override_memory_safety_and_recovers(self):
        events=self.counting_runtime()
        self.store.now=lambda:datetime(2026,10,26,12)
        with patch('title_eyes_standby.obs_active',return_value=True):
            with patch('title_eyes_standby.resource_pause',return_value='Waiting for available memory'):
                self.work();self.assertEqual(events,[])
            self.work();self.assertEqual(events.count('load'),1)

    def test_spawned_process_is_not_reported_ready_before_loading_finishes(self):
        observations=[]
        standby=self.store.standby
        class LoadingRuntime:
            process=SimpleNamespace(poll=lambda:None)
            def __init__(self,settings,cancelled,stage):self.stage=stage
            def __enter__(self):
                self.stage('Loading title model')
                observations.append(standby.status()['loaded'])
                return self
            def __exit__(self,*args):pass
        self.store.runtime_factory=LoadingRuntime
        with patch('title_eyes_standby.obs_active',return_value=True):
            self.work()
            self.assertEqual(observations,[False])
            self.assertTrue(standby.status()['loaded'])

    def test_queued_request_is_not_cancelled_at_warm_day_boundary(self):
        self.store.now=lambda:datetime(2026,10,25,23,59)
        events=self.counting_runtime();self.work()
        job=self.submit().json['id']
        self.store.now=lambda:datetime(2026,10,26,0)
        self.assertTrue(self.work())
        self.assertEqual(self.store.row(job)['status'],'complete')
        self.assertIsNone(self.store.row(job)['image'])
        self.assertEqual(events.count('load'),1)
        self.assertEqual(events.count('stop'),1)

    def test_obs_memory_and_competing_work_release_then_reload(self):
        events=self.counting_runtime();self.work()
        with patch('title_eyes_standby.resource_pause',return_value='Waiting for OBS to close'):
            self.work();self.assertIsNone(self.store.standby.runtime)
        self.work();self.assertEqual(events.count('load'),2)
        with patch.object(self.store.standby,'competing_work',return_value=True):
            self.work();self.assertIsNone(self.store.standby.runtime)
        self.work();self.assertEqual(events.count('load'),3)
        with patch('title_eyes_standby.resource_pause',return_value='Waiting for available memory'):
            self.work();self.assertIsNone(self.store.standby.runtime)

    def test_failure_during_preload_releases_lock_and_backs_off(self):
        class Broken:
            def __init__(self,*args):pass
            def __enter__(self):raise ValueError('Invalid installation')
            def __exit__(self,*args):pass
        self.store.runtime_factory=Broken
        self.work();self.assertIsNone(self.store.standby.runtime)
        from owned_assets import exclusive_file
        with exclusive_file(server.app.config['DATA_DIR']/'local-inference.lock'):pass
        self.assertGreater(self.store.standby.retry_at,0)
        with patch.object(self.store,'runtime_factory',side_effect=AssertionError('Retry backoff')):self.work()

    def test_changed_model_files_cause_fresh_load(self):
        events=self.counting_runtime();self.work()
        with patch.object(self.store.standby.runtime,'reusable',return_value=False):self.work()
        self.assertEqual(events.count('stop'),1);self.assertEqual(events.count('load'),2)

    def test_idle_runtime_has_no_per_slide_ten_minute_lifetime(self):
        from title_eyes_standby import StandbyRuntime
        runtime=StandbyRuntime({},lambda:False,lambda _:None)
        runtime.finish_request();self.assertIsNone(runtime.deadline)
        import time
        runtime.begin_request(lambda:False,lambda _:None)
        self.assertGreater(runtime.deadline,time.monotonic()+590)
        runtime.finish_request();self.assertIsNone(runtime.deadline)
        runtime.http.close()


    def test_real_file_fingerprint_rejects_changes_and_runtime_inventory_additions(self):
        from title_eyes_standby import StandbyRuntime
        path=self.root/'model.gguf';path.write_bytes(b'original')
        root=self.root/'runtime';root.mkdir();binary=root/'llama-server.exe';binary.write_bytes(b'runtime')
        runtime=StandbyRuntime({},lambda:False,lambda _:None)
        runtime._watched=[str(path),str(binary)]
        runtime._runtime_root=root;runtime._inventory={'llama-server.exe'}
        runtime.process=SimpleNamespace(poll=lambda:None)
        runtime._fingerprint=runtime.fingerprint()
        self.assertTrue(runtime.reusable())
        path.write_bytes(b'changed!');self.assertFalse(runtime.reusable())
        runtime._fingerprint=runtime.fingerprint();self.assertTrue(runtime.reusable())
        (root/'extra.dll').write_bytes(b'extra');self.assertFalse(runtime.reusable())
        runtime.http.close()

    def test_memory_limit_stops_the_resident_child(self):
        from title_eyes_standby import StandbyRuntime
        runtime=StandbyRuntime({},lambda:False,lambda _:None)
        runtime.process=SimpleNamespace(pid=123)
        runtime.done=SimpleNamespace(wait=lambda _:False)
        fake=SimpleNamespace(memory_info=lambda:SimpleNamespace(rss=5*1024**3))
        with patch('psutil.Process',return_value=fake),patch('psutil.virtual_memory',return_value=SimpleNamespace(available=30*1024**3)),patch.object(runtime,'stop_child') as stop:
            runtime.monitor()
            stop.assert_called_once_with(runtime.process)
        self.assertIn('4 GiB',runtime.stop_reason)
        runtime.http.close()

if __name__=='__main__':unittest.main()
