"""Offline HTTP/Responses integration: actual auth and persistence, fake paid API."""
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import server
import sessions
from test_sessions import FakeResponse
import test_venture


class FakeEngine:
    def __init__(self, root, settings=None):
        self.root=root; self.values={}; self.ready=True; self.calls=[]
    def close(self):pass
    def start(self, workers=2):pass
    def enqueue(self,owner,pid,rev,project):
        self.values[(owner,pid,rev)]=project
        return self.status(owner,pid,rev)
    def status(self,owner,pid,revision=None):
        ready=self.ready and (owner,pid,revision) in self.values
        return dict(status='ready' if ready else 'updating',ready=ready,revision=revision)
    def prepare(self,owner,pid,revision,query,budget=48000,mode='adaptive'):
        self.calls.append((owner,pid,revision,query,mode))
        return dict(status='ready',manifest=dict(projectId=pid,revision=revision),
                    items=[dict(source='source-1',text='OPN 0030 WC 2CU0SA 1.5 HRS')],
                    text='OPN 0030 WC 2CU0SA 1.5 HRS',complete=True,warnings=[],metrics={'estimatedTokens':18})
    def source(self,owner,pid,rev,reference,budget=48000,offset=0):
        return self.prepare(owner,pid,rev,reference,budget)
    def node(self,owner,pid,rev,reference,budget=48000):
        return self.prepare(owner,pid,rev,reference,budget)


