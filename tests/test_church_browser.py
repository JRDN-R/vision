"""Real Chromium, local intercepted HTML fixtures; no Planning Center traffic."""
from pathlib import Path
import os,sys
import pytest
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'vision-pc'))
from church_browser import SchedulingEmailBrowser
from church_notifications import BrowserReviewRequired,LoginRequired

HTML='''<button role="tab">Teams</button><div role="list"><div role="listitem"><span>Fixture Person</span>Streamers<button onclick="document.getElementById('compose').hidden=false">Scheduling email</button></div></div><section role="dialog" id="compose" hidden><h2>Scheduling Email</h2><p>Fixture Person</p><select aria-label="Template"><option>Existing scheduling template</option></select><button onclick="window.sendCount=(window.sendCount||0)+1">Send 1</button></section>'''
@pytest.fixture
def page():
    with sync_playwright() as pw:
        options={'executable_path':os.environ['CHURCH_TEST_CHROMIUM']} if os.environ.get('CHURCH_TEST_CHROMIUM') else {}
        browser=pw.chromium.launch(headless=True,**options);page=browser.new_page();page.set_default_timeout(500)
        page.route('**/*',lambda route:route.fulfill(body=HTML,content_type='text/html'))
        page.goto('https://services.planningcenteronline.com/plans/13');yield page;browser.close()

def ready(page):return SchedulingEmailBrowser.ready_to_send(page,{'plan_id':'13'},{'attributes':{'name':'Fixture Person','team_position_name':'Streamers'}})

def test_exact_recipient_opens_template_without_sending(page):
    button=ready(page);assert button.is_enabled()
    assert page.evaluate('window.sendCount||0')==0
    assert page.get_by_role('combobox').input_value()=='Existing scheduling template'
    button.click();assert page.evaluate('window.sendCount')==1

def test_ambiguous_person_stops_before_composer(page):
    page.locator('body').evaluate("el=>el.insertAdjacentHTML('beforeend','<div>Fixture Person</div>')")
    with pytest.raises(BrowserReviewRequired):ready(page)
    assert not page.get_by_role('dialog').is_visible()

def test_wrong_position_stops_without_send(page):
    page.get_by_role('listitem').evaluate("el=>el.innerHTML=el.innerHTML.replace('Streamers','Camera')")
    with pytest.raises(BrowserReviewRequired):ready(page)
    assert page.evaluate('window.sendCount||0')==0

def test_send_more_than_one_rejected(page):
    page.get_by_role('button',name='Send 1',include_hidden=True).evaluate("el=>el.textContent='Send 2'")
    with pytest.raises(BrowserReviewRequired):ready(page)
    assert page.evaluate('window.sendCount||0')==0

def test_wrong_plan_redirect_rejected(page):
    page.goto('https://services.planningcenteronline.com/plans/999')
    with pytest.raises(BrowserReviewRequired):ready(page)

def test_login_wall_detected(page):
    page.goto('https://login.planningcenteronline.com/login/new')
    with pytest.raises(LoginRequired):ready(page)
