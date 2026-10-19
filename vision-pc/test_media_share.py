"""Offline cross-app boundaries, durable queue, revisions, and outbox retries."""
import io
from datetime import datetime
from unittest.mock import patch
import unittest
from PIL import Image
import server
from firebase_auth import InvalidIdentity
import test_title_eyes as title_tests

class MediaShareTests(unittest.TestCase):
    setUp=title_tests.TitleEyesTests.setUp
    tearDown=title_tests.TitleEyesTests.tearDown
    fake=title_tests.TitleEyesTests.fake
    work=title_tests.TitleEyesTests.work
    def request(self,method,path,**kw):
        with patch.object(server.media_share.identity,'verify',return_value={'sub':'uploader'}):
            return getattr(self.client,method)('/api/media-share/'+path,headers={'Authorization':'Bearer test','Origin':'https://firstupc.github.io'},**kw)
    def upload(self,rid='upload-test-1',prior=None):
        out=io.BytesIO();Image.new('RGB',(160,90),'navy').save(out,'PNG')
        data={'requestId':rid,'image':(io.BytesIO(out.getvalue()),'slide.png')}
        if prior:data['revision']=str(prior['revision'])
        return self.request('post','drops'+('/'+prior['id'] if prior else ''),data=data)
    def sync(self):
        with patch.object(server.media_share.publisher,'publish') as publish:
            server.media_share.sync_once();return publish
    def test_scoped_auth_and_cors(self):
        with patch.object(server.media_share.identity,'verify',side_effect=InvalidIdentity):
            self.assertEqual(self.client.get('/api/media-share/changes').status_code,401)
        result=self.request('get','changes?wait=0')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.headers['Access-Control-Allow-Origin'],'https://firstupc.github.io')
        self.assertEqual(self.client.get('/api/media-share/changes',headers={'Origin':'https://evil.example'}).status_code,403)
        # A media identity is never accepted by Vision's normal verifier.
        with patch.object(server.media_share.identity,'verify',return_value={'sub':'uploader'}):
            self.assertIn(self.client.get('/api/title-eyes/standby',headers={'Authorization':'Bearer test','Origin':'https://firstupc.github.io'}).status_code,(401,403))
    def test_browser_exit_and_restart_do_not_stop_title_or_publication(self):
        response=self.upload();self.assertEqual(response.status_code,202,response.json);row=response.json
        self.assertEqual(self.upload().json['id'],row['id'])
        self.assertEqual(self.request('get','requests/upload-test-1').json['id'],row['id'])
        self.store.initialize();server.media_share.initialize()
        self.fake('{"title":"THE COST OF A WEDDING","uncertain":false}');self.work();self.sync()
        got=self.request('get','drops/'+row['id']).json
        self.assertEqual(got['title'],'The Cost of a Wedding');self.assertTrue(got['published'])
        with server.connect_db() as db:self.assertIsNone(db.execute('SELECT image FROM media_share_drops').fetchone()[0])
    def test_title_lock_manual_revision_and_replacement(self):
        row=self.upload().json;url='drops/'+row['id']
        self.assertEqual(self.request('patch',url,json={'revision':row['revision'],'title':'Early'}).status_code,409)
        self.fake('{"title":"HELLO","uncertain":false}');self.work();self.sync();row=self.request('get',url).json
        edited=self.request('patch',url,json={'revision':row['revision'],'title':'When Methuselah is Gone'}).json
        self.assertEqual(edited['title'],'When Methuselah is Gone')
        self.assertEqual(self.request('patch',url,json={'revision':row['revision'],'title':'Stale'}).status_code,409)
        self.sync();self.assertEqual(self.request('get',url).json['title'],'When Methuselah is Gone')
        replaced=self.upload('upload-test-2',edited);self.assertEqual(replaced.status_code,202,replaced.json)
        self.assertNotEqual(replaced.json['imageKey'],edited['imageKey']);self.assertIsNone(replaced.json['title'])
        self.sync();self.assertIsNone(self.request('get',url).json['title'])
    def test_off_day_loads_on_demand_unloads_and_publish_retries(self):
        self.store.now=lambda:datetime(2026,10,19,12)
        self.assertFalse(self.work());self.assertIsNone(self.store.standby.runtime)
        row=self.upload().json;self.sync()
        self.fake('{"title":"WEDNESDAY","uncertain":false}');self.work()
        with patch.object(server.media_share.publisher,'publish',side_effect=OSError('offline')):server.media_share.sync_once()
        self.assertFalse(self.work());self.assertIsNone(self.store.standby.runtime)
        got=self.request('get','drops/'+row['id']).json
        self.assertEqual(got['title'],'Wednesday');self.assertFalse(got['published']);self.assertTrue(got['publishError'])
        with server.connect_db() as db:db.execute('UPDATE media_share_drops SET retry_at=0')
        self.sync();self.assertTrue(self.request('get','drops/'+row['id']).json['published'])
    def test_off_day_failed_model_stops_instead_of_repeating_forever(self):
        self.store.now=lambda:datetime(2026,10,19,12)
        row=self.upload().json
        self.fake('not valid title JSON');self.work();self.sync()
        self.assertEqual(self.request('get','drops/'+row['id']).json['titleStatus'],'error')
        self.assertFalse(self.work());self.assertIsNone(self.store.standby.runtime)

    def test_delete_cancels_pending_work_and_emits_change(self):
        before=self.request('get','changes?wait=0').json['sequence'];row=self.upload().json
        self.assertNotEqual(before,self.request('get','changes?wait=0').json['sequence'])
        result=self.request('delete','drops/'+row['id'],json={'revision':row['revision']})
        self.assertEqual(result.status_code,200);self.assertEqual(self.request('get','drops/'+row['id']).status_code,404)
        with server.connect_db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM title_eyes_jobs').fetchone()[0],0)
        publish=self.sync();self.assertTrue(publish.call_args.args[0]['deleted'])

if __name__=='__main__':unittest.main()
