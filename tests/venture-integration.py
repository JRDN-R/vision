"""Hosted browser -> real Flask/FFmpeg/profile/storage -> mocked Gemini.
Uses Chromium's fake microphone, not Web Speech API or a text-only recorder stub.
No account credentials, real microphone or paid model request is used.
"""
from pathlib import Path
import importlib.util
import io
import json
import os
import sys
import threading
import time
from unittest.mock import patch
import wave
import base64

from PIL import Image
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server, WSGIRequestHandler
from werkzeug.wrappers import Response

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'vision-pc'))
import server
from test_venture_features import VentureFeatureTests, GeminiReply
spec=importlib.util.spec_from_file_location('venture_browser_fixture',ROOT/'tests/venture-smoke.py')
fixture_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture_module)
fixture=fixture_module.fixture

class QuietHandler(WSGIRequestHandler):
    def log(self,*args,**kwargs):pass


def run():
    VentureFeatureTests.setUpClass();test=VentureFeatureTests();test.setUp();test.enable_dictation()
    test.complete(test.submit().json['runId'])
    posts=[]
    def gemini(url,**kwargs):
        posts.append(kwargs['json'])
        return GeminiReply('The dictation is plain text, with no subtitle timestamps.')
    def application(environ,start_response):
        if environ.get('PATH_INFO')=='/':return Response(fixture,mimetype='text/html')(environ,start_response)
        return server.app(environ,start_response)
    http=make_server('127.0.0.1',0,application,threaded=True,request_handler=QuietHandler)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{http.server_port}/'
    token=test.token('alice')
    try:
        with patch('venture_dictation.requests.post',side_effect=gemini),sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,args=['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream'],
                **({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
            try:
                context=browser.new_context(viewport={'width':390,'height':844},permissions=['microphone'])
                context.add_init_script('window.ventureHTTPToken='+json.dumps(token)+';')
                page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(url);page.locator('#runProjectBtn').click()
                page.wait_for_function('venture.ready')
                assert page.locator('.venture-monocle image').count()>=2
                assert 'Vision Venture' not in page.locator('#visionVenture').inner_text()
                page.locator('#ventureMessage').fill('Existing draft.')
                page.locator('#ventureMic').click()
                page.wait_for_function("ventureRecording?.phase==='recording'")
                page.wait_for_timeout(1500)
                assert page.locator('#ventureWaveform').is_visible()
                assert page.evaluate("ventureRecording.analyser instanceof AnalyserNode")
                assert page.evaluate("Array.from(ventureRecording.samples).some(n=>Math.abs(n-128)>1)"),'The waveform did not receive microphone samples'
                page.screenshot(path=str(ROOT/'tests/venture-screenshots/venture-dictation-390.png'))
                assert page.locator('#ventureSend').is_disabled()
                page.locator('#ventureMic').click()
                page.wait_for_function('ventureRecording===null',timeout=15000)
                assert page.locator('#ventureMessage').input_value()=='Existing draft. The dictation is plain text, with no subtitle timestamps.'
                assert len(posts)==1
                assert '-->' not in page.locator('#ventureMessage').input_value()
                assert page.evaluate("venture.current===null"),'Dictation must not automatically submit a conversation'
                assert page.evaluate("$('ventureMic').onclick===ventureDictate")
                assert test.client.get('/api/gemini/access',headers=test.a).json['status']=='unrequested'
                # Browser timers: advance three minutes without a real three-minute wait.
                page.clock.install()
                page.locator('#ventureMic').click()
                page.wait_for_function("ventureRecording?.phase==='recording'")
                page.evaluate('window.lastMicStream=ventureRecording.stream;window.lastMicRecorder=ventureRecording.recorder;')
                # Fast forward to the cut-off. Native capture remains real; backend
                # duration cap is separately tested with an actual 181-second WAV.
                page.clock.fast_forward(180000)
                assert page.evaluate("window.lastMicRecorder.state==='inactive'")
                assert page.evaluate("window.lastMicStream.getTracks().every(t=>t.readyState==='ended')")
                page.wait_for_function('ventureRecording===null',timeout=15000)
                assert len(posts)==2
                assert not page.locator('#ventureDictation').is_visible()
                assert 'minute' not in page.locator('#ventureNotice').inner_text().lower()
                assert 'limit' not in page.locator('#ventureNotice').inner_text().lower()
                # Real profile upload via browser canvas -> server crop/re-encode.
                page.locator('#ventureAvatar').click()
                image=Image.new('RGB',(1000,500),(90,150,40));buf=io.BytesIO();image.save(buf,format='PNG')
                page.locator('#ventureAvatarInput').set_input_files({'name':'photo.png','mimeType':'image/png','buffer':buf.getvalue()})
                page.wait_for_function("$('ventureAvatarStatus').textContent.includes('saved')")
                assert page.locator('#ventureAvatar img').get_attribute('src').startswith('blob:')
                page.locator('#ventureShowStorage').click()
                page.wait_for_function("$('ventureStoragePath').textContent.includes('conversations')")
                assert str(test.venture.user_root('firebase:alice')) in page.locator('#ventureStoragePath').inner_text()
                page.locator('#ventureAccountClose').click()
                # Custom avatar survives reload and stays isolated from the other user.
                page.reload();page.locator('#runProjectBtn').click();page.wait_for_function('!!ventureAvatarObjectURL')
                assert not test.client.get('/api/venture/profile',headers=test.b).json['hasAvatar']
                # Real optional memory setting plus real conversation deletion.
                page.locator('#ventureHistoryToggle').click()
                page.locator('[data-conversation="'+test.cid+'"]').first.click()
                page.wait_for_function('!!venture.current')
                page.locator('#ventureSettingsToggle').click();page.locator('#ventureMemory').check()
                page.wait_for_function("$('ventureSettingsStatus').textContent.includes('Saved')")
                assert test.client.get(test.path,headers=test.a).json['conversation']['settings']['memoryEnabled'] is True
                page.locator('#ventureSettingsClose').click()
                page.locator('#ventureHistoryToggle').click();page.locator('[data-rename="'+test.cid+'"]').click()
                page.on('dialog',lambda dialog:dialog.accept())
                page.locator('#ventureDeleteConversation').click();page.wait_for_function("!$('ventureRenameDialog').open")
                assert test.client.get(test.path,headers=test.a).status_code==404
                assert page.locator('[data-conversation="'+test.cid+'"]').count()==0
                assert page.evaluate('venture.current===null')
                assert not errors,errors
                context.close()
                print('Hosted integration passed: real microphone capture/waveform, server FFmpeg -> Gemini text adapter, silent 180-second timer, draft preservation/no auto-send, profile crop/upload/reload, storage paths, memory settings, conversation deletion and unchanged project Gemini approval.')
            finally:browser.close()
    finally:
        http.shutdown();thread.join(timeout=5);test.tearDown()

if __name__=='__main__':run()