class ContextIntegrationTests(unittest.TestCase):
    setUpClass=classmethod(test_venture.VentureTests.setUpClass.__func__)
    token=test_venture.VentureTests.token
    headers=test_venture.VentureTests.headers

    def setUp(self):
        self.engine_patch=patch.dict(sys.modules,context_engine=SimpleNamespace(ContextEngine=FakeEngine))
        self.engine_patch.start()
        test_venture.VentureTests.setUp(self)
        self.service.context.settings['syncDebounceSeconds']=0
        self.board='board_context_00000001'
        self.board_path='/api/projects/'+self.board
        result=self.client.put(self.board_path,headers=self.a,json={'revision':0,'project':{'title':'Operations','nodes':[{'id':'instructions','text':'Reproduce every operation'}]}})
        self.assertEqual(result.status_code,200,result.json)
        self.engine=self.service.context.engine
    def tearDown(self):
        test_venture.VentureTests.tearDown(self)
        self.engine_patch.stop()

    def turn(self, request_id='ctx-turn-1', **kwargs):
        options=dict(clientRequestId=request_id,message='Reproduce every operation',model='gpt-6-astra',
                     runOptions={'codeInterpreter':False},boardContext=dict(projectId=self.board,revision=1,mode='adaptive'))
        options.update(kwargs)
        return self.client.post('/api/projects/'+self.cid+'/runs',headers=self.a,data={'options':json.dumps(options)})

    def test_autosave_enqueues_only_durable_marker_and_sync_coalesces(self):
        self.assertEqual(self.engine.values,{})
        self.client.put(self.board_path,headers=self.a,json={'revision':1,'project':{'nodes':[]}})
        with server.connect_db() as db:
            job=db.execute('SELECT revision FROM context_sync_jobs WHERE project_id=?',(self.board,)).fetchone()
        self.assertEqual(job[0],2)
        with patch.object(self.service.context,'enqueue',wraps=self.service.context.enqueue) as enqueued:
            self.assertTrue(self.service.context.sync_once())
        self.assertEqual(len(enqueued.call_args_list),1)
        self.assertIn(('firebase:alice',self.board,2),self.engine.values)
        self.assertNotIn(('firebase:alice',self.board,1),self.engine.values)

    def test_repeated_evidence_uses_verified_provider_chain_without_retransmission(self):
        rid=self.turn().json['runId']
        self.service.prepare_payload(self.service.row(rid),'sk-fake')
        self.service.update(rid,status='completed',response_id='saved_provider',text='Earlier answer')
        following=self.turn('ctx-repeated',previousRunId=rid).json['runId']
        with patch.object(self.service,'call_json',return_value={'id':'saved_provider'}):
            payload=self.service.prepare_payload(self.service.row(following),'sk-fake')
        self.assertEqual(payload['previous_response_id'],'saved_provider')
        current=json.dumps(payload['input'][-1])
        self.assertIn('retrieved evidence are unchanged',current)
        self.assertNotIn('OPN 0030',current)
        self.assertTrue(self.service.snapshot(following)['contextMetrics']['unchangedEvidenceReused'])

    def test_board_owner_independently_checked_and_no_private_binding_exposed(self):
        other='board_context_bob_0001'
        self.assertEqual(self.client.put('/api/projects/'+other,headers=self.b,json={'revision':0,'project':{}}).status_code,200)
        denied=self.turn(boardContext=dict(projectId=other,revision=1))
        self.assertEqual(denied.status_code,404)
        self.assertEqual(self.client.get(self.board_path+'/context?revision=1',headers=self.b).status_code,404)
        accepted=self.turn()
        self.assertEqual(accepted.status_code,202,accepted.json)
        self.assertNotIn('owner',accepted.json['boardContext'])
        self.assertEqual(accepted.json['boardContext']['revision'],1)
        self.assertIn('firebase:alice',self.service.row(accepted.json['runId'])['board_context_json'])
        self.assertEqual(self.client.get(self.board_path+'/context',headers={}).status_code,401)

    def test_background_pending_never_submits_paid_request(self):
        self.engine.ready=False
        result=self.turn();self.assertEqual(result.status_code,202,result.json)
        with patch.object(self.service,'call') as paid:
            self.service.work_once()
        paid.assert_not_called()
        row=self.service.row(result.json['runId'])
        self.assertEqual(row['status'],'queued')
        self.assertIn('exact board revision',row['phase'])

    def test_compact_pinned_context_without_ci_or_uploads(self):
        result=self.turn();rid=result.json['runId']
        self.client.put(self.board_path,headers=self.a,json={'revision':1,'project':{'nodes':[]}})
        with patch.object(self.service,'call_json') as paid:
            payload=self.service.prepare_payload(self.service.row(rid),'sk-fake')
        paid.assert_not_called()
        self.assertEqual(self.engine.calls[-1][2],1)
        serialized=json.dumps(payload)
        self.assertIn('OPN 0030 WC 2CU0SA 1.5 HRS',serialized)
        self.assertNotIn('MAIN_PROMPT.txt',serialized.split('"input":',1)[1])
        self.assertFalse(any(t['type']=='code_interpreter' for t in payload['tools']))
        self.assertEqual(payload['tools'][0]['name'],'vision_context')
        self.assertFalse(payload['parallel_tool_calls'])
        snapshot=self.service.snapshot(rid)
        self.assertTrue(snapshot['contextMetrics']['complete'])
        self.assertIsNone(snapshot['contextMetrics']['providerUsage'])

    def test_unknown_model_uses_server_prefetch_without_function_tools(self):
        rid=self.turn(model='custom-unverified-model').json['runId']
        payload=self.service.prepare_payload(self.service.row(rid),'sk-fake')
        self.assertNotIn('tools',payload)
        self.assertIn('server-managed retrieval only',json.dumps(payload))

    def test_invalid_revision_and_malformed_query_are_rejected(self):
        self.assertEqual(self.turn(boardContext={'projectId':self.board,'revision':True}).status_code,400)
        self.assertEqual(self.turn(boardContext={'projectId':self.board,'revision':999}).status_code,409)
        self.assertEqual(self.client.post(self.board_path+'/context/search',headers=self.a,json=[]).status_code,400)
        self.assertEqual(self.client.post(self.board_path+'/context/search',headers=self.a,json={'revision':1,'query':{'bad':1}}).status_code,400)

    def test_tool_continuation_preserves_instructions_and_all_provider_usage(self):
        rid=self.turn().json['runId'];submitted=[]
        first=dict(id='resp_context_1',status='completed',usage={'input_tokens':100,'output_tokens':10},
                   output=[dict(type='function_call',name='vision_context',call_id='call_1',arguments=json.dumps({'operation':'source','reference':'source-1','query':''}))])
        final=dict(id='resp_context_2',status='completed',usage={'input_tokens':140,'output_tokens':20},
                   output=[dict(type='message',content=[dict(type='output_text',text='Complete operation 0030.')])])
        def fake(key,method,path,**kwargs):
            self.assertEqual((method,path),('POST','/responses'))
            submitted.append(kwargs['json'])
            result=first if len(submitted)==1 else final
            return FakeResponse(events=[dict(type='response.created',sequence_number=0,response={'id':result['id'],'status':'in_progress'}),
                                        dict(type='response.completed',sequence_number=1,response=result)])
        with patch.object(self.service,'call',side_effect=fake):
            self.service.work_once()
            self.service.work_once()
        row=self.service.row(rid)
        self.assertEqual(row['status'],'completed',row.get('error'))
        self.assertEqual(row['response_id'],'resp_context_2')
        self.assertEqual(row['context_round'],1)
        self.assertEqual(submitted[1]['instructions'],submitted[0]['instructions'])
        self.assertEqual(submitted[1]['previous_response_id'],'resp_context_1')
        self.assertEqual(submitted[1]['input'][0]['type'],'function_call_output')
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM context_responses WHERE run_id=?',(rid,)).fetchone()[0],2)
        usage=self.service.snapshot(rid)['contextMetrics']['providerUsage']
        self.assertEqual(usage['input_tokens'],240)
        self.assertEqual(usage['output_tokens'],30)
        self.assertTrue(usage['complete'])

    def test_interrupted_continuation_never_resubmits(self):
        rid=self.turn().json['runId']
        self.service.update(rid,status='submitting',response_id='resp_parent',context_pending_parent='resp_parent')
        self.service.recover()
        row=self.service.row(rid)
        self.assertEqual(row['status'],'error')
        self.assertIn('not submitted again',row['error'])
        with patch.object(self.service,'call') as paid:
            self.assertFalse(self.service.work_once())
        paid.assert_not_called()

    def test_tool_budget_exhaustion_reports_incomplete(self):
        rid=self.turn().json['runId']
        response=dict(id='resp_limit',status='completed',output=[dict(type='function_call',name='vision_context',call_id='call_last',arguments='{}')])
        self.service.update(rid,status='saving',response_id=response['id'],response_json=json.dumps(response),context_round=4)
        with patch.object(self.service,'call') as paid:
            self.service.work_once()
        paid.assert_not_called()
        self.assertEqual(self.service.row(rid)['status'],'incomplete')
        self.assertIn('Coverage is incomplete',self.service.row(rid)['error'])

    def test_local_retrieval_interruption_resumes_saved_hop_not_new_inference(self):
        rid=self.turn().json['runId']
        response=dict(id='resp_interrupted_source',status='completed',output=[])
        self.service.update(rid,status='saving',response_id=response['id'],response_json=json.dumps(response))
        with patch.object(self.service.context,'continuation',side_effect=InterruptedError()),patch.object(self.service,'call') as paid:
            self.service.work_once()
        paid.assert_not_called()
        self.assertEqual(self.service.row(rid)['status'],'saving')
        self.service.recover()
        self.assertEqual(self.service.row(rid)['status'],'saving')

    def test_cancel_during_local_retrieval_prevents_next_paid_hop(self):
        rid=self.turn().json['runId']
        response=dict(id='resp_cancel_source',status='completed',output=[])
        self.service.update(rid,status='saving',response_id=response['id'],response_json=json.dumps(response))
        def cancelled(*args):
            self.service.update(rid,cancel_requested=1)
            return {'model':'gpt-6-astra','input':'must not submit'}
        with patch.object(self.service.context,'continuation',side_effect=cancelled),patch.object(self.service,'call') as paid:
            self.service.work_once()
        paid.assert_not_called()
        self.assertEqual(self.service.row(rid)['status'],'cancelled')

    def test_partial_provider_usage_is_not_reported_as_measured_zero(self):
        rid=self.turn().json['runId']
        self.service.context.record_response(self.service.row(rid),dict(id='usage_partial',usage={'input_tokens':100},output=[]))
        usage=self.service.snapshot(rid)['contextMetrics']['providerUsage']
        self.assertFalse(usage['complete'])
        self.assertNotIn('output_tokens',usage)
        self.assertNotIn('cached_tokens',usage)
        self.assertEqual(usage['input_tokens'],100)

    def test_changed_board_revision_drops_stale_provider_chain(self):
        rid=self.turn().json['runId']
        self.service.update(rid,status='completed',response_id='old_provider',text='old facts')
        self.client.put(self.board_path,headers=self.a,json={'revision':1,'project':{'nodes':[{'id':'new'}]}})
        next_id=self.turn('ctx-turn-2',previousRunId=rid,boardContext=dict(projectId=self.board,revision=2)).json['runId']
        with patch.object(self.service,'call_json') as paid:
            payload=self.service.prepare_payload(self.service.row(next_id),'sk-fake')
        paid.assert_not_called()
        self.assertNotIn('previous_response_id',payload)
        self.assertIn('old facts',json.dumps(payload))
        self.assertEqual(self.engine.calls[-1][2],2)



