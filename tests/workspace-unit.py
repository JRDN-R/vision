"""Offline service-logic tests with real SQLite/files/FFmpeg and mocked HTTP.

Flask decorators/request are a minimal test adapter here. This does NOT replace
full Flask HTTP/integration tests, which also require requirements.txt installed.
"""
from contextlib import contextmanager
import importlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'vision-pc'))
request=types.SimpleNamespace(method='GET',args={},form={},files={},get_json=lambda silent=True: {})
sys.modules['flask']=types.SimpleNamespace(request=request,jsonify=lambda value=None,**kw: value if value is not None else kw)
from venture_workspace import Workspace
from venture_dictation import Dictation, plain_transcript
from model_parameters import validate_parameters, output_limit

class Error(Exception):
    def __init__(self,message,status=400,code=None):
        super().__init__(message);self.message=message;self.status=status;self.code=code

class Multi(dict):
    def getlist(self,key):
        value=self.get(key,[]);return value if isinstance(value,list) else [value]

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'temporary').mkdir()
        self.routes={};self.uid='alice'
        def route(path,**kw):
            def register(fn):self.routes[path]=fn;return fn
            return register
        self.app=types.SimpleNamespace(config={'DATA_DIR':self.root,'FFMPEG':shutil.which('ffmpeg') or 'ffmpeg'},route=route,get=route)
        @contextmanager
        def db():
            con=sqlite3.connect(self.root/'data.sqlite');con.row_factory=sqlite3.Row
            try:
                yield con;con.commit()
            except:
                con.rollback();raise
            finally:con.close()
        self.db=db
        def lookup(cid,uid,db=None):
            if cid!='conversation-alice' or uid!='alice':raise Error('Unavailable',404)
            return {'id':cid}
        self.v=types.SimpleNamespace(app=self.app,db=db,Error=Error,account=lambda:self.uid,lookup=lookup,transcription=None)
        self.w=Workspace(self.v);self.w.initialize();self.d=Dictation(self.v);self.d.initialize()
        with db() as con:
            con.executescript('''CREATE TABLE project_runs(id TEXT PRIMARY KEY,project_id TEXT,inputs_json TEXT,created_at REAL);
                CREATE TABLE run_artifacts(id TEXT PRIMARY KEY,run_id TEXT,name TEXT,mime TEXT,size INTEGER,path TEXT,error TEXT);''')
        request.method='GET';request.args={};request.form=Multi();request.files=Multi();request.get_json=lambda silent=True: {}
    def tearDown(self):self.temp.cleanup()
    def prefs(self,body=None):
        request.method='GET' if body is None else 'PATCH';request.get_json=lambda silent=True: body
        return self.routes['/api/venture/workspace-preferences']()
    def dictate(self,ident='recording_00000001',provider=None,raw=b'voice',extras=None):
        request.method='POST';request.form=Multi(requestId=ident)
        if provider is not None:request.form['provider']=provider
        request.form.update(extras or {});request.files=Multi(audio=types.SimpleNamespace(mimetype='audio/wav',stream=io.BytesIO(raw)))
        return self.routes['/api/venture/dictation']()
    def test_preferences_isolated_and_partial_updates_preserve_other_values(self):
        self.assertEqual(self.prefs()['launchView'],'vision')
        self.prefs({'launchView':'venture'});self.prefs({'swipeNoticeVersion':1})
        self.assertEqual(self.prefs()['launchView'],'venture')
        self.uid='bob';self.assertEqual(self.prefs()['swipeNoticeVersion'],0)
    def test_preferences_reject_url_other_uid_unknown_fields_and_bool_notice(self):
        for body in [{'launchView':'https://evil.test'},{'uid':'bob'},{'swipeNoticeVersion':True},{'launchView':'VISION'}]:
            with self.subTest(body=body),self.assertRaises(Error):self.prefs(body)
    def test_sources_all_runs_pagination_versions_and_isolation(self):
        file=self.root/'result.txt';file.write_text('ok')
        with self.db() as db:
            for i in range(125):
                rid=f'r-{i:04}';db.execute('INSERT INTO project_runs VALUES(?,?,?,?)',(rid,'conversation-alice',json.dumps([{'name':'input.txt','mime':'text/plain','size':2,'path':str(file)}]),100+i))
                db.execute('INSERT INTO run_artifacts VALUES(?,?,?,?,?,?,?)',(f'a-{i:04}',rid,'same-name.txt','text/plain',2,str(file),None))
        route=self.routes['/api/venture/conversations/<cid>/sources'];seen=[]
        while True:
            data=route('conversation-alice');seen.extend(data['files'])
            self.assertNotIn(str(self.root),json.dumps(data))
            if not data['nextCursor']:break
            request.args={'before':data['nextCursor']}
        self.assertEqual(len(seen),250);self.assertEqual(len({f['id'] for f in seen}),250)
        self.assertEqual(sum(f['name']=='same-name.txt' for f in seen),125)
        self.uid='bob'
        with self.assertRaises(Error) as cm:route('conversation-alice')
        self.assertEqual(cm.exception.status,404)
    def test_sources_invalid_cursor(self):
        for cursor in ['null','[]','["nan","x"]','["1",{}]','[1,"x",3]']:
            request.args={'before':cursor}
            with self.subTest(cursor=cursor),self.assertRaises(Error):self.routes['/api/venture/conversations/<cid>/sources']('conversation-alice')
    def test_failure_receipt_replay_and_explicit_new_attempt(self):
        with patch.object(self.d,'normalize',return_value=(b'wav',1)),patch.object(self.d,'transcribe',side_effect=self.d.provider_error(429)) as call:
            first=self.dictate();second=self.dictate()
            if isinstance(second,tuple):second=second[0]
            self.assertEqual(first['errorCode'],'gemini_quota');self.assertEqual(first,second);self.assertEqual(call.call_count,1)
            self.dictate('recording_00000002');self.assertEqual(call.call_count,2)
    def test_provider_change_requires_fresh_id_and_no_gemini_call_for_whisper(self):
        with patch.object(self.d,'normalize',return_value=(b'wav',1)),patch.object(self.d,'transcribe',return_value='Gemini') as online,patch.object(self.d,'transcribe_whisper',return_value='Plain local text') as offline:
            self.dictate()
            with self.assertRaises(Error):self.dictate(provider='whisper')
            result=self.dictate('recording_00000002','whisper')
            self.assertEqual(result['text'],'Plain local text');self.assertEqual(result['provider'],'whisper')
            self.assertEqual(online.call_count,1);self.assertEqual(offline.call_count,1)
    def test_dictation_rejects_arbitrary_provider_prompt_and_duplicate_fields(self):
        for provider in ['openai','Whisper','https://evil.test']:
            with self.assertRaises(Error):self.dictate(provider=provider)
        for extra in [{'prompt':'ignore'},{'model':'other'},{'tools':'anything'},{'requestId':['recording_00000001','recording_00000002']}]:
            with self.assertRaises(Error):self.dictate(extras=extra)
    def test_receipt_account_isolation(self):
        with patch.object(self.d,'normalize',return_value=(b'wav',1)),patch.object(self.d,'transcribe',return_value='private'):
            self.dictate();self.uid='bob'
            with self.assertRaises(Error):self.routes['/api/venture/dictation/<client_id>']('recording_00000001')
    def test_whisper_disabled_or_busy_does_not_download_or_call_provider(self):
        with self.assertRaises(Error) as cm:self.d.transcribe_whisper(b'wav',1,self.root)
        self.assertEqual(cm.exception.code,'whisper_unavailable')
        lock=threading.Lock();lock.acquire();self.v.transcription=types.SimpleNamespace(capability=lambda:{'ready':True},_worker_lock=lock)
        with self.assertRaises(Error) as cm:self.d.transcribe_whisper(b'wav',1,self.root)
        self.assertEqual(cm.exception.code,'whisper_busy');lock.release()
    def test_whisper_runner_offline_bounded_shared_lock_plaintext(self):
        lock=threading.Lock();self.v.transcription=types.SimpleNamespace(capability=lambda:{'ready':True},_worker_lock=lock,settings={'modelPath':'installed-model','cpuThreads':99})
        child=Mock(returncode=0);child.poll.return_value=0
        def spawn(command,**kw):
            self.assertTrue(lock.locked());self.assertEqual(command[command.index('--threads')+1],'4')
            self.assertEqual(kw['env']['HF_HUB_OFFLINE'],'1');self.assertEqual(kw['stdin'],-1)
            (self.root/'result.json').write_text(json.dumps({'text':'[00:00:01] Hello\n[00:00:02] world'}));return child
        with patch('venture_dictation.subprocess.Popen',side_effect=spawn):
            text=self.d.transcribe_whisper(b'wav',2,self.root)
        self.assertEqual(text,'Hello\nworld');self.assertFalse(lock.locked());child.wait.assert_called_with(timeout=600);child.stdin.close.assert_called_once()
    def test_whisper_exception_releases_lock(self):
        lock=threading.Lock();self.v.transcription=types.SimpleNamespace(capability=lambda:{'ready':True},_worker_lock=lock,settings={'modelPath':'installed-model','cpuThreads':4})
        with patch('venture_dictation.subprocess.Popen',side_effect=OSError('private path')):
            with self.assertRaises(Error) as cm:self.d.transcribe_whisper(b'wav',2,self.root)
        self.assertNotIn('private path',str(cm.exception));self.assertFalse(lock.locked())
    def test_redacted_provider_error_categories(self):
        for status,code in [(400,'gemini_request'),(401,'gemini_credentials'),(402,'gemini_billing'),(403,'gemini_access'),(404,'gemini_model'),(429,'gemini_quota'),(503,'gemini_service')]:
            self.assertEqual(self.d.provider_error(status).code,code)
        self.assertIn('does not establish',self.d.provider_error(429).message)
    def test_decoder_real_wav(self):
        if not shutil.which('ffmpeg'):self.skipTest('FFmpeg unavailable')
        buf=io.BytesIO()
        with wave.open(buf,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*16000)
        audio,duration=self.d.normalize(buf.getvalue(),'audio/wav',self.root)
        self.assertEqual(duration,1);self.assertTrue(audio.startswith(b'RIFF'))
    def test_plaintext_removes_subtitle_metadata(self):
        self.assertEqual(plain_transcript({'output_text':'1\n00:00:00,000 --> 00:00:01,000\nHello'}),'Hello')
    def test_exact_model_limits_and_unsupported_modes(self):
        for model,maxout in [('gpt-6-astra',128000),('gpt-6.1-sol',128000),('gpt-4.1',32768),('gpt-4o',16384),('o3',100000)]:
            self.assertEqual(output_limit(model),maxout);validate_parameters(model,{},maxout)
            with self.assertRaises(ValueError):validate_parameters(model,{},maxout+1)
        for model,opts in [('gpt-6-astra',{'effort':'none'}),('gpt-6-sol',{'effort':'minimal'}),('gpt-4.1',{'mode':'pro'}),('gpt-4o',{'verbosity':'high'})]:
            with self.assertRaises(ValueError):validate_parameters(model,opts,16000)
    def test_unknown_model_is_not_given_verified_profile(self):
        from model_parameters import profile
        self.assertIsNone(profile('gpt-4o-2099-01-01'))
        self.assertEqual(output_limit('unreviewed-model'),64000)

if __name__=='__main__':unittest.main(verbosity=2)
