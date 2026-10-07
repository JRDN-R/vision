"""Offline Venture acceptance, isolation, recovery, preparation and funding checks."""
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock
import zipfile

import server
import sessions
import test_firebase_auth
from test_sessions import FakeResponse
from venture import disk_name, local_title, local_graphic
from venture_billing import amount_micro, estimate, key_id
from venture_files import check_zip


class VentureTests(unittest.TestCase):
    setUpClass = classmethod(test_firebase_auth.FirebaseTests.setUpClass.__func__)
    token = test_firebase_auth.FirebaseTests.token
    headers = test_firebase_auth.FirebaseTests.headers

    def setUp(self):
        test_firebase_auth.FirebaseTests.setUp(self)
        self.service = server.sessions
        self.venture = self.service.venture
        self.vault = patch.multiple(sessions, protect_secret=lambda x: x.encode(), unprotect_secret=lambda x: bytes(x).decode())
        self.vault.start()
        self.a['X-OpenAI-Key'] = 'sk-test-venture'
        self.cid = 'venture_test_00000001'
        self.path = '/api/venture/conversations/' + self.cid
        result = self.client.post('/api/venture/conversations', headers=self.a, json={'id': self.cid})
        self.assertEqual(result.status_code, 201, result.json)
        with server.connect_db() as db:
            db.execute('INSERT INTO account_credentials VALUES(?,?,?)', ('firebase:alice', b'sk-test-venture', time.time()))

    def tearDown(self):
        self.vault.stop()
        test_firebase_auth.FirebaseTests.tearDown(self)

    def submit(self, request_id='request-venture-0001', previous=None, retry=None, files=False):
        opts = dict(clientRequestId=request_id, message='Create a cowling gap inspection report', model='gpt-6-astra', runOptions={'codeInterpreter': True})
        if previous: opts['previousRunId'] = previous
        if retry: opts['retryOf'] = retry
        data = {'options': json.dumps(opts)}
        if files: data['attachments'] = (io.BytesIO(b'Wing facts and recorded dimensions.'), 'facts.txt')
        return self.client.post('/api/projects/'+self.cid+'/runs', headers=self.a, data=data)

    def complete(self, rid, response_id='resp_venture_1', **kwargs):
        response = dict(id=response_id, status='completed', model='gpt-6-astra', service_tier='default',
                        usage=dict(input_tokens=1000, output_tokens=100, input_tokens_details={'cached_tokens': 200, 'cache_write_tokens': 100}, output_tokens_details={'reasoning_tokens': 50}), output=[])
        response.update(kwargs)
        self.service.update(rid, status='completed', response_id=response_id, text='Here is the cowling gap inspection report.', response_json=json.dumps(response))
        return response

    def funding(self, kind='set', amount='25', request_id='calibrate-request-0001', revision=0):
        return self.client.post('/api/venture/funding', headers=self.a, json=dict(kind=kind, amount=amount, requestId=request_id, revision=revision))

    def test_account_history_and_board_separation(self):
        self.assertEqual(self.client.get('/api/projects', headers=self.a).json['projects'], [])
        self.assertEqual(self.client.get('/api/venture/conversations', headers=self.a).json['conversations'], [])
        self.assertEqual(self.client.get('/api/venture/conversations', headers=self.b).json['conversations'], [])
        for method in ('get', 'patch'):
            result = getattr(self.client, method)(self.path, headers=self.b, **({'json': {'title': 'stolen', 'revision': 1}} if method=='patch' else {}))
            self.assertEqual(result.status_code, 404)
        duplicate = self.client.post('/api/venture/conversations', headers=self.a, json={'id': self.cid})
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(self.client.post('/api/venture/conversations', headers=self.b, json={'id': self.cid}).status_code, 404)
        self.assertTrue((self.venture.root(self.cid)/'conversation.json').is_file())

    def test_rename_revision_and_local_title_preservation(self):
        renamed = self.client.patch(self.path, headers=self.a, json={'title': 'My custom adventure', 'revision': 1})
        self.assertEqual(renamed.status_code, 200, renamed.json)
        self.assertEqual(self.client.patch(self.path, headers=self.a, json={'title':'collision', 'revision':1}).status_code, 409)
        rid = self.submit().json['runId']; self.complete(rid)
        self.assertEqual(self.client.get(self.path, headers=self.a).json['conversation']['title'], 'My custom adventure')
        self.assertTrue(self.client.get(self.path, headers=self.a).json['conversation']['manualTitle'])

    def test_title_and_readable_message_original_recovery(self):
        accepted = self.submit(files=True)
        self.assertEqual(accepted.status_code, 202, accepted.json)
        rid = accepted.json['runId']
        self.assertEqual(self.submit(files=True).json['runId'], rid)
        self.complete(rid)
        result = self.client.get(self.path, headers=self.a).json
        self.assertNotEqual(result['conversation']['title'], 'New venture')
        root = self.venture.root(self.cid)
        snapshot = json.loads((root/'messages'/f'{rid}.json').read_text())
        attachment = root/snapshot['attachments'][0]['recoveryPath']
        self.assertEqual(attachment.name, '01-facts.txt')
        self.assertEqual(attachment.read_bytes(), b'Wing facts and recorded dimensions.')
        url = self.path+'/uploads/'+rid+'/0'
        download = self.client.get(url, headers=self.a)
        self.assertEqual(download.data, attachment.read_bytes()); download.close()
        self.assertEqual(self.client.get(url, headers=self.b).status_code, 404)
        self.assertNotIn('sk-test-venture', json.dumps(snapshot))

    def test_generated_files_retained_and_collision_safe(self):
        rid = self.submit().json['runId']
        row = self.service.row(rid)
        path = self.venture.artifact_path(row, 'a1', 'report.html')
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text('<h1>Retained</h1>')
        with server.connect_db() as db:
            db.execute('INSERT INTO run_artifacts(id,run_id,container_id,file_id,name,mime,size,path) VALUES(?,?,?,?,?,?,?,?)', ('a1',rid,'cntr_1','file_1','report.html','text/html',path.stat().st_size,str(path)))
        self.complete(rid)
        self.assertNotEqual(path, self.venture.artifact_path(row, 'a2', 'report.html'))
        url='/api/projects/'+self.cid+'/runs/'+rid+'/artifacts/a1'
        result=self.client.get(url, headers=self.a); self.assertIn(b'Retained',result.data);result.close()
        self.assertEqual(self.client.get(url, headers=self.b).status_code,404)
        server.prune_expired()
        self.assertTrue(path.exists())

    def test_calibration_and_usage_are_idempotent(self):
        initial = self.funding(); self.assertEqual(initial.status_code,200,initial.json)
        self.assertTrue({'balance','amount','capacity'}.isdisjoint(initial.json))
        rid=self.submit().json['runId']; self.complete(rid)
        once=self.venture.funding.status('firebase:alice',key_id('sk-test-venture'))
        self.venture.funding.account_for(self.service.row(rid))
        twice=self.venture.funding.status('firebase:alice',key_id('sk-test-venture'))
        self.assertEqual(once['fraction'],twice['fraction'])
        self.assertLess(once['fraction'],1)
        added=self.funding('add','5','calibrate-request-0002',once['revision'])
        self.assertEqual(added.status_code,200,added.json)
        duplicate=self.funding('add','5','calibrate-request-0002',once['revision'])
        self.assertEqual(duplicate.json['revision'],added.json['revision'])
        self.assertEqual(self.funding('add','6','calibrate-request-0002',added.json['revision']).status_code,409)
        self.assertEqual(self.client.get('/api/venture/funding',headers=self.b).json['status'],'no-key')

    def test_pending_calibration_and_authoritative_exhaustion(self):
        self.funding();rid=self.submit().json['runId']
        self.assertEqual(self.funding('set','30','calibrate-request-0002',1).status_code,409)
        self.venture.funding.exhausted(self.service.row(rid),'rate_limit_exceeded')
        self.assertEqual(self.venture.funding.status('firebase:alice',key_id('sk-test-venture'))['status'],'available')
        self.venture.funding.exhausted(self.service.row(rid),'credit_balance_exhausted')
        self.assertEqual(self.venture.funding.status('firebase:alice',key_id('sk-test-venture'))['status'],'exhausted')

    def test_retry_preserves_branch_and_request_identity(self):
        first=self.submit().json['runId'];self.complete(first)
        second=self.submit('request-venture-0002',previous=first).json['runId'];self.complete(second,'resp_venture_2')
        retry=self.submit('request-venture-0003',previous=first,retry=second)
        self.assertEqual(retry.status_code,202,retry.json)
        row=self.service.row(retry.json['runId'])
        self.assertEqual(row['previous_id'],first)
        self.assertEqual(row['venture_retry_of'],second)
        self.assertEqual(len(self.client.get(self.path,headers=self.a).json['runs']),3)

    def test_history_requires_response_and_titles_first_successful_exchange(self):
        first=self.submit().json['runId']
        self.service.update(first,status='error',text='',error='Unavailable')
        self.assertEqual(self.client.get('/api/venture/conversations',headers=self.a).json['conversations'],[])
        second=self.submit('request-venture-0002').json['runId']
        self.service.update(second,status='in_progress',text='Inspect the cowling gaps.')
        rows=self.client.get('/api/venture/conversations',headers=self.a).json['conversations']
        self.assertEqual(len(rows),1)
        self.assertLessEqual(len(rows[0]['title'].split()),4)
        self.assertEqual(rows[0]['graphic'],'plane')
        self.complete(second)
        self.assertLessEqual(len(self.client.get(self.path,headers=self.a).json['conversation']['title'].split()),4)

    def test_existing_titles_migrate_without_changing_manual_names(self):
        rid=self.submit().json['runId'];self.complete(rid)
        with server.connect_db() as db:
            db.execute("UPDATE venture_conversations SET title='A very long old automatically generated title',metadata_version=0 WHERE id=?",(self.cid,))
        self.venture.initialize()
        self.assertLessEqual(len(self.venture.lookup(self.cid)['title'].split()),4)
        with server.connect_db() as db:
            db.execute("UPDATE venture_conversations SET title='Keep my own longer custom title',manual_title=1,metadata_version=0 WHERE id=?",(self.cid,))
        self.venture.initialize()
        self.assertEqual(self.venture.lookup(self.cid)['title'],'Keep my own longer custom title')

    def test_funding_zero_addition_and_changed_connection_are_rejected(self):
        self.funding()
        self.assertEqual(self.funding('add','0','calibrate-request-0002',1).status_code,400)
        result=self.client.post('/api/venture/funding',headers=self.a,json=dict(kind='add',amount='5',requestId='calibrate-request-0003',revision=1,connectionId='old-key'))
        self.assertEqual(result.status_code,409)
        self.assertEqual(self.venture.funding.status('firebase:alice',key_id('sk-test-venture'))['revision'],1)

    def test_model_catalog_is_scoped_to_saved_key_and_read_only(self):
        response=MagicMock();response.__enter__.return_value=response;response.status_code=200
        response.json.return_value={'data':[{'id':'gpt-6-astra'},{'id':'gpt-6-luna'},{'id':'gpt-6-luna'},{'id':'<script>'},{'id':'text-embedding-3-small'}]}
        with patch('venture.requests.get',return_value=response) as transport:
            result=self.client.get('/api/venture/models',headers=self.a)
            self.assertEqual(result.status_code,200,result.json)
            self.assertEqual([m['id'] for m in result.json['models']],['gpt-6-astra','gpt-6-luna','text-embedding-3-small'])
            self.assertNotIn('sk-test-venture',json.dumps(result.json))
            self.client.get('/api/venture/models',headers=self.a)
            self.assertEqual(transport.call_count,1)
            self.assertEqual(self.client.get('/api/venture/models',headers=self.b).json['status'],'no-key')
            self.assertEqual(transport.call_count,1)
            with server.connect_db() as db:db.execute('UPDATE account_credentials SET openai_key_cipher=? WHERE uid=?',(b'sk-replaced','firebase:alice'))
            self.client.get('/api/venture/models',headers=self.a)
            self.assertEqual(transport.call_count,2)
            self.assertEqual(transport.call_args.kwargs['headers']['Authorization'],'Bearer sk-replaced')


