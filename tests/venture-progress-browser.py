"""Mobile/desktop activity timeline: real events, public summaries and native metrics."""
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
        page = browser.new_page(viewport={'width': width, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/*', lambda route: route.abort())
        page.set_content(fixture)
        page.evaluate("""()=>{
          venture.open=true;$('visionVenture').hidden=false;
          const modelNote=Array(5).fill('Comparing sunlight exposure and the available soil information, while checking which vegetable choices can be supported by the garden constraints.').join(' ');
          window.run={runId:'notes',sequence:1,status:'in_progress',model:'qwen-ultra-local',phase:'Deep 2/3 · Reviewing the draft',text:'',message:'Help me plan a small vegetable garden.',localQueue:{
            processingSeconds:81,estimateStatus:'learning',metrics:{computeRoute:'fupcj',inputTokens:1893,contextTokens:32768,reasoning:{stageNumber:2},nativeReasoning:{receivedCharacters:12048,observedSeconds:65}},
            progress:{version:1,steps:[
             {id:1,text:'Preparing the user request and relevant background material.',source:'stage',atSeconds:0.4},
             {id:2,text:modelNote,source:'model',atSeconds:41.2}
            ]}
          }};
          venture.runs=[run];venturePaintTurns();
        }""")
        panel = page.locator('.venture-progress-notes')
        history = page.locator('.venture-progress-history')
        current = page.locator('.venture-progress-current')
        timeline = page.locator('.venture-progress-steps')
        assert panel.evaluate('(e)=>e.open')
        assert history.evaluate('(e)=>e.open')
        assert 30 < len(current.inner_text().split()) <= 120
        # The label is visually uppercased by CSS; assert its authored text.
        assert 'Qwen’s latest public explanation' in page.locator('.venture-progress-label').text_content()
        assert '12,048 native reasoning characters' in page.locator('.venture-progress-telemetry').inner_text()
        assert '1,893 input tokens · 32,768 context capacity' in page.locator('.venture-progress-telemetry').inner_text()
        assert 'Live output measurements are unavailable' in page.locator('.venture-progress-health').inner_text()
        assert timeline.locator('.venture-progress-entry').count() == 1
        assert '+0s' in timeline.inner_text()
        assert timeline.locator('details').count() == 0

        # Observed stage changes do not hide the latest model-authored summary.
        page.evaluate("""()=>{
          run.localQueue.progress.steps.push({id:3,text:'Deep review request was started with the original evidence retained.',source:'stage',atSeconds:70});
          run.sequence++;venturePaintTurns();
        }""")
        assert 'Comparing sunlight' in current.inner_text()
        assert timeline.locator('.venture-progress-entry').count() == 2
        assert 'Processor event' in timeline.inner_text()

        # Expanded history survives polling and new events; a manual close is honored.
        history.locator(':scope > summary').click()
        page.evaluate('run.sequence++;venturePaintTurns()')
        assert not history.evaluate('(e)=>e.open')
        page.evaluate("""()=>{
          run.localQueue.progress.steps.push({id:4,text:'Checking whether the growing-season assumptions are stated clearly and which recommendations would require additional local evidence.',source:'model',atSeconds:79});
          run.sequence++;venturePaintTurns();
        }""")
        assert not history.evaluate('(e)=>e.open')
        assert 'Checking whether the growing-season' in current.inner_text()
        history.locator(':scope > summary').click()
        assert timeline.locator('.venture-progress-entry').count() == 3
        assert 'Qwen public summary' in timeline.inner_text()
        assert timeline.locator('.venture-progress-entry-heading span:first-of-type').all_text_contents() == ['Step 1', 'Step 2', 'Step 3']

        # A real generation counter advances independently of static input size.
        page.evaluate("""()=>{
          run.localQueue.metrics.activity={version:1,requestNumber:2,generatedTokens:1793,tokensPerSecond:1.37,secondsSinceOutput:1,secondsSinceEvent:0,elapsedSeconds:1335};
          run.sequence++;venturePaintTurns();
        }""")
        telemetry = page.locator('.venture-progress-telemetry')
        health = page.locator('.venture-progress-health')
        assert '1,793 generated tokens this pass' in telemetry.inner_text()
        assert 'Last model output 1s ago' in telemetry.inner_text()
        assert health.is_hidden()
        telemetry.scroll_into_view_if_needed()
        page.screenshot(path=str(shots/f'qwen-ultra-notes-{width}.png'))
        # Polling ages an unchanged stream; a fresh heartbeat does not hide silence.
        page.evaluate("run.localQueue.metrics.activity.secondsSinceOutput=180;venturePaintTurns()")
        assert 'slow or stalled' in health.inner_text()
        assert 'heartbeat does not confirm generation' in health.inner_text()
        assert 'Last model output 3m ago' in telemetry.inner_text()
        health.scroll_into_view_if_needed()
        page.screenshot(path=str(shots/f'qwen-ultra-quiet-{width}.png'))
        page.evaluate("run.status='cancelled';run.sequence++;venturePaintTurns()")
        assert health.is_hidden()
        assert panel.evaluate('(e)=>e.open')
        assert timeline.locator('.venture-progress-entry').count() == 4
        assert 'Stopped' in panel.locator(':scope > summary').inner_text()

        panel.locator(':scope > summary').click()
        page.evaluate("run.status='completed';run.text='Plant herbs near the kitchen and give tomatoes your sunniest spot.';run.sequence++;venturePaintTurns()")
        assert not panel.evaluate('(e)=>e.open')
        panel.locator(':scope > summary').click()
        assert 'Qwen public summary' in timeline.inner_text()

        # Earlier processors left summary tags in answer-channel text.
        page.evaluate("""()=>{
          run.localQueue.progress.steps=[{id:1,text:'Preparing the question and its context.',source:'stage'}];
          run.text='<progress_summary>Checking the supplied background before responding.</progress_summary>\\n\\n## Answer\\n\\n**Ready.**';
          run.sequence++;venturePaintTurns();
        }""")
        assert 'Checking the supplied background' in current.inner_text()
        assert timeline.locator('.venture-progress-entry').count() == 2
        assert page.locator('[data-venture-text] strong').inner_text() == 'Ready.'
        assert '<progress_summary>' not in page.locator('[data-venture-text]').inner_text()
        page.evaluate("()=>{window.consoleCopyText=async value=>{window.copiedAnswer=value;}}")
        page.locator('[data-v-action="copy"]').click()
        assert page.evaluate("window.copiedAnswer") == '## Answer\n\n**Ready.**'

        # Untrusted model text is never inserted as HTML; overlong notes are rejected.
        page.evaluate("""()=>{
          run.text='';run.status='in_progress';
          run.localQueue.progress.steps=[
           {id:5,text:'Checking <img onerror=alert(1)> supplied material.',source:'model'},
           {id:6,text:'word '.repeat(121),source:'model'}];
          run.sequence++;venturePaintTurns();
        }""")
        assert page.locator('[data-venture-notes] img').count() == 0
        assert '<img' in current.text_content()
        assert timeline.locator('.venture-progress-entry').count() == 0
        page.evaluate("delete run.localQueue.progress;run.sequence++;venturePaintTurns()")
        assert page.locator('.venture-progress-notes').count() == 0
        page.emulate_media(reduced_motion='reduce')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors, errors
        page.close()
    browser.close()
