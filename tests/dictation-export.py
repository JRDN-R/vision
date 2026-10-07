"""Actual bundled offline FFmpeg -> MP3 export; network and providers are unused."""
import base64
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import wave
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('venture_fixture', ROOT/'tests/venture-smoke.py')
fixture_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture_module)
source = (ROOT/'Vision.html').read_text()
assets = ''.join(re.search(r'<script\b[^>]*\bid="'+name+r'"[^>]*>[\s\S]*?</script>', source)[0]
                 for name in ('ffmpeg-wasm-source','ffmpeg-core-source','fvad-core-source','decoder-source'))
base = (ROOT/'web/base.js').read_text()
helpers = '\n'.join(next(line for line in base.splitlines() if line.startswith(prefix))
                    for prefix in ('async function embeddedBytes(', 'function decoderClient('))
fixture = fixture_module.fixture.replace('<body>', '<body>'+assets)
fixture = fixture.replace('</script></body>', '\n'+helpers+'</script></body>')
wave_data = io.BytesIO()
with wave.open(wave_data,'wb') as audio:
    audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
    audio.writeframes(b''.join(struct.pack('<h',int(9000*math.sin(2*math.pi*440*i/16000))) for i in range(32000)))
encoded = base64.b64encode(wave_data.getvalue()).decode()
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, **({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
    context = browser.new_context(viewport={'width':390,'height':844},accept_downloads=True)
    context.route('**/*',lambda route:route.abort())
    page = context.new_page(); errors=[]; page.on('pageerror', lambda error:errors.append(str(error)))
    page.set_content(fixture)
    page.evaluate('''data=>{
      venture.open=true;venture.ready=true;document.getElementById('visionVenture').hidden=false;
      const rec={epoch:venture.epoch,selection:venture.selection,phase:'failed',cancelled:false,
        audio:new Blob([Uint8Array.from(atob(data),x=>x.charCodeAt(0))],{type:'audio/wav'})};
      ventureRecording=rec;ventureDictationShowRecovery(rec,'Transcription failed. Save your audio.');
    }''', encoded)
    page.locator('#ventureDictationSave').click()
    page.wait_for_function("document.getElementById('ventureDictationSave').textContent==='Save audio (.mp3)'",timeout=45000)
    with page.expect_download() as download:
        page.locator('#ventureDictationSave').click()
    assert download.value.suggested_filename.endswith('.mp3')
    mp3=Path(download.value.path());assert mp3.stat().st_size>1000
    details=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(mp3)]))
    assert details['format']['format_name']=='mp3'
    assert 1.9<=float(details['format']['duration'])<=2.5
    assert details['streams'][0]['sample_rate']=='16000'
    page.screenshot(path=str(ROOT/'tests/venture-screenshots/dictation-mp3-390.png'))
    # If conversion fails, the original remains downloadable, not renamed to MP3.
    page.evaluate('''()=>{ventureRecording.mp3=null;decoderClient=()=>{throw new Error('fixture conversion unavailable')};}''')
    page.locator('#ventureDictationSave').click()
    page.wait_for_function("document.getElementById('ventureDictationStatus').textContent.includes('Save the original')")
    with page.expect_download() as original:
        page.locator('#ventureDictationOriginal').click()
    assert original.value.suggested_filename.endswith('.wav')
    assert Path(original.value.path()).read_bytes()==wave_data.getvalue()
    assert not errors, errors
    context.close();browser.close()
print('Offline MP3 export passed: real embedded FFmpeg, valid 16kHz MP3 with the recorded duration, fresh download click, no network, unchanged original on conversion failure.')