class VenturePureTests(unittest.TestCase):
    def setUp(self):
        self.catalog=json.loads(Path(__file__).with_name('venture-pricing.json').read_text())
    def test_usage_cache_and_reasoning_not_double_billed(self):
        response=dict(model='gpt-6-astra',service_tier='default',usage=dict(input_tokens=1000,output_tokens=100,input_tokens_details={'cached_tokens':200,'cache_write_tokens':100},output_tokens_details={'reasoning_tokens':90}),output=[])
        result=estimate(response,'gpt-6-astra',self.catalog,now=1791320000)
        self.assertEqual(result['micro'],13450)
        response['usage']['output_tokens_details']['reasoning_tokens']=1
        self.assertEqual(estimate(response,'gpt-6-astra',self.catalog)['micro'],13450)
        response['model']='unknown-model'
        self.assertTrue(estimate(response,'unknown-model',self.catalog)['problems'])
    def test_money_validation(self):
        self.assertEqual(amount_micro('25.123456'),25123456)
        for value in ('-1','nan','1e3','1.1234567',float('nan'),None):
            with self.assertRaises(ValueError):amount_micro(value)
    def test_windows_names_and_local_titles(self):
        for name in ('CON','NUL.txt','../../report.html',r'c:\bad\path.txt'):
            result=disk_name(name);self.assertNotIn('/',result);self.assertNotIn('\\',result)
        self.assertTrue(local_title('Please inspect cowling gaps and prepare a work order','The cowling gap inspection needs measurements.'))
        for prompt,reply in [('Hi','Hello there'),('','Here is your inspection report'),('Can you help me create a detailed report on aircraft corrosion around the wing spar','Inspect the aircraft wing spar')]:
            self.assertLessEqual(len(local_title(prompt,reply).split()),4)
        self.assertEqual(local_graphic('Fix this Python API code','Here is the function'),'code')
    def test_unsafe_archive_rejected_without_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'source.zip'
            with zipfile.ZipFile(path,'w') as z:z.writestr('../escape.txt','bad')
            with self.assertRaises(ValueError):check_zip(path)
            self.assertFalse((Path(temp).parent/'escape.txt').exists())
            with zipfile.ZipFile(path,'w') as z:z.writestr('notes.txt','safe')
            check_zip(path)

if __name__=='__main__':unittest.main()
