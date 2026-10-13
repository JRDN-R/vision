"""Exercise real Lottie and response DOM across frequent server snapshots, offline."""
import os
import runpy
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
fixture=runpy.run_path(str(ROOT/'tests/venture-smoke.py'),run_name='motion_fixture')['fixture']
controller=runpy.run_path(str(ROOT/'web/build.py'))['qwen_animation_script']()
fixture=fixture.replace('<script>', '<script>'+(ROOT/'web/vendor/lottie_svg.min.js').read_text()+'\n'+controller+'\n',1)
with sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True,**({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
 for width in (390,1280):
  page=browser.new_page(viewport={'width':width,'height':844});errors=[]
  page.on('pageerror',lambda error:errors.append(str(error)))
  page.route('**/*',lambda route:route.abort())
  page.set_content(fixture)
  page.evaluate('''()=>{
   venture.open=true;$('visionVenture').hidden=false;
   window.run={runId:'motion',status:'in_progress',phase:'Checking installed models',model:'qwen3.8-local',text:'',message:'Explain how this works.',localQueue:{processingSeconds:10,waitingSeconds:0,estimateStatus:'learning'}};
   venture.runs=[run];venturePaintTurns();
  }''')
  page.wait_for_function("document.querySelector('venture-loading-symbol')?.ready && document.querySelector('#ventureTurns venture-qwen-symbol')?.ready")
  page.evaluate('''()=>{
   window.badge=document.querySelector('#ventureTurns venture-qwen-symbol');
   window.spinner=document.querySelector('venture-loading-symbol');
   window.order=[];window.loops=0;
   badge.players.forEach((p,i)=>{p.setSpeed(6);p.addEventListener('complete',()=>order.push(i));});
   spinner.players[0].setSpeed(6);spinner.players[0].addEventListener('loopComplete',()=>loops++);
   window.polling=setInterval(()=>{run.localQueue.processingSeconds++;run.sequence=(run.sequence||0)+1;venturePaintTurns();},180);
  }''')
  page.wait_for_function('order.length>=4 && loops>=2')
  assert page.evaluate('order.slice(0,4)')==[0,1,0,1]
  assert page.evaluate("badge===document.querySelector('#ventureTurns venture-qwen-symbol') && spinner===document.querySelector('venture-loading-symbol')")
  assert 'Checking installed' not in page.locator('#ventureTurns').inner_text()
  assert 'Learning response' not in page.locator('#ventureTurns').inner_text()
  assert page.locator('.venture-progress-track').get_attribute('aria-valuenow') is None
  page.evaluate("run.localQueue={processingSeconds:60,waitingSeconds:0,estimateStatus:'estimated',estimatedRemainingRange:[20,60]};venturePaintTurns();")
  assert page.locator('.venture-progress-track').get_attribute('aria-valuenow')=='50'
  page.evaluate("run.localQueue.estimateStatus='overrun';venturePaintTurns();")
  assert page.locator('.venture-progress-track').get_attribute('aria-valuenow') is None
  page.evaluate("clearInterval(polling);run.text='A calm answer arrives in a smooth flow, with **clear emphasis** and a useful explanation. '.repeat(5);venturePaintTurns();")
  page.wait_for_function("document.querySelector('[data-venture-text]').textContent.length>5")
  assert page.evaluate("document.querySelector('[data-venture-text]').ventureReveal.shown.length<run.text.length")
  page.wait_for_function("document.querySelector('[data-venture-text]').ventureReveal.shown===run.text")
  page.evaluate("window.firstWord=document.querySelector('.venture-reveal-word');run.text+='\\n\\n## Details\\n\\n- First item\\n- Second item\\n\\n```js\\nconst answer = 42;\\n```';run.status='completed';venturePaintTurns();")
  page.wait_for_function("document.querySelector('[data-venture-text]').ventureReveal.shown===run.text")
  assert page.evaluate("firstWord===document.querySelector('.venture-reveal-word')")
  assert page.locator('[data-venture-text] strong').count()>0
  assert page.locator('[data-venture-text] pre').inner_text()=='const answer = 42;'
  assert page.locator('[data-venture-text] li').count()==2
  assert page.locator('venture-loading-symbol').count()==0
  assert page.evaluate("badge===document.querySelector('#ventureTurns venture-qwen-symbol')")
  page.wait_for_timeout(400)
  assert page.evaluate('firstWord.getAnimations().length')==0
  # Previously completed content opens instantly; corrections replace stale text.
  page.evaluate("venture.rendered.clear();$('ventureTurns').replaceChildren();venturePaintTurns();")
  assert page.evaluate("document.querySelector('[data-venture-text]').ventureReveal.shown===run.text")
  page.evaluate("run.text='Corrected answer';venturePaintTurns();")
  assert page.locator('[data-venture-text]').inner_text()=='Corrected answer'
  page.emulate_media(reduced_motion='reduce')
  page.evaluate("run.status='in_progress';run.text+=' with no animation.';venturePaintTurns();")
  assert page.locator('[data-venture-text]').inner_text()=='Corrected answer with no animation.'
  page.wait_for_function("document.querySelector('venture-loading-symbol').players.every(p=>p.isPaused)")
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  shots=ROOT/'tests/venture-screenshots';shots.mkdir(exist_ok=True)
  page.screenshot(path=str(shots/f'response-motion-{width}.png'))
  page.evaluate("$('ventureTurns').replaceChildren();")
  assert page.evaluate('lottie.getRegisteredAnimations().length')==0
  assert not errors,errors
  print(f'{width}px: uninterrupted badge/loader, estimates, buffered Markdown, reduced motion and cleanup passed')
  page.close()
 browser.close()
