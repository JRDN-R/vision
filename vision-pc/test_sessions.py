"""Offline integration checks for project authorization, restart safety and durable runs."""
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import server
import sessions


class FakeResponse:
    def __init__(self, value=None, events=None, content=b'file bytes', status=200):
        self.value, self.events, self.content = value, events or [], content
        self.status_code = status
        self.headers = {}
    def json(self):
        return self.value
    def close(self):
        pass
    def iter_lines(self, **kwargs):
        yield from [b'data: '+json.dumps(event).encode() for event in self.events]
    def iter_content(self, size):
        yield self.content


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        config = self.root / 'config.json'
        config.write_text(json.dumps({'token': 'a'*48, 'publicAccess': True}))
        server.configure(config)
        server.app.config['TESTING'] = True
        self.client, self.service = server.app.test_client(), server.sessions
        self.project_id = 'project_test_0001'
        self.headers = {'Authorization': 'Bearer '+'a'*48, 'X-Vision-Project-Key': 's'*48, 'X-OpenAI-Key': 'sk-not-real', 'Origin': 'null'}
        self.base = '/api/projects/'+self.project_id
        self.vault = patch.multiple(sessions, protect_secret=lambda x: b'ciphertext', unprotect_secret=lambda x: 'sk-not-real')
        self.vault.start()
        self.save()
    def tearDown(self):
        self.vault.stop()
        self.tmp.cleanup()
    def save(self, revision=0, body=None):
        return self.client.put(self.base, json={'project': body or {'nodes': [], 'cloudProject': {'projectKey': 's'*48}}, 'revision': revision}, headers=self.headers)
    def submit(self, cid='request_1', previous=None, files=True, **options):
        opts = dict(clientRequestId=cid, message='Analyze the board', model='gpt-6-astra', projectPrompt='Read MAIN_PROMPT.txt', **options)
        if previous:
            opts['previousRunId'] = previous
        data = {'options': json.dumps(opts)}
        if files:
            data['file'] = (io.BytesIO(b'PK\x03\x04 project'), 'vision-project.zip')
            data['attachments'] = (io.BytesIO(b'some facts'), 'notes.txt')
        return self.client.post(self.base+'/runs', headers=self.headers, data=data)
    def terminal(self, rid='resp_1', text='A finished response'):
        return {'id': rid, 'status': 'completed', 'output': [{'type':'code_interpreter_call','container_id':'cntr_1'}, {'type':'message', 'content':[{'type':'output_text','text':text, 'annotations':[{'type':'container_file_citation','container_id':'cntr_1','file_id':'cfile_1','filename':'answer.txt'}]}]}]}
    def fake_calls(self, method, url, **kwargs):
        path = url.replace(sessions.OPENAI, '')
        if method == 'POST' and path == '/files':
            return FakeResponse({'id':'file_1'})
        if method == 'POST' and path == '/responses':
            return FakeResponse(events=[{'type':'response.created','sequence_number':0,'response':{'id':'resp_1','status':'in_progress'}}, {'type':'response.output_text.delta','sequence_number':1,'delta':'A finished response'}, {'type':'response.completed','sequence_number':2,'response':self.terminal()}])
        if path == '/containers/cntr_1/files':
            return FakeResponse({'data':[]})
        if path.endswith('/content'):
            return FakeResponse(content=b'retained deliverable')
        if path == '/responses/resp_1':
            return FakeResponse(self.terminal())
        raise AssertionError((method,path))
    def test_project_authorization_conflicts_and_capabilities(self):
        result = self.client.get(self.base, headers=self.headers)
        self.assertEqual(result.json['revision'], 1)
        self.assertEqual(self.save().status_code, 409)
        self.assertEqual(self.save(1).json['revision'], 2)
        bad = {**self.headers,'X-Vision-Project-Key':'wrong'*10}
        self.assertEqual(self.client.get(self.base, headers=bad).status_code, 403)
        self.assertEqual(self.client.get(self.base+'/runs', headers=bad).status_code, 403)
        self.assertEqual(self.client.get('/api/projects', headers=self.headers).status_code, 404)
        self.assertEqual(self.save(2, {'apiKey':'sk-secret'}).status_code, 400)
        health = self.client.get('/api/health', headers=self.headers).json
        self.assertEqual(health['maxProjectBytes'], 150*1024*1024)
        self.assertEqual(health['maxArchiveBytes'], 25*1024*1024)
        self.assertTrue(health['capabilities']['persistentRuns'])
        preflight = self.client.options(self.base, headers={'Origin':'https://any.example'})
        self.assertIn('PUT', preflight.headers['Access-Control-Allow-Methods'])
        self.assertIn('X-Vision-Project-Key', preflight.headers['Access-Control-Allow-Headers'])
    def test_project_save_larger_than_run_upload_limit(self):
        body = {'notes':'a'*(26*1024*1024)}
        response = self.save(1, body)
        self.assertEqual(response.status_code, 200)
    def test_background_worker_persists_files_without_browser(self):
        accepted = self.submit()
        self.assertEqual(accepted.status_code, 202, accepted.json)
        run_id = accepted.json['runId']
        self.assertEqual(self.submit().json['runId'], run_id)
        self.assertEqual(self.submit('request_2').status_code, 409)
        row = self.service.row(run_id)
        self.assertEqual(row['key_cipher'], b'ciphertext')
        self.assertNotIn('sk-not-real', json.dumps(accepted.json))
        with patch.object(sessions.requests, 'request', side_effect=self.fake_calls):
            self.assertTrue(self.service.work_once())
        completed = self.client.get(self.base+'/runs/'+run_id, headers=self.headers).json
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(completed['text'], 'A finished response')
        self.assertEqual(completed['projectPrompt'], 'Read MAIN_PROMPT.txt')
        self.assertEqual(len(completed['attachments']), 2)
        self.assertIsNone(self.service.row(run_id)['key_cipher'])
        aid = completed['artifacts'][0]['id']
        file = self.client.get(self.base+'/runs/'+run_id+'/artifacts/'+aid, headers=self.headers)
        self.assertEqual(file.data, b'retained deliverable')
        file.close()
        with server.connect_db() as db:
            db.execute('UPDATE project_runs SET created_at=?,updated_at=? WHERE id=?', (time.time()-10*86400,time.time()-10*86400,run_id))
        server.prune_expired()
        self.assertEqual(self.client.get(self.base+'/runs/'+run_id, headers=self.headers).status_code, 200)
        replay = self.client.get(self.base+'/runs/'+run_id+'/events?after=0', headers=self.headers)
        self.assertIn(b'event: snapshot', replay.data)
        self.assertIn(b'A finished response', replay.data)
    def test_restart_preserves_known_runs_and_blocks_ambiguous_submission(self):
        rid = self.submit().json['runId']
        self.service.update(rid, status='submitting')
        self.service.recover()
        self.assertEqual(self.service.row(rid)['status'], 'error')
        self.assertIn('not automatically submitted again', self.service.row(rid)['error'])
        rid2 = self.submit('request_2').json['runId']
        self.service.update(rid2, status='in_progress', response_id='resp_1')
        self.service.recover()
        self.assertEqual(self.service.row(rid2)['status'], 'in_progress')
        with patch.object(sessions.requests, 'request', side_effect=self.fake_calls) as calls:
            self.service.work_once()
        self.assertFalse(any(c.args[0] == 'POST' and c.args[1].endswith('/responses') for c in calls.call_args_list))
        self.assertEqual(self.service.row(rid2)['status'], 'completed')
    def test_restart_saving_uses_local_response(self):
        rid = self.submit().json['runId']
        self.service.update(rid, status='saving', response_id='resp_1', container_id='cntr_1', response_json=json.dumps(self.terminal()))
        self.service.recover()
        self.assertEqual(self.service.row(rid)['status'], 'saving')
        with patch.object(sessions.requests, 'request', side_effect=self.fake_calls) as calls:
            self.service.work_once()
        self.assertFalse(any(c.args[1].endswith('/responses/resp_1') for c in calls.call_args_list))
        self.assertEqual(self.service.row(rid)['status'], 'completed')
    def test_cancel_queued_and_project_isolation(self):
        rid = self.submit().json['runId']
        cancelled = self.client.post(self.base+'/runs/'+rid+'/cancel', headers=self.headers)
        self.assertEqual(cancelled.json['status'], 'cancelled')
        self.assertIsNone(self.service.row(rid)['key_cipher'])
        other = '/api/projects/project_other_0002'
        self.client.put(other, json={'project':{},'revision':0}, headers=self.headers)
        self.assertEqual(self.client.get(other+'/runs/'+rid, headers=self.headers).status_code,404)
        result = self.client.post(other+'/runs', data={'options':json.dumps({'clientRequestId':'other','message':'hi','previousRunId':rid})}, headers=self.headers)
        self.assertEqual(result.status_code,404)
    def test_provider_expiration_rebuilds_from_saved_context_and_files(self):
        rid = self.submit().json['runId']
        with patch.object(sessions.requests, 'request', side_effect=self.fake_calls):
            self.service.work_once()
        second = self.submit('request_2', previous=rid, files=False).json['runId']
        paths = []
        def expired(key, method, path, **kwargs):
            paths.append((method,path,kwargs))
            if path in ('/containers/cntr_1','/responses/resp_1'):
                raise sessions.UpstreamFailure('expired',404)
            if path == '/files':
                return {'id':'file_restored'}
            raise AssertionError(path)
        with patch.object(self.service,'call_json',side_effect=expired):
            payload = self.service.prepare_payload(self.service.row(second),'sk-not-real')
        self.assertNotIn('previous_response_id',payload)
        self.assertEqual(payload['input'][1]['role'],'assistant')
        self.assertEqual(payload['input'][1]['content'],'A finished response')
        self.assertEqual(len(payload['tools'][0]['container']['file_ids']),3)
    def test_unknown_post_disconnect_never_resubmits(self):
        rid = self.submit(files=False).json['runId']
        def uncertain(key, method, path, **kwargs):
            self.assertEqual((method,path),('POST','/responses'))
            raise sessions.UpstreamFailure('Connection lost',502)
        with patch.object(self.service,'call',side_effect=uncertain) as call:
            self.service.work_once()
            self.assertFalse(self.service.work_once())
        self.assertEqual(call.call_count,1)
        self.assertEqual(self.service.row(rid)['status'],'error')
        self.assertIn('may have been accepted',self.service.row(rid)['error'])
    def test_cancel_while_saving_keeps_durable_completion(self):
        rid = self.submit(files=False).json['runId']
        self.service.update(rid,status='saving',response_id='resp_1',container_id='cntr_1',response_json=json.dumps(self.terminal()),cancel_requested=1)
        with patch.object(sessions.requests,'request',side_effect=self.fake_calls) as calls:
            self.service.work_once()
        self.assertFalse(any(c.args[1].endswith('/cancel') for c in calls.call_args_list))
        self.assertEqual(self.service.row(rid)['status'],'completed')
        self.assertIsNone(self.service.row(rid)['key_cipher'])

    def test_output_download_retry_preserves_key_until_retained(self):
        rid = self.submit(files=False).json['runId']
        self.service.update(rid,status='saving',response_id='resp_1',container_id='cntr_1',response_json=json.dumps(self.terminal()))
        def offline(method,url,**kwargs):
            if url.endswith('/content'):
                raise sessions.requests.ConnectionError('test outage')
            return self.fake_calls(method,url,**kwargs)
        with patch.object(sessions.requests,'request',side_effect=offline):
            self.service.work_once()
        self.assertEqual(self.service.row(rid)['status'],'saving')
        self.assertIsNotNone(self.service.row(rid)['key_cipher'])
        self.service.update(rid,next_attempt=0)
        with patch.object(sessions.requests,'request',side_effect=self.fake_calls):
            self.service.work_once()
        self.assertEqual(self.service.row(rid)['status'],'completed')
        self.assertIsNone(self.service.row(rid)['key_cipher'])
        self.assertTrue(self.service.snapshot(rid)['artifacts'][0]['ready'])

    def test_partial_snapshot_does_not_duplicate_replayed_delta(self):
        rid = self.submit(files=False).json['runId']
        self.service.update(rid,status='in_progress',response_id='resp_1',text='Hello',upstream_sequence=1)
        def reconnect(key, method, path, **kwargs):
            if kwargs.get('stream'):
                return FakeResponse(events=[{'type':'response.output_text.delta','sequence_number':2,'delta':' world'}])
            return FakeResponse({'id':'resp_1','status':'in_progress','output':[{'type':'message','content':[{'type':'output_text','text':'Hello world'}]}]})
        with patch.object(self.service,'call',side_effect=reconnect):
            self.service.work_once()
        self.assertEqual(self.service.row(rid)['text'],'Hello world')



