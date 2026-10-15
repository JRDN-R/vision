"""Strict, isolated Planning Center UI adapter. Stop when the UI is ambiguous."""
from __future__ import annotations

from pathlib import Path
import os
import re

from church_notifications import LoginRequired, BrowserReviewRequired


def exactly_one(locator, label):
    if locator.count() != 1 or not locator.is_visible():
        raise BrowserReviewRequired(label+' is missing or ambiguous. No send was attempted.')
    return locator


class SchedulingEmailBrowser:
    def __init__(self, admin):
        self.admin = admin

    def send(self, job, assignment, plan, storage, before_click):
        packages = self.admin.settings.get('packagesPath')
        if packages:
            import sys
            if packages not in sys.path:
                sys.path.insert(0,packages)
        browser_path = self.admin.settings.get('browsersPath')
        if browser_path:
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = browser_path
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(storage_state=storage)
                page = context.new_page()
                page.set_default_timeout(15000)
                page.goto('https://services.planningcenteronline.com/plans/'+job['plan_id'],wait_until='domcontentloaded',timeout=30000)
                send = self.ready_to_send(page, job, assignment)
                with self.admin.lock:
                    before_click()
                    send.click()
                # Dispatch is verified separately through notification_sent_at.
                return True
            finally:
                browser.close()

    @staticmethod
    def ready_to_send(page, job, assignment):
        if 'login.planningcenteronline.com' in page.url or page.get_by_role('button',name='Log in',exact=True).is_visible():
            raise LoginRequired()
        if not re.match(r'https://services\.planningcenteronline\.com/plans/'+re.escape(job['plan_id'])+r'(?:[/?#]|$)',page.url):
            raise BrowserReviewRequired('The requested Planning Center plan did not open.')
        teams = page.get_by_role('tab',name='Teams',exact=True).or_(page.get_by_role('link',name='Teams',exact=True)).or_(page.get_by_role('button',name='Teams',exact=True))
        exactly_one(teams,'Teams tab').click()
        name = assignment['attributes']['name']
        person = page.get_by_text(name,exact=True)
        exactly_one(person,'Scheduled person')
        # Use visible semantic containers; never a positional selector or
        # a plan-wide/team-wide Send action. Unknown layouts stop for review.
        candidates = page.get_by_role('row').filter(has=person).or_(page.get_by_role('listitem').filter(has=person))
        row = exactly_one(candidates,'Individual assignment row')
        role = assignment['attributes'].get('team_position_name','')
        if role and role not in row.inner_text():
            raise BrowserReviewRequired('The individual assignment role did not verify.')
        envelope = row.get_by_role('button',name=re.compile(r'^(?:Send )?(?:scheduling )?email(?: notification)?$',re.I)).or_(row.get_by_role('link',name=re.compile(r'^(?:Send )?(?:scheduling )?email(?: notification)?$',re.I)))
        exactly_one(envelope,'Individual scheduling email control').click()
        dialog = exactly_one(page.get_by_role('dialog'),'Scheduling Email composer')
        recipient = dialog.get_by_text(name,exact=True)
        exactly_one(recipient,'Scheduling Email recipient')
        # The existing selected template is preserved. Do not fabricate
        # Accept/Decline links; Services appends those on dispatch.
        send = exactly_one(dialog.get_by_role('button',name='Send 1',exact=True),'Send 1 button')
        if not send.is_enabled():
            raise BrowserReviewRequired('The scheduling email is not ready to send.')
        return send