class RealContextIntegrationTests(unittest.TestCase):
    setUpClass=classmethod(test_venture.VentureTests.setUpClass.__func__)
    token=test_venture.VentureTests.token
    headers=test_venture.VentureTests.headers
    setUp=test_venture.VentureTests.setUp
    tearDown=test_venture.VentureTests.tearDown

    def board(self,snapshot):
        board='real_context_board_0001'
        self.service.context.engine.settings['debounceSeconds']=0
        self.service.context.settings['syncDebounceSeconds']=0
        result=self.client.put('/api/projects/'+board,headers=self.a,json={'revision':0,'project':snapshot})
        self.assertEqual(result.status_code,200,result.json)
        self.assertTrue(self.service.context.sync_once())
        self.assertTrue(self.service.context.engine.process_next())
        return board

    def submit_board(self,board,ci=False):
        opts=dict(clientRequestId='real-context-request',message='Reproduce every operation and all notes',model='gpt-6-astra',
                  runOptions={'codeInterpreter':ci},boardContext=dict(projectId=board,revision=1))
        result=self.client.post('/api/projects/'+self.cid+'/runs',headers=self.a,data={'options':json.dumps(opts)})
        self.assertEqual(result.status_code,202,result.json)
        return self.service.row(result.json['runId'])

    def test_real_engine_complete_records_and_authenticated_rebuild(self):
        records='OPN,WC,Description,Run Hrs,Notes\n'+'\n'.join(f'{30*i:04d},2CU0SA,Operation {i},{i}.25,Exact note {i}' for i in range(1,14))
        board=self.board(dict(mainPrompt='Keep exact values and complete operation groups.',nodes=[dict(id='operations',prompt='Read every record.',attachments=[dict(id='records',name='operations.csv',text=records,mime='text/csv')])],edges=[]))
        row=self.submit_board(board)
        with patch.object(self.service,'call') as paid,patch.object(self.service,'call_json') as uploaded:
            payload=self.service.prepare_payload(row,'sk-test')
        paid.assert_not_called();uploaded.assert_not_called()
        rendered=json.dumps(payload)
        for i in range(1,14):
            self.assertIn('Exact note '+str(i),rendered)
            self.assertIn(f'{30*i:04d}',rendered)
        path='/api/projects/'+board+'/context'
        self.assertEqual(self.client.post(path,headers=self.b,json={'action':'rebuild','revision':1}).status_code,404)
        self.assertEqual(self.client.post(path,headers=self.a,json={'action':'rebuild','revision':2}).status_code,409)
        rebuilt=self.client.post(path,headers=self.a,json={'action':'rebuild','revision':1})
        self.assertEqual(rebuilt.status_code,200,rebuilt.json)
        self.assertTrue(rebuilt.json['ready'])  # Previous valid generation remains readable.
        self.assertEqual(rebuilt.json['status'],'updating')

    def test_real_pdf_page_subprocess_and_complete_original_transfer(self):
        import base64
        import io
        try:
            from reportlab.pdfgen import canvas
            import pdfplumber
            import pypdfium2
        except ImportError:
            self.skipTest('Optional document processing dependencies unavailable')
        from pathlib import Path
        output=io.BytesIO();document=canvas.Canvas(output)
        document.drawString(30,700,'OPN 0030 WC 2CU0SA Run Hrs 1.50');document.save()
        settings=dict(enabled=True,pythonPath=sys.executable,profile='Standard')
        self.service.venture.documents.settings=settings
        self.service.context.engine.settings['documentSettings']=settings
        board=self.board(dict(nodes=[dict(id='pdf',attachments=[dict(id='source',name='operations.pdf',data='data:application/pdf;base64,'+base64.b64encode(output.getvalue()).decode())])],edges=[]))
        row=self.submit_board(board,ci=True)
        manifest=self.service.context.engine.status('firebase:alice',board,1)['manifest']
        source=manifest['sourceInventory'][0]['id']
        binding=self.service.context.binding(row)
        result,images=self.service.context.page(row,binding,source,0)
        self.assertEqual(result['page'],1)
        self.assertEqual(images[0]['content'][1]['type'],'input_image')
        with patch.object(self.service,'call_json',return_value={'id':'file_pdf_checked'}) as upload:
            transferred,extras=self.service.context.transfer(row,binding,source,'sk-test')
        self.assertTrue(transferred['transferred'])
        self.assertIn('not proven absent',transferred['validationNote'])
        self.assertTrue(transferred['sensitiveDataUnverified'])
        self.assertEqual(upload.call_args.args[2],'/files')


if __name__=='__main__':unittest.main()
