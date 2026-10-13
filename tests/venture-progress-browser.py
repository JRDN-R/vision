"""Real progress disclosure DOM with simulated model summaries, no model calls."""
import os
import runpy
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(ROOT/'tests/venture-smoke.py'), run_name='progress_fixture')['fixture']
shots = ROOT/'tests/venture-screenshots'
shots.mkdir(exist_ok=True)

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, **({'executable_path': os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
    for width in (390, 1280):
        page = browser.new_page(viewport={'width':width, 'height':900})
        errors=[]
        page.on('pageerror', lambda e:errors.append(str(e)))
        page.route('**/*', lambda route:route.abort())
        page.set_content(fixture)
        page.evaluate('''()=>{
          venture.open=true;$('visionVenture').hidden=false;
          window.run={runId:'notes',status:'in_progress',model:'qwen-ultra-local',phase:'Qwen thinking',text:'',message:'Help me plan a small vegetable garden.',localQueue:{processingSeconds:20,estimateStatus:'learning',progress:{version:1,steps:[{id:1,text:'Getting your request ready.',source:'stage'},{id:2,text:'Comparing the available space and sunlight so the planting suggestions fit your garden, rather than giving you a general list of vegetables.',source:'model'}]}}};
          venture.runs=[run];venturePaintTurns();
        }''')
        panel=page.locator('.venture-progress-notes')
        history=page.locator('.venture-progress-history')
        assert panel.evaluate('(e)=>e.open')
        assert 'Comparing the available space' in page.locator('.venture-progress-current').inner_text()
        assert not history.evaluate('(e)=>e.open')
        history.locator(':scope > summary').click()
        page.locator('.venture-progress-steps details > summary').click()
        page.evaluate("run.sequence=2;venturePaintTurns()")
        assert history.evaluate('(e)=>e.open')
        assert page.locator('.venture-progress-steps details').evaluate('(e)=>e.open')
        page.evaluate("run.localQueue.progress.steps.push({id:3,text:'Checking which vegetables share similar watering needs, making it easier to group them together and keep the garden manageable throughout the growing season.',source:'model'});venturePaintTurns()")
        assert not history.evaluate('(e)=>e.open')
        assert page.locator('.venture-progress-steps details[open]').count()==0
        assert panel.evaluate('(e)=>e.open')
        assert len(page.locator('.venture-progress-current').inner_text().split())<=30
        page.screenshot(path=str(shots/f'qwen-ultra-notes-{width}.png'))
        panel.locator(':scope > summary').click()
        page.evaluate("run.sequence++;venturePaintTurns()")
        assert not panel.evaluate('(e)=>e.open')
        page.evaluate("run.status='completed';run.text='Plant herbs near the kitchen and give tomatoes your sunniest spot.';venturePaintTurns()")
        assert not panel.evaluate('(e)=>e.open')
        panel.locator(':scope > summary').click()
        assert history.evaluate('(e)=>e.open')
        assert page.locator('.venture-progress-steps details').count()==3
        page.evaluate("run.sequence++;venturePaintTurns()")
        assert panel.evaluate('(e)=>e.open')
        page.locator('.venture-progress-steps details > summary').nth(1).click()
        assert 'Comparing the available space' in page.locator('.venture-progress-steps').text_content()
        # Reopening history does not replay or auto-expand saved summaries.
        page.evaluate("venture.rendered.clear();$('ventureTurns').replaceChildren();venturePaintTurns()")
        assert not panel.evaluate('(e)=>e.open')
        for status in ('cancelled','error','incomplete'):
            page.evaluate("run.status='in_progress';venturePaintTurns();document.querySelector('.venture-progress-notes').open=true")
            page.evaluate('(status)=>{run.status=status;venturePaintTurns()}',status)
            assert not panel.evaluate('(e)=>e.open')
        page.emulate_media(reduced_motion='reduce')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        # Pre-fix responses may have saved the marker in final answer content.
        page.evaluate("""()=>{
          run.status='completed';
          run.localQueue.progress.steps=[{id:1,text:'Getting your request ready.',source:'stage'}];
          run.text='<progress_summary>Checking the supplied background before responding.</progress_summary>\\n\\n## Answer\\n\\n**Ready.**';
          venturePaintTurns();
        }""")
        assert page.locator('.venture-progress-notes').count()==1
        assert 'Checking the supplied background' in page.locator('.venture-progress-steps').text_content()
        assert page.locator('.venture-progress-steps details').count()==2
        assert page.locator('[data-venture-text] strong').inner_text()=='Ready.'
        assert '<progress_summary>' not in page.locator('[data-venture-text]').inner_text()
        assert '</progress_summary>' not in page.locator('[data-venture-text]').inner_html()
        page.evaluate("()=>{window.consoleCopyText=async value=>{window.copiedAnswer=value;}}")
        page.locator('[data-v-action="copy"]').click()
        assert page.evaluate("window.copiedAnswer")=='## Answer\n\n**Ready.**'

        # Malformed notes are omitted. Model text is always rendered as text.
        page.evaluate("run.text='';run.status='in_progress';run.localQueue.progress.steps=[{id:4,text:'Checking <img onerror=alert(1)> supplied material.',source:'model'},{id:5,text:'word '.repeat(31),source:'model'}];venturePaintTurns()")
        assert page.locator('[data-venture-notes] img').count()==0
        assert '<img' in page.locator('.venture-progress-current').text_content()
        page.evaluate("delete run.localQueue.progress;venturePaintTurns()")
        assert page.locator('.venture-progress-notes').count()==0
        assert not errors,errors
        page.close()
    browser.close()
