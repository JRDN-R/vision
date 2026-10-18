"""Real H.264/AAC playback, fresh gestures, exclusive playback and retry UI.
No account, server, or provider network calls; the preview route is a fixture.
"""
from pathlib import Path
import os
import subprocess
import tempfile
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
URL = 'https://video-preview.test/'
source = (ROOT/'web/video-preview.js').read_text()
player_code = source[source.index('function pauseOtherPCVideos'):source.index('function installPCVideoControls')]
css = (ROOT/'web/video-preview.css').read_text()
html = '''<html><meta charset="utf-8"><style>body{margin:0}.canvas-wrap{position:relative;width:180px;height:260px;display:inline-block}</style><style>'''+css+'''</style><body><script>
const pcVideoPlayers=new Map(),state={nodes:[]};let bad=false,downloads=0,revoked=0;
const revoke=URL.revokeObjectURL.bind(URL);URL.revokeObjectURL=url=>{revoked++;revoke(url);};
function pcVideoCurrent(project,n,id){return project===state&&state.nodes.includes(n)&&n.pcVideo.requestId===id;}
async function pcVideoRequest(n,suffix,options){downloads++;const r=await fetch(bad?'/bad.mp4':'/preview.mp4',{signal:options.signal});return r.blob();}
function toast(message){window.lastToast=message;}
function choosePCVideoAgain(){window.reprocessRequested=true;}
function installPCVideoControls(n){const wrap=document.getElementById(n.id);let b=wrap.querySelector('.pc-video-play');if(!b){b=document.createElement('button');b.className='pc-video-play';wrap.append(b);}b.hidden=false;b.disabled=false;b.textContent='Load preview';b.onclick=()=>playPCVideo(n,b);}
'''+player_code+'''
for(const id of ['one','two']){const n={id,title:id,src:'',pcVideo:{requestId:id}};state.nodes.push(n);const w=document.createElement('div');w.id=id;w.className='canvas-wrap';document.body.append(w);installPCVideoControls(n);}
</script></body></html>'''

with tempfile.TemporaryDirectory() as temp:
    movie=Path(temp)/'preview.mp4'
    h264_command=['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc2=size=270x480:rate=15','-f','lavfi','-i','sine=frequency=440:sample_rate=22050','-t','5','-c:v','libx264','-pix_fmt','yuv420p','-b:v','192k','-c:a','aac','-ac','1','-b:a','24k','-movflags','+faststart',str(movie)]
    with sync_playwright() as p:
        for browser_type in (p.chromium,p.webkit):
            if browser_type.name not in os.environ.get('VISION_TEST_BROWSERS','chromium,webkit').split(','):
                continue
            # Playwright's unbranded Chromium omits proprietary H.264/AAC codecs.
            # WebKit exercises the actual shipping codec; Chromium uses VP9/Opus
            # in the same MP4 container to exercise real playback/lifecycle.
            command=list(h264_command)
            if browser_type.name=='chromium':
                command[command.index('libx264')]='libvpx-vp9'
                command[command.index('aac')]='libopus'
                command[command.index('sine=frequency=440:sample_rate=22050')]='sine=frequency=440:sample_rate=48000'
            subprocess.run(command,check=True)
            browser=browser_type.launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':390,'height':844},has_touch=True,is_mobile=True)
                def route(r):
                    path=r.request.url
                    if path==URL:r.fulfill(content_type='text/html',body=html)
                    elif path.endswith('/preview.mp4'):r.fulfill(content_type='video/mp4',body=movie.read_bytes())
                    elif path.endswith('/bad.mp4'):r.fulfill(content_type='video/mp4',body=b'not a video')
                    else:r.abort()
                context.route('**/*',route)
                page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(URL)
                def load(id):
                    page.locator('#'+id+' .pc-video-play').click()
                    expect(page.locator('#'+id+' [role=status]')).to_have_text('Preview loaded. Tap to play.')
                    assert page.locator('#'+id+' video').evaluate('(v)=>v.paused && v.playsInline && v.currentTime===0')
                def play(id):
                    page.locator('#'+id+' .pc-video-notice button').first.click()
                    page.wait_for_function("id=>{const v=document.querySelector('#'+id+' video');return !v.paused&&v.currentTime>0;}",arg=id)
                load('one');play('one')
                load('two');assert page.locator('#one video').evaluate('(v)=>v.paused')
                play('two');assert page.locator('#one video').evaluate('(v)=>v.paused')
                # Native controls/resume also pause the other player.
                page.locator('#one video').evaluate('(v)=>v.play()')
                page.wait_for_function("document.querySelector('#two video').paused")
                # Policy rejection is visible and permits another fresh tap.
                page.evaluate("const v=document.querySelector('#two video');v.realPlay=v.play;v.play=()=>Promise.reject(new DOMException('Blocked','NotAllowedError'));pcVideoPlayers.get('two').notice.hidden=false;")
                page.locator('#two .pc-video-notice button').first.click()
                expect(page.locator('#two [role=status]')).to_have_text('Playback paused. Tap to play.')
                page.evaluate("const v=document.querySelector('#two video');v.play=v.realPlay;void 0;")
                play('two')
                page.locator('#two .pc-video-close').click()
                assert page.locator('#two video').count()==0
                assert page.evaluate('revoked')==1
                page.evaluate('bad=true')
                page.locator('#two .pc-video-play').click()
                expect(page.locator('#two [role=status]')).to_contain_text('could not decode')
                expect(page.locator('#two .pc-video-notice button').first).to_have_text('Retry preview')
                page.evaluate('bad=false')
                page.locator('#two .pc-video-notice button').first.click()
                expect(page.locator('#two [role=status]')).to_have_text('Preview loaded. Tap to play.')
                play('two')
                page.evaluate("for(const id of [...pcVideoPlayers.keys()])stopPCVideoPlayer(id)")
                assert page.locator('video').count()==0
                assert page.evaluate('pcVideoPlayers.size')==0
                assert not errors,errors
                print('PASS:',browser_type.name,'real MP4, fresh tap, exclusive playback, rejection recovery, decode retry, URL cleanup')
            finally:browser.close()
