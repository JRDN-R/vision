"""Offline integration tests. FakeRuntime is an explicit protocol fixture, not a model benchmark."""
import io
import json
from pathlib import Path
import unittest
import subprocess
import sys
import os
import tempfile
from unittest.mock import patch

import server
import test_firebase_auth
from local_preferences import MODEL, EPOCH, DISCLOSURE, local_settings
from owned_assets import exclusive_file
import local_runtime


class FakeRuntime:
    requests = []
    def __init__(self, settings, cancelled, stage):
        self.cancelled = cancelled
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def count(self, messages):
        return sum(len(m['content'])//4+10 for m in messages)
    def generate(self, messages, max_tokens):
        self.requests.append(messages)
        yield {'choices': [{'delta': {'reasoning_content': 'PRIVATE REASONING TEST'}}]}
        yield {'choices': [{'delta': {'content': 'The bore is 0.380 inches [facts.txt].'}}]}
        yield {'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 90, 'completion_tokens': 15}}


class LocalInferenceTests(unittest.TestCase):
    setUpClass = classmethod(test_firebase_auth.FirebaseTests.setUpClass.__func__)
    token = test_firebase_auth.FirebaseTests.token
    headers = test_firebase_auth.FirebaseTests.headers

    def setUp(self):
        test_firebase_auth.FirebaseTests.setUp(self)
        self.s = server.sessions
        self.local = self.s.local
        self.runtime = self.local.runtime_factory
        self.local.runtime_factory = FakeRuntime
        FakeRuntime.requests = []
        placeholder = self.root / 'runtime-fixture'
        placeholder.write_text('Fixture only; no weights downloaded.')
        self.local.settings = dict(enabled=True, runtimePath=str(placeholder), modelPath=str(placeholder),
                                   manifestPath=str(placeholder), contextTokens=4096, maxOutputTokens=2048)
        self.cid = 'local_conversation_0001'
        self.new(self.cid)
        self.client.post('/api/venture/local/disclosure', headers=self.a, json={'version':DISCLOSURE})

    def tearDown(self):
        self.local.runtime_factory = self.runtime
        self.s.stop.clear()
        test_firebase_auth.FirebaseTests.tearDown(self)

    def new(self, cid, headers=None):
        result = self.client.post('/api/venture/conversations', headers=headers or self.a, json={'id':cid})
        self.assertEqual(result.status_code, 201, result.json)

    def send(self, cid=None, headers=None, request_id='local-turn-1', **options):
        value = dict(clientRequestId=request_id, model=MODEL, maxOutputTokens=1024,
                     message='What is the bore?', runOptions={'webSearch':False,'codeInterpreter':False})
        value.update(options)
        return self.client.post('/api/projects/'+(cid or self.cid)+'/runs', headers=headers or self.a,
                                data={'options':json.dumps(value)})

    def test_no_key_discovery_disclosure_answer_retention_and_no_billing(self):
        catalog = self.client.get('/api/venture/models', headers=self.a)
        self.assertEqual(catalog.json['models'][0]['id'], MODEL)
        self.assertFalse(catalog.json['models'][0]['capabilities']['vision'])
        with patch.object(self.s, 'call', side_effect=AssertionError('Cloud call')), \
                patch.object(self.s.venture.funding, 'bind', side_effect=AssertionError('Paid binding')):
            result = self.send()
            self.assertEqual(result.status_code,202,result.json)
            rid = result.json['runId']
            self.s.work_once()
        saved = self.s.snapshot(rid)
        self.assertEqual(saved['status'],'completed',saved)
        self.assertEqual(saved['provider'],'local_llama_cpp')
        self.assertIsNone(saved['responseId'])
        self.assertIsNone(self.s.row(rid)['venture_key_id'])
        self.assertNotIn('PRIVATE REASONING TEST',json.dumps(saved))
        self.assertEqual(self.send().json['runId'],rid)
        self.assertEqual(len(FakeRuntime.requests),1)
        self.assertEqual(self.client.get('/api/projects/'+self.cid+'/runs/'+rid,headers=self.b).status_code,404)

    def test_disclosure_is_account_scoped_and_required(self):
        self.new('local_bob_conversation',self.b)
        result=self.send('local_bob_conversation',self.b)
        self.assertEqual(result.status_code,409)
        self.assertEqual(result.json['code'],'local-disclosure-required')

    def test_timing_estimates_only_use_this_accounts_completed_work(self):
        import time
        now=time.time()
        for index in range(3):
            rid=self.send(request_id='timing-alice-'+str(index)).json['runId']
            self.s.update(rid,status='completed',local_started_at=now-120,local_finished_at=now)
        self.new('local_bob_timing_chat',self.b)
        self.client.post('/api/venture/local/disclosure',headers=self.b,json={'version':DISCLOSURE})
        for index in range(3):
            rid=self.send('local_bob_timing_chat',self.b,request_id='timing-bob-'+str(index)).json['runId']
            self.s.update(rid,status='completed',local_started_at=now-3000,local_finished_at=now)
        pending=self.send(request_id='timing-current').json
        self.assertEqual(pending['localQueue']['estimateSamples'],3)
        self.assertEqual(pending['localQueue']['estimatedRemainingSeconds'],120)
        self.assertEqual(pending['localQueue']['estimateStatus'],'estimated')

    def test_files_work_with_code_disabled_and_no_cloud(self):
        options=dict(clientRequestId='local-file',model=MODEL,maxOutputTokens=1024,
                     message='What is the bore?',runOptions={'codeInterpreter':False})
        accepted=self.client.post('/api/projects/'+self.cid+'/runs',headers=self.a,
             data={'options':json.dumps(options),'attachments':(io.BytesIO(b'Bore diameter 0.380 inches. Do not enlarge.'),'facts.txt')})
        self.assertEqual(accepted.status_code,202,accepted.json)
        self.s.work_once()
        saved=self.s.snapshot(accepted.json['runId'])
        self.assertEqual(saved['status'],'completed',saved)
        prompt=json.dumps(FakeRuntime.requests)
        self.assertIn('0.380',prompt)
        self.assertIn('Do not enlarge',prompt)
        self.assertNotIn('file_id',prompt)

    def test_fifo_cross_process_lock_and_queue_privacy(self):
        first=self.send().json
        self.new('local_conversation_0002')
        second=self.send('local_conversation_0002',request_id='local-turn-2').json
        self.assertEqual(second['localQueue']['requestsAhead'],1)
        self.assertNotIn('alice',json.dumps(second['localQueue']))
        self.assertFalse(self.local.work_once(self.s.row(second['runId'])))
        with exclusive_file(self.s.app.config['DATA_DIR']/'local-inference.lock'):
            self.assertFalse(self.local.work_once(self.s.row(first['runId'])))
        self.s.work_once()
        self.assertEqual(self.s.row(first['runId'])['status'],'completed')
        self.s.work_once()
        self.assertEqual(self.s.row(second['runId'])['status'],'completed')

    def test_queued_cancel_and_restart_do_not_replay_generation(self):
        accepted=self.send().json
        url='/api/projects/'+self.cid+'/runs/'+accepted['runId']+'/cancel'
        self.assertEqual(self.client.post(url,headers=self.b).status_code,404)
        self.assertEqual(self.client.post(url,headers=self.a).json['status'],'cancelled')
        rid=self.send(request_id='interrupted').json['runId']
        self.s.update(rid,status='in_progress',text='Partial output')
        self.local.recover()
        self.assertEqual(self.s.row(rid)['status'],'error')
        self.assertEqual(self.s.row(rid)['text'],'Partial output')
        self.assertFalse(self.s.work_once())

    def test_generation_budget_and_stream_failure_remain_incomplete(self):
        class Exhausted(FakeRuntime):
            def generate(self,*args):
                yield {'choices':[{'delta':{'reasoning_content':'not final'},'finish_reason':'length'}]}
        self.local.runtime_factory=Exhausted
        rid=self.send().json['runId'];self.s.work_once()
        self.assertEqual(self.s.snapshot(rid)['status'],'incomplete')
        self.assertIn('budget',self.s.snapshot(rid)['error'])

    def test_migration_once_explicit_choice_stale_tabs_and_history_identity(self):
        prefs=self.local.preferences
        rid=self.send().json['runId'];self.s.work_once()
        self.local.settings['defaultEpoch']=EPOCH
        gate=patch.object(self.local, 'migration_eligible', return_value=True)
        gate.start();self.addCleanup(gate.stop)
        first=prefs.get('firebase:alice')
        self.assertEqual(first['settings']['model'],MODEL)
        self.assertEqual(prefs.get('firebase:alice')['preferenceRevision'],first['preferenceRevision'])
        cloud={**first['settings'],'model':'gpt-6-astra','maxOutputTokens':16000}
        updated=prefs.put('firebase:alice',dict(settings=cloud,preferenceRevision=first['preferenceRevision'],
                    defaultEpoch=EPOCH,explicitModelSelection=True))
        self.assertEqual(prefs.get('firebase:alice')['settings']['model'],'gpt-6-astra')
        self.assertEqual(self.s.row(rid)['model'],MODEL)
        with self.assertRaises(self.s.Error):
            prefs.put('firebase:alice',dict(settings=local_settings(cloud),preferenceRevision=first['preferenceRevision'],
                     defaultEpoch=EPOCH,explicitModelSelection=True))
        self.assertEqual(self.send(request_id='stale-tab').status_code,409)
        self.assertEqual(prefs.get('firebase:bob')['settings']['model'],MODEL)
        self.assertEqual(self.client.get('/api/venture/conversations/'+self.cid,headers=self.a).json['conversation']['settings']['model'],'gpt-6-astra')

    def test_oversized_prompt_fails_without_cloud_fallback(self):
        rid=self.send(message='Precise facts '*3000).json['runId']
        self.s.work_once()
        result=self.s.snapshot(rid)
        self.assertEqual(result['status'],'error')
        self.assertIn('do not fit',result['error'])
        self.assertEqual(FakeRuntime.requests,[])

    def test_local_dictation_cannot_use_gemini(self):
        result=self.client.post('/api/venture/dictation',headers=self.a,data={
            'requestId':'local-dictation-1234','provider':'gemini','localOnly':'true',
            'audio':(io.BytesIO(b'not parsed'),'voice.wav','audio/wav')})
        self.assertEqual(result.status_code,400)
        self.assertIn('Whisper',result.json['error'])

    def test_independent_process_cannot_start_a_second_local_job(self):
        rid=self.send().json['runId']
        script='from owned_assets import exclusive_file;from pathlib import Path;import sys\nwith exclusive_file(Path(sys.argv[1])):\n print("locked",flush=True)\n sys.stdin.read(1)\n'
        env=dict(os.environ,PYTHONPATH=str(Path(__file__).parent))
        child=subprocess.Popen([sys.executable,'-c',script,str(self.s.app.config['DATA_DIR']/'local-inference.lock')],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
        try:
            self.assertEqual(child.stdout.readline().strip(),'locked')
            self.assertFalse(self.s.work_once())
            self.assertEqual(self.s.row(rid)['status'],'queued')
            child.communicate('x',timeout=10)
        finally:
            if child.poll() is None:child.kill();child.communicate()
        self.s.work_once();self.assertEqual(self.s.row(rid)['status'],'completed')

    def test_accepted_profile_is_immutable_and_running_cancel_stops_the_attempt(self):
        rid=self.send().json['runId']
        before=self.s.row(rid)['local_profile_json']
        self.local.settings['contextTokens']=16384
        self.assertEqual(json.loads(before)['contextTokens'],4096)
        outer=self
        class CancelledRuntime(FakeRuntime):
            def generate(self,*args):
                yield {'choices':[{'delta':{'content':'Partial answer'}}]}
                outer.s.update(rid,cancel_requested=1)
                yield {'choices':[{'delta':{'content':' should not be saved'}}]}
        self.local.runtime_factory=CancelledRuntime
        self.s.work_once()
        saved=self.s.snapshot(rid)
        self.assertEqual(saved['status'],'cancelled')
        self.assertEqual(saved['text'],'Partial answer')
        self.assertEqual(saved['localQueue']['metrics']['contextTokens'],4096)

    def test_queue_positions_refresh_even_if_a_waiting_run_has_no_new_tokens(self):
        first=self.send().json
        self.new('local_conversation_0002')
        second=self.send('local_conversation_0002',request_id='wait-for-first').json
        self.assertEqual(second['localQueue']['requestsAhead'],1)
        self.client.post('/api/projects/'+self.cid+'/runs/'+first['runId']+'/cancel',headers=self.a)
        response=self.client.get('/api/venture/conversations/local_conversation_0002?since=9999999999',headers=self.a)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['runs'][0]['localQueue']['requestsAhead'],0)


class LocalModelContractTests(unittest.TestCase):
    def test_manifest_matches_reviewed_runtime_and_publisher_sampling(self):
        manifest = json.loads(Path(__file__).with_name('local-model-manifest.json').read_text())
        for key, expected in [('modelBytes', local_runtime.MODEL_BYTES),
                              ('modelSha256', local_runtime.MODEL_SHA),
                              ('modelRevision', local_runtime.MODEL_REVISION),
                              ('modelRepository', local_runtime.MODEL_REPOSITORY),
                              ('modelFile', local_runtime.MODEL_FILE),
                              ('runtimeCommit', local_runtime.RUNTIME_COMMIT)]:
            self.assertEqual(manifest[key], expected)
        # Exercise the wire payload rather than merely checking source constants.
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def iter_lines(self, **kwargs): return iter([b'data: [DONE]'])
        runtime = local_runtime.LlamaRuntime({}, lambda: False, lambda value: None)
        try:
            with patch.object(runtime, 'request', return_value=Response()) as request:
                self.assertEqual(list(runtime.generate([{'role':'user','content':'Hello'}], 1024)), [])
            payload = request.call_args.args[1]
            self.assertEqual(payload['temperature'], manifest['sampling']['temperature'])
            self.assertEqual(payload['top_p'], manifest['sampling']['topP'])
            self.assertEqual(payload['top_k'], manifest['sampling']['topK'])
            self.assertEqual(payload['max_tokens'], 1024)
        finally:
            runtime.http.close()

    def test_previous_model_and_modified_weights_cannot_pass_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / 'fixture.gguf'; model.write_bytes(b'fixture')
            executable = root / 'llama-server.exe'; executable.write_bytes(b'runtime fixture')
            manifest = json.loads(Path(__file__).with_name('local-model-manifest.json').read_text())
            manifest_path = root / 'manifest.json'
            settings = dict(runtimePath=str(executable), modelPath=str(model), manifestPath=str(manifest_path))
            runtime = local_runtime.LlamaRuntime(settings, lambda: False, lambda value: None)
            try:
                with patch.object(local_runtime, 'MODEL_BYTES', len(b'fixture')):
                    manifest['modelBytes'] = len(b'fixture')
                    # A stale 27B provenance record must fail before hashing/loading.
                    manifest['modelRevision'] = '6374af6aaa473b4f3b5387db526ee533c709c6af'
                    manifest_path.write_text(json.dumps(manifest))
                    with patch.object(local_runtime, 'digest_file') as digest:
                        with self.assertRaisesRegex(local_runtime.LocalFailure, 'provenance'):
                            runtime.verify()
                        digest.assert_not_called()
                    manifest['modelRevision'] = local_runtime.MODEL_REVISION
                    manifest_path.write_text(json.dumps(manifest))
                    with self.assertRaisesRegex(local_runtime.LocalFailure, 'integrity'):
                        runtime.verify()
            finally:
                runtime.http.close()


if __name__=='__main__':
    unittest.main()
