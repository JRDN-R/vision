"""Signed-account integration tests; real audio/image processing, fake Gemini transport."""
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import wave

from PIL import Image
import requests
import server
import sessions
import test_venture
from venture_dictation import MODEL, MAX_SECONDS, plain_transcript
from venture_profile import compress_avatar


def wav_bytes(seconds=0.15):
    out=io.BytesIO()
    with wave.open(out,'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000)
        audio.writeframes(b'\x00\x01'*int(seconds*16000))
    return out.getvalue()


class GeminiReply:
    status_code=200
    def __init__(self,text='Please check the wing measurements.',status=200):
        self.status_code=status
        self.data={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':text}]}],
                   'usage':{'total_input_tokens':15,'total_output_tokens':8}}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def iter_content(self,n):yield json.dumps(self.data).encode()


class VentureFeatureTests(unittest.TestCase):
    setUpClass=classmethod(test_venture.VentureTests.setUpClass.__func__)
    token=test_venture.VentureTests.token
    headers=test_venture.VentureTests.headers
    setUp=test_venture.VentureTests.setUp
    tearDown=test_venture.VentureTests.tearDown
    submit=test_venture.VentureTests.submit
    complete=test_venture.VentureTests.complete
    funding=test_venture.VentureTests.funding

    def enable_dictation(self):
        ffmpeg=shutil.which('ffmpeg')
        if not ffmpeg:self.skipTest('FFmpeg required for real dictation decoding')
        server.app.config['FFMPEG']=ffmpeg
        credentials=Mock();credentials.available.return_value=True;credentials.get.return_value='TEST-ONLY-NOT-A-REAL-KEY'
        server.app.config['GEMINI_CREDENTIALS']=credentials
        return credentials

    def dictate(self,raw=None,mime='audio/wav',ident='dictation-request-0001',headers=None,extra=None):
        return self.client.post('/api/venture/dictation',headers=headers or self.a,
            data={'requestId':ident,'audio':(io.BytesIO(raw if raw is not None else wav_bytes()),'microphone',mime),**(extra or {})})

    def post_turn(self,cid=None,message='What were the cobalt wing measurements?',memory=False,previous=None):
        options={'clientRequestId':'turn-'+str(time.time_ns()),'message':message,
                 'memoryEnabled':memory,'runOptions':{'codeInterpreter':False}}
        if previous:options['previousRunId']=previous
        result=self.client.post('/api/projects/'+(cid or self.cid)+'/runs',headers=self.a,data={'options':json.dumps(options)})
        self.assertEqual(result.status_code,202,result.json)
        return result.json['runId']

    def new_conversation(self,cid,headers=None):
        result=self.client.post('/api/venture/conversations',headers=headers or self.a,json={'id':cid})
        self.assertEqual(result.status_code,201,result.json)
        return cid

    def test_dictation_all_accounts_without_project_approval_and_plain_text(self):
        self.enable_dictation()
        with patch('venture_dictation.requests.post',return_value=GeminiReply()) as transport:
            for headers in (self.a,self.b):
                before=self.client.get('/api/gemini/access',headers=headers).json
                capability=self.client.get('/api/venture/dictation',headers=headers).json
                self.assertTrue(capability['allowed']);self.assertTrue(capability['ready'])
                self.assertFalse(capability['projectAccessGranted'])
                result=self.dictate(headers=headers)
                self.assertEqual(result.status_code,200,result.json)
                self.assertEqual(result.json['text'],'Please check the wing measurements.')
                self.assertEqual(result.json['format'],'text')
                self.assertEqual(result.json['status'],'completed')
                self.assertEqual(self.client.get('/api/gemini/access',headers=headers).json,before)
            self.assertEqual(transport.call_count,2)
            body=transport.call_args.kwargs['json']
            self.assertEqual(body['model'],MODEL);self.assertIs(body['store'],False)
            self.assertEqual(body['generation_config'],{'transcription_config':{'mode':{'type':'verbatim'}}})
            self.assertEqual(set(body),{'model','store','input','generation_config'})
            self.assertEqual(len(body['input']),1);self.assertEqual(body['input'][0]['type'],'audio')
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gemini_access').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gemini_usage').fetchone()[0],2)
        self.assertFalse(list((server.app.config['DATA_DIR']/'temporary').glob('venture-dictation-*')))

    def test_dictation_never_unlocks_project_gemini_processing(self):
        self.enable_dictation()
        with patch('venture_dictation.requests.post',return_value=GeminiReply()) as transport:
            self.assertEqual(self.dictate().json['status'],'completed')
            path='/api/projects/project_separate_gate_01'
            self.assertEqual(self.client.put(path,headers=self.a,json={'revision':0,'project':{'title':'Restricted','nodes':[]}}).status_code,200)
            body={'clientRequestId':'project-gemini-001','provider':'gemini','sourceName':'speech.wav',
                  'sections':[{'start':0,'end':1,'mimeType':'audio/wav','audioData':'data:audio/wav;base64,'+base64.b64encode(wav_bytes()).decode()}]}
            result=self.client.post(path+'/transcriptions',headers=self.a,json=body)
            self.assertEqual(result.status_code,202,result.json)
            self.assertEqual(result.json['status'],'approval_waiting')
            self.assertEqual(self.client.get('/api/gemini/access',headers=self.a).json['status'],'pending')
            self.dictate(ident='dictation-after-pending-001')
            self.assertEqual(self.client.get('/api/gemini/access',headers=self.a).json['status'],'pending')
            with patch.object(server.transcriptions,'run_gemini') as runner:
                self.assertFalse(server.transcriptions.work_once());runner.assert_not_called()
            self.assertEqual(transport.call_count,2)

    def test_dictation_server_caps_audio_at_three_minutes(self):
        self.enable_dictation()
        with patch('venture_dictation.requests.post',return_value=GeminiReply()) as transport:
            result=self.dictate(wav_bytes(181.25))
            self.assertEqual(result.json['status'],'completed',result.json)
            data=base64.b64decode(transport.call_args.kwargs['json']['input'][0]['data'])
            with wave.open(io.BytesIO(data),'rb') as audio:
                self.assertEqual((audio.getnchannels(),audio.getframerate(),audio.getsampwidth()),(1,16000,2))
                self.assertEqual(audio.getnframes()/audio.getframerate(),MAX_SECONDS)
            self.assertNotIn('warning',result.json)

    def test_webm_and_safari_mp4_recordings_decode(self):
        self.enable_dictation()
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'input.wav';source.write_bytes(wav_bytes())
            for extension,mime,codec in [('webm','audio/webm','libopus'),('m4a','audio/mp4','aac')]:
                target=Path(temp)/('recording.'+extension)
                subprocess.run([server.app.config['FFMPEG'],'-v','error','-i',str(source),'-c:a',codec,str(target)],check=True)
                with patch('venture_dictation.requests.post',return_value=GeminiReply()) as transport:
                    response=self.dictate(target.read_bytes(),mime,ident='dictation-format-'+extension)
                    self.assertEqual(response.json['status'],'completed',response.json)
                    self.assertEqual(transport.call_count,1)

    def test_dictation_rejects_prompt_tools_models_and_url_inputs(self):
        self.enable_dictation()
        with patch('venture_dictation.requests.post') as transport:
            for key in ['prompt','model','tools','projectId','url','generation_config']:
                self.assertEqual(self.dictate(extra={key:'ignored?'}).status_code,400,key)
            self.assertEqual(self.dictate(mime='text/plain').status_code,415)
            self.assertEqual(self.dictate(ident='../escape').status_code,400)
            self.assertEqual(self.client.post('/api/venture/dictation',json={'audio':'https://example.org'}).status_code,401)
            transport.assert_not_called()

    def test_receipt_replay_is_idempotent_and_owner_scoped(self):
        self.enable_dictation()
        with patch('venture_dictation.requests.post',return_value=GeminiReply()) as transport:
            first=self.dictate();second=self.dictate()
            self.assertEqual(first.json,second.json);self.assertEqual(transport.call_count,1)
            self.assertEqual(self.dictate(wav_bytes(.2)).status_code,409)
            self.assertEqual(self.client.get('/api/venture/dictation/dictation-request-0001',headers=self.b).status_code,404)
            self.assertEqual(self.client.get('/api/venture/dictation/dictation-request-0001',headers=self.a).json['text'],first.json['text'])

    def test_provider_error_and_interruption_never_replay_or_leak(self):
        self.enable_dictation()
        with patch('venture_dictation.requests.post',side_effect=requests.ConnectionError('SECRET-KEY-IN-EXCEPTION')) as transport:
            result=self.dictate()
            self.assertEqual(result.json['status'],'error');self.assertNotIn('SECRET',result.get_data(as_text=True))
            self.dictate();self.assertEqual(transport.call_count,1)
        with server.connect_db() as db:
            db.execute("UPDATE venture_dictation_requests SET status='processing'")
        self.venture.dictation.initialize()
        with patch('venture_dictation.requests.post') as transport:
            self.assertEqual(self.dictate().json['status'],'interrupted');transport.assert_not_called()
        self.assertFalse(list((server.app.config['DATA_DIR']/'temporary').glob('venture-dictation-*')))

    def test_srt_is_converted_to_text_and_draft_receipts_expire(self):
        self.enable_dictation()
        srt='1\n00:00:00,000 --> 00:00:01,000\nHello there.\n\n2\n00:00:01,000 --> 00:00:02,000\nCheck the wing.'
        self.assertNotIn('-->',plain_transcript({'output_text':srt}))
        self.assertNotIn('\n2\n',plain_transcript({'output_text':srt}))
        with patch('venture_dictation.requests.post',return_value=GeminiReply(srt)):
            self.assertEqual(self.dictate().json['status'],'completed')
        with server.connect_db() as db:db.execute('UPDATE venture_dictation_requests SET updated_at=?',(time.time()-1000,))
        server.prune_expired()
        with server.connect_db() as db:
            row=db.execute('SELECT * FROM venture_dictation_requests').fetchone()
            self.assertEqual(row['text'],'');self.assertEqual(row['status'],'expired')
        self.assertEqual(self.client.get(self.path,headers=self.a).json['runs'],[])

    def test_invalid_audio_is_not_sent_to_gemini_and_temp_files_removed(self):
        self.enable_dictation()
        with patch('venture_dictation.requests.post') as transport:
            response=self.dictate(b'not really audio')
            self.assertEqual(response.json['status'],'error');transport.assert_not_called()
        self.assertFalse(list((server.app.config['DATA_DIR']/'temporary').glob('venture-dictation-*')))

    def test_avatar_crop_compression_metadata_and_account_isolation(self):
        image=Image.new('RGB',(1400,600),'green');image.paste('red',(0,0,300,600));image.paste('blue',(1100,0,1400,600))
        exif=Image.Exif();exif[270]='PRIVATE UPLOAD METADATA';out=io.BytesIO();image.save(out,format='JPEG',exif=exif)
        response=self.client.put('/api/venture/profile',headers=self.a,data={'avatar':(io.BytesIO(out.getvalue()),'../photo.jpg')})
        self.assertEqual(response.status_code,200,response.json);self.assertTrue(response.json['hasAvatar'])
        self.assertFalse(self.client.get('/api/venture/profile',headers=self.b).json['hasAvatar'])
        self.assertEqual(self.client.get('/api/venture/profile/avatar',headers=self.b).status_code,404)
        result=self.client.get('/api/venture/profile/avatar',headers=self.a)
        with Image.open(io.BytesIO(result.data)) as cropped:
            self.assertEqual(cropped.size,(256,256));self.assertEqual(cropped.format,'WEBP')
            self.assertFalse(cropped.getexif());self.assertGreater(cropped.getpixel((10,10))[1],90)
        self.assertLess(len(result.data),64000);self.assertNotIn(b'PRIVATE',result.data);result.close()
        self.assertEqual(self.client.delete('/api/venture/profile',headers=self.a).status_code,200)
        self.assertEqual(self.client.get('/api/venture/profile/avatar',headers=self.a).status_code,404)

    def test_avatar_rejects_svg_and_disguised_nonimage(self):
        for raw in (b'<svg onload="alert(1)"></svg>',b'not an image'):
            response=self.client.put('/api/venture/profile',headers=self.a,data={'avatar':(io.BytesIO(raw),'photo.png','image/png')})
            self.assertEqual(response.status_code,415,response.json)
        self.assertEqual(self.client.get('/api/venture/profile').status_code,401)

    def test_storage_paths_are_the_actual_configured_user_roots(self):
        result=self.client.get('/api/venture/storage',headers=self.a)
        root=server.app.config['DATA_DIR']/'users'/hashlib.sha256(b'firebase:alice').hexdigest()
        self.assertEqual(result.json['conversationsDirectory'],str(root/'conversations'))
        self.assertEqual(result.json['identityPath'],str(root/'user.json'))
        self.assertEqual(result.json['avatarPath'],str(root/'profile'/'avatar.webp'))
        self.assertEqual(result.json['databasePath'],str(server.app.config['DATABASE']))
        self.assertEqual(self.client.get('/api/venture/storage').status_code,401)

    def test_delete_erases_history_files_memory_and_cannot_recreate_id(self):
        rid=self.submit(files=True).json['runId'];self.complete(rid)
        root=self.venture.root(self.cid);working=server.app.config['DATA_DIR']/'projects'/self.cid
        working.mkdir(parents=True,exist_ok=True);(working/'old-working-copy.txt').write_text('old')
        self.assertTrue(root.exists());self.assertTrue(working.exists())
        self.assertEqual(self.client.delete(self.path,headers=self.b).status_code,404)
        result=self.client.delete(self.path,headers=self.a)
        self.assertEqual(result.status_code,200,result.json);self.assertTrue(result.json['deleted'])
        self.assertFalse(root.exists());self.assertFalse(working.exists())
        self.assertEqual(self.client.get(self.path,headers=self.a).status_code,404)
        self.assertEqual(self.client.get('/api/projects/'+self.cid+'/runs/'+rid,headers=self.a).status_code,404)
        self.assertEqual(self.client.get('/api/venture/conversations',headers=self.a).json['conversations'],[])
        self.assertEqual(self.client.post('/api/venture/conversations',headers=self.a,json={'id':self.cid}).status_code,410)
        self.assertEqual(self.client.delete(self.path,headers=self.a).status_code,200)
        self.assertEqual(self.submit(request_id='a-late-tab-new-run-01').status_code,404)
        with server.connect_db() as db:
            for table in ['project_runs','run_artifacts','run_events','venture_memory','venture_memory_indexed']:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0,table)
        self.venture.initialize()
        self.assertEqual(self.client.get('/api/venture/conversations',headers=self.a).json['conversations'],[])

    def test_active_delete_is_refused_and_stopping_allows_delete(self):
        rid=self.submit().json['runId']
        self.assertEqual(self.client.delete(self.path,headers=self.a).status_code,409)
        self.service.update(rid,status='cancelled')
        self.assertEqual(self.client.delete(self.path,headers=self.a).status_code,200)

    def test_delete_keeps_funding_debit_and_owner_profile(self):
        self.funding();rid=self.submit().json['runId'];self.complete(rid)
        before=self.client.get('/api/venture/funding',headers=self.a).json
        self.client.delete(self.path,headers=self.a)
        after=self.client.get('/api/venture/funding',headers=self.a).json
        self.assertEqual(before['fraction'],after['fraction'])
        self.assertTrue((self.venture.user_root('firebase:alice')/'user.json').exists())

    def test_delete_imported_conversation_preserves_board_project(self):
        cid='project_imported_board_01';path='/api/projects/'+cid
        self.client.put(path,headers=self.a,json={'revision':0,'project':{'title':'Keep my board','nodes':[]}})
        self.venture.ensure(cid)
        self.assertEqual(self.client.delete('/api/venture/conversations/'+cid,headers=self.a).status_code,200)
        self.assertEqual(self.client.get(path,headers=self.a).status_code,200)
        self.assertEqual(self.client.get(path+'/runs',headers=self.a).status_code,404)
        self.venture.initialize()
        ids=[c['id'] for c in self.client.get('/api/venture/conversations',headers=self.a).json['conversations']]
        self.assertNotIn(cid,ids)

    def test_memory_opt_in_matches_older_conversations_and_never_other_account(self):
        source=self.new_conversation('venture_cobalt_source_01')
        rid=self.post_turn(source,message='The cobalt wing measurement is 0.379 inch.');self.complete(rid)
        bob=self.new_conversation('venture_bob_private_001',self.b)
        bobturn=self.client.post('/api/projects/'+bob+'/runs',headers={**self.b,'X-OpenAI-Key':'sk-test-bob'},
            data={'options':json.dumps({'clientRequestId':'bob-private-turn-001','message':'My secret cobalt code 98437','runOptions':{'codeInterpreter':False}})}).json['runId']
        self.complete(bobturn)
        for i in range(52):self.new_conversation('venture_extra_'+str(i).zfill(10))
        rid=self.post_turn(memory=False)
        with patch.object(self.service,'call_json',side_effect=AssertionError('Unexpected API call')):
            payload=self.service.prepare_payload(self.service.row(rid),'sk-test')
        self.assertNotIn('0.379',str(payload));self.complete(rid)
        follow=self.post_turn(memory=True)
        with patch.object(self.service,'call_json',return_value={'id':'resp_previous'}):
            payload=self.service.prepare_payload(self.service.row(follow),'sk-test')
        serialized=json.dumps(payload)
        self.assertIn('0.379',serialized);self.assertNotIn('98437',serialized)
        self.assertIn('quoted background',payload['instructions'])
        self.assertIn('Not a complete reading',serialized)
        snapshot=self.service.snapshot(follow)
        self.assertTrue(snapshot['memoryEnabled']);self.assertEqual(snapshot['memorySources'][0]['conversationId'],source)
        saved=self.service.row(follow)['venture_memory_sources'];self.assertNotIn('0.379',saved)

    def test_deleted_memory_not_retrieved_and_stale_upstream_context_is_not_reused(self):
        source=self.new_conversation('venture_deleted_memory_01')
        rid=self.post_turn(source,message='The cobalt wing measurement is 0.379 inch.');self.complete(rid)
        turn=self.post_turn(memory=True)
        with patch.object(self.service,'call_json',return_value={'id':'response-old'}):
            self.assertIn('0.379',str(self.service.prepare_payload(self.service.row(turn),'sk-test')))
        self.complete(turn)
        self.assertEqual(self.client.delete('/api/venture/conversations/'+source,headers=self.a).status_code,200)
        self.assertEqual(self.service.snapshot(turn)['memorySources'],[])
        for enable in (False,True):
            follow=self.post_turn(memory=enable,previous=turn)
            with patch.object(self.service,'call_json',side_effect=AssertionError('Must not reuse an old upstream chain')):
                payload=self.service.prepare_payload(self.service.row(follow),'sk-test')
            self.assertNotIn('previous_response_id',payload);self.assertNotIn('0.379',str(payload))
            self.complete(follow)

    def test_memory_flag_is_validated_and_settings_persist(self):
        response=self.client.patch(self.path,headers=self.a,json={'revision':1,'settings':{'memoryEnabled':True}})
        self.assertEqual(response.status_code,200,response.json);self.assertTrue(response.json['conversation']['settings']['memoryEnabled'])
        result=self.client.post('/api/projects/'+self.cid+'/runs',headers=self.a,data={'options':json.dumps({'clientRequestId':'bad-memory-flag-001','message':'Hi','memoryEnabled':'true'})})
        self.assertEqual(result.status_code,400)

if __name__=='__main__':unittest.main()
