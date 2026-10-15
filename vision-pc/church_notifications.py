"""One-at-a-time official Scheduling Email worker with dispatch reconciliation."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import time

import requests

from church_admin import ChurchError


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (TypeError, ValueError, AttributeError):
        return None


class PlanningCenter:
    def __init__(self, credentials, session=None):
        self.session = session or requests.Session()
        self.session.auth = (credentials['client_id'], credentials['secret'])
        self.session.headers.update({'X-PCO-API-Version':'2018-11-01','Accept':'application/json'})

    def get(self, path):
        if not path.startswith('/services/v2/') or '..' in path:
            raise ChurchError('Unsupported Planning Center read.')
        try:
            with self.session.get('https://api.planningcenteronline.com'+path, timeout=(5,20), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise ChurchError('Planning Center read failed. Check authorization and the assignment.', 502)
                return response.json()
        except requests.RequestException:
            raise ChurchError('Planning Center could not be reached.', 502) from None

    def assignment(self, job):
        value = self.get(f"/services/v2/people/{job['person_id']}/plan_people/{job['assignment_id']}")['data']
        for field in ('person','plan','service_type'):
            if value.get('relationships',{}).get(field,{}).get('data',{}).get('id') != job[field+'_id']:
                raise ChurchError('Assignment identity changed. Review the plan.', 409)
        if value.get('id') != job['assignment_id']:
            raise ChurchError('Assignment identity changed. Review the plan.', 409)
        if value['attributes'].get('status') in ('D','Declined'):
            raise ChurchError('This assignment was declined. No email sent.', 409)
        return value

    def plan(self, job):
        value = self.get(f"/services/v2/service_types/{job['service_type_id']}/plans/{job['plan_id']}?include=plan_times")
        if value['data']['id'] != job['plan_id']:
            raise ChurchError('Plan identity did not verify.', 409)
        times = [r['attributes']['starts_at'] for r in value.get('included',[]) if r.get('type')=='PlanTime' and r['attributes'].get('time_type')=='service']
        if not times or any(timestamp(t) is None or timestamp(t) < time.time() for t in times):
            raise ChurchError('The plan has no verified future service time.', 409)
        return value

    def close(self):
        self.session.close()


class LoginRequired(Exception):
    pass


class BrowserReviewRequired(Exception):
    pass


class NotificationWorker:
    def __init__(self, admin, api_factory=PlanningCenter, sender_factory=None, sleep=time.sleep):
        self.admin, self.api_factory, self.sleep = admin, api_factory, sleep
        if sender_factory is None:
            from church_browser import SchedulingEmailBrowser
            sender_factory = SchedulingEmailBrowser
        self.sender_factory = sender_factory

    def change(self, job_id, state, detail='', **values):
        allowed = {'before_sent','sent_at','attempted_at'}
        if set(values)-allowed:
            raise ValueError('Invalid operation state')
        fields = {'state':state, 'detail':detail, 'updated_at':time.time(), **values}
        if state in ('sent','already_sent','cancelled','failed'):
            fields['credential'] = None
        with self.admin.db() as db:
            db.execute('UPDATE church_email_jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?', (*fields.values(),job_id))

    def import_login(self):
        pending = self.admin.directory / 'browser-state.pending.json'
        if not pending.is_file() or pending.is_symlink():
            return
        if pending.stat().st_size > 2*1024*1024:
            raise ChurchError('Planning Center sign-in state is too large.')
        text = pending.read_text(encoding='utf-8')
        state = json.loads(text)
        if not isinstance(state.get('cookies'), list) or not isinstance(state.get('origins'), list):
            raise ChurchError('Planning Center sign-in state is invalid.')
        # Only provider cookies and origins enter the isolated service browser.
        for cookie in state['cookies']:
            domain = cookie.get('domain','').lstrip('.')
            if domain != 'planningcenteronline.com' and not domain.endswith('.planningcenteronline.com'):
                raise ChurchError('Sign-in state contains an unexpected cookie domain.')
        for origin in state['origins']:
            if origin.get('origin','') not in ('https://services.planningcenteronline.com','https://login.planningcenteronline.com'):
                raise ChurchError('Sign-in state contains an unexpected origin.')
        target = self.admin.directory / 'browser-state.dpapi'
        stage = target.with_suffix('.tmp')
        stage.write_bytes(self.admin.protect(text))
        os.replace(stage,target)
        pending.unlink()
        with self.admin.db() as db:
            db.execute("UPDATE church_email_jobs SET state='queued',detail='Sign-in updated.' WHERE state='waiting_for_login'")

    def process(self, job):
        api = None
        before = None
        attempted = None
        try:
            self.admin.member(job['uid'], write=True)
            creds = json.loads(self.admin.unprotect(job['credential']))
            api = self.api_factory(creds)
            before = api.assignment(job)
            previous = before['attributes'].get('notification_sent_at')
            if previous and timestamp(previous) is None:
                raise ChurchError('The prior notification timestamp could not be verified.', 409)
            self.change(job['id'],'running',before_sent=previous)
            if previous and not job['resend']:
                self.change(job['id'],'already_sent','Planning Center already records an email; no new email sent.',sent_at=previous)
                return
            plan = api.plan(job)
            state_file = self.admin.directory / 'browser-state.dpapi'
            if not state_file.is_file():
                raise LoginRequired()
            storage = json.loads(self.admin.unprotect(state_file.read_bytes()))

            def before_click():
                nonlocal attempted
                # Serialize the last authorization check with access revocation.
                self.admin.member(job['uid'], write=True)
                current = api.assignment(job)
                if current['attributes'].get('notification_sent_at') != previous:
                    raise BrowserReviewRequired('Another operator changed the notification. Check Planning Center.')
                attempted = time.time()
                self.change(job['id'],'sending','Send attempted; awaiting confirmation.',attempted_at=attempted)

            sender = self.sender_factory(self.admin)
            try:
                sender.send(job, before, plan, storage, before_click)
            except (LoginRequired, BrowserReviewRequired):
                raise
            except Exception:
                # Reconcile even when the browser timed out after the click.
                if attempted is None:
                    raise BrowserReviewRequired('The scheduling screen could not be verified. No send was attempted.') from None
            if attempted is None:
                raise BrowserReviewRequired('The worker did not reach the verified send action.')
            sent = None
            for delay in (0,2,4):
                if delay:
                    self.sleep(delay)
                try:
                    sent = api.assignment(job)['attributes'].get('notification_sent_at')
                except ChurchError:
                    continue
                new_time = timestamp(sent)
                if sent != previous and new_time is not None and attempted-5 <= new_time <= time.time()+60:
                    self.change(job['id'],'sent','Planning Center recorded the Scheduling Email send.',sent_at=sent)
                    return
            self.change(job['id'],'needs_review','Send result could not be verified. Check Planning Center; do not resend automatically.')
        except LoginRequired:
            self.change(job['id'],'needs_review' if attempted else 'waiting_for_login',
                        'Planning Center sign-in is required on FUPCJ Server.' if not attempted else 'Sign-in expired after a send attempt. Check Planning Center before retrying.')
        except (ChurchError, BrowserReviewRequired) as exc:
            self.change(job['id'],'needs_review' if attempted else 'failed', str(exc))
        except Exception:
            self.change(job['id'],'needs_review' if attempted else 'failed','Notification worker failed. Check the server setup and Planning Center before retrying.')
            self.admin.app.logger.error('Church notification worker failed for job %s.',job['id'])
        finally:
            if api:
                api.close()

    def run(self):
        while not self.admin.stop.is_set():
            try:
                self.import_login()
                with self.admin.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    job = db.execute("SELECT * FROM church_email_jobs WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
                    if job:
                        db.execute("UPDATE church_email_jobs SET state='running',updated_at=? WHERE id=?",(time.time(),job['id']))
                if job:
                    self.process(dict(job))
                    continue
            except Exception:
                self.admin.app.logger.error('Church worker initialization/check failed; no notification sent.')
            self.admin.wake.wait(5)
            self.admin.wake.clear()