class RunParameterTests(SessionTests):
    def test_settings_survive_queue_and_map_to_responses(self):
        opts={'mode':'pro','effort':'max','verbosity':'high','webSearch':True,'codeInterpreter':True}
        response=self.submit(files=False,runOptions=opts)
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.json['runOptions'],opts)
        row=self.service.row(response.json['runId'])
        payload=self.service.prepare_payload(row,'sk-not-real')
        self.assertEqual(payload['reasoning']['mode'],'pro')
        self.assertEqual(payload['reasoning']['effort'],'max')
        self.assertEqual(payload['text'],{'verbosity':'high'})
        self.assertEqual([t['type'] for t in payload['tools']],['code_interpreter','web_search'])
        self.assertNotIn('do not browse',payload['instructions'])
        self.assertTrue(self.client.get('/api/health',headers=self.headers).json['capabilities']['runParametersV1'])
    def test_text_only_disables_container_and_default_fields_are_omitted(self):
        response=self.submit(files=False,runOptions={'codeInterpreter':False})
        payload=self.service.prepare_payload(self.service.row(response.json['runId']),'sk-not-real')
        self.assertNotIn('tools',payload)
        self.assertNotIn('text',payload)
        self.assertNotIn('mode',payload.get('reasoning',{}))
        self.assertNotIn('effort',payload.get('reasoning',{}))
    def test_rejects_invalid_settings_before_billable_submission(self):
        for opts in [{'mode':'nonsense'},{'webSearch':'true'},{'effort':'none'},{'arbitraryTool':True},'invalid']:
            result=self.submit(files=False,runOptions=opts)
            self.assertEqual(result.status_code,400,result.json)
        self.assertEqual(self.submit(runOptions={'codeInterpreter':False}).status_code,400)
    def test_citations_are_retained_for_clickable_sources(self):
        response=self.submit(files=False);rid=response.json['runId'];terminal=self.terminal()
        terminal['output'][1]['content'][0]['annotations'].append({'type':'url_citation','url':'https://example.com','title':'Source'})
        self.service.absorb_response(rid,terminal)
        self.assertEqual(self.service.snapshot(rid)['citations'],[{'url':'https://example.com','title':'Source'}])

if __name__ == '__main__':
    unittest.main()
