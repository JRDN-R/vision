"""FirstUPCJax permissions, ChatGPT linking and durable notification jobs.

This module exposes defined church operations, never a shell or generic browser API.
The normal Vision Firebase verifier authenticates dashboard requests. Bridge calls
use a separate HMAC key and replay protection; neither mechanism grants membership.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import threading
import time

from flask import g, jsonify, request

READ_TOOLS = {'connection_status', 'find_people', 'list_service_types', 'list_plans',
              'get_plan', 'list_positions', 'get_person_availability', 'preview_assignment',
              'get_assignment', 'notification_job'}
WRITE_TOOLS = {'schedule_person', 'prepare_notification', 'send_scheduling_email'}
ROLES = {'viewer', 'scheduler'}
ID = re.compile(r'[0-9]{1,24}\Z')
REQUEST_ID = re.compile(r'[A-Za-z0-9_-]{16,100}\Z')


class ChurchError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def email(value):
    if not isinstance(value, str) or len(value) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise ChurchError('Enter a valid email address.')
    return value.strip().casefold()


class ChurchAdmin:
    def __init__(self, app, db, protect, unprotect, account_blocked=lambda uid: False):
        self.app, self.db, self.protect, self.unprotect = app, db, protect, unprotect
        self.settings = {}
        self.account_blocked = account_blocked
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.RLock()
        self.register_routes()

    def initialize(self, config):
        self.settings = config.get('churchAdmins') or {}
        if not isinstance(self.settings, dict):
            raise ValueError('churchAdmins must be an object')
        self.directory = Path(self.app.config['DATA_DIR']) / 'church-admin'
        self.directory.mkdir(exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS church_members (
                    uid TEXT PRIMARY KEY, email TEXT NOT NULL, role TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS church_single_owner ON church_members(role) WHERE role='owner';
                CREATE TABLE IF NOT EXISTS church_invites (
                    email TEXT PRIMARY KEY, role TEXT NOT NULL, active INTEGER NOT NULL,
                    expires_at REAL NOT NULL, created_by TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS church_links (
                    actor TEXT PRIMARY KEY, uid TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS church_pair_codes (
                    hash TEXT PRIMARY KEY, uid TEXT NOT NULL, expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS church_bridge_nonces (nonce TEXT PRIMARY KEY, at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS church_email_jobs (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, uid TEXT NOT NULL,
                    assignment_id TEXT NOT NULL, person_id TEXT NOT NULL, plan_id TEXT NOT NULL,
                    service_type_id TEXT NOT NULL, state TEXT NOT NULL, resend INTEGER NOT NULL,
                    before_sent TEXT, sent_at TEXT, attempted_at REAL,
                    credential BLOB, detail TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS church_active_assignment ON church_email_jobs(assignment_id)
                    WHERE state IN ('queued','running','waiting_for_login','sending','needs_review');
                CREATE TABLE IF NOT EXISTS church_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL,
                    action TEXT NOT NULL, subject TEXT NOT NULL, at REAL NOT NULL);
            ''')
            # Never automatically repeat a potentially dispatched email after a crash.
            db.execute("UPDATE church_email_jobs SET state='needs_review',detail='Interrupted send. Check Planning Center before retrying.' WHERE state='sending'")
            db.execute("UPDATE church_email_jobs SET state='queued' WHERE state='running'")

    def enabled(self):
        if self.settings.get('enabled') is not True:
            raise ChurchError('FirstUPCJax Admins needs its FUPCJ Server setup.', 503)

    def audit(self, db, uid, action, subject):
        db.execute('INSERT INTO church_audit(uid,action,subject,at) VALUES(?,?,?,?)',
                   (uid, action, str(subject)[:120], time.time()))

    def member(self, uid, write=False, owner=False):
        if self.account_blocked(uid):
            raise ChurchError('This Vision account has been removed.', 403)
        with self.db() as db:
            row = db.execute('SELECT uid,email,role,active FROM church_members WHERE uid=?', (uid,)).fetchone()
        if not row or not row['active']:
            raise ChurchError('Your account is not authorized for FirstUPCJax Admins.', 403)
        if owner and row['role'] != 'owner' or write and row['role'] not in ('owner', 'scheduler'):
            raise ChurchError('Your role does not allow this action.', 403)
        return dict(row)

    def dashboard_identity(self):
        self.enabled()
        if getattr(g, 'auth_kind', '') != 'firebase-google':
            raise ChurchError('Sign in to your verified Vision account.', 401)
        claims = getattr(g, 'identity_claims', {})
        if claims.get('email_verified') is not True:
            raise ChurchError('Verify your Vision account email before accepting access.', 403)
        address = email(claims.get('email'))
        uid = g.uid
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM church_members WHERE uid=?', (uid,)).fetchone()
            if not row:
                owner = db.execute("SELECT uid FROM church_members WHERE role='owner'").fetchone()
                if not owner and address == email(self.settings.get('ownerEmail')):
                    db.execute('INSERT INTO church_members VALUES(?,?,?,?,?)', (uid, address, 'owner', 1, time.time()))
                    self.audit(db, uid, 'owner-linked', uid)
                else:
                    invite = db.execute('SELECT * FROM church_invites WHERE email=? AND active=1 AND expires_at>?', (address, time.time())).fetchone()
                    if not invite:
                        raise ChurchError('Ask the FirstUPCJax administrator to add your email.', 403)
                    db.execute('INSERT INTO church_members VALUES(?,?,?,?,?)', (uid, address, invite['role'], 1, time.time()))
                    db.execute('UPDATE church_invites SET active=0 WHERE email=?', (address,))
                    self.audit(db, uid, 'invite-accepted', address)
        return self.member(uid)

    def check_bridge(self):
        self.enabled()
        key = self.settings.get('bridgeSecret', '')
        stamp = request.headers.get('X-FUPCJ-Time', '')
        nonce = request.headers.get('X-FUPCJ-Nonce', '')
        signature = request.headers.get('X-FUPCJ-Signature', '')
        body = request.get_data(cache=True)
        if not isinstance(key, str) or len(key) < 40 or len(body) > 32768:
            raise ChurchError('Bridge is not configured or request is too large.', 401)
        if not stamp.isdigit() or abs(time.time() - int(stamp)) > 60 or not re.fullmatch(r'[a-f0-9]{32}', nonce):
            raise ChurchError('Bridge authentication failed.', 401)
        signed = (stamp + '\n' + nonce + '\n').encode() + body
        expected = hmac.new(key.encode(), signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ChurchError('Bridge authentication failed.', 401)
        with self.db() as db:
            db.execute('DELETE FROM church_bridge_nonces WHERE at<?', (time.time() - 180,))
            result = db.execute('INSERT OR IGNORE INTO church_bridge_nonces VALUES(?,?)', (nonce, time.time()))
            if result.rowcount != 1:
                raise ChurchError('Bridge request already processed; reconcile before retrying.', 409)
        g.auth_kind = 'church-bridge'

    def pair(self, actor, code):
        if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,100}', code):
            raise ChurchError('Link code is invalid or expired.', 403)
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT uid FROM church_pair_codes WHERE hash=? AND expires_at>?', (digest(code), time.time())).fetchone()
            if not row:
                raise ChurchError('Link code is invalid or expired.', 403)
            membership = self.member(row['uid'])
            existing = db.execute('SELECT uid FROM church_links WHERE actor=?', (actor,)).fetchone()
            if existing and existing['uid'] != row['uid']:
                raise ChurchError('This ChatGPT connection is already linked to another Vision account. Unlink it first.', 409)
            db.execute('INSERT OR REPLACE INTO church_links VALUES(?,?,?)', (actor, row['uid'], time.time()))
            db.execute('DELETE FROM church_pair_codes WHERE uid=?', (row['uid'],))
            self.audit(db, row['uid'], 'chatgpt-linked', digest(actor))
        return {'linked': True, 'role': membership['role']}

    def linked_member(self, actor, action):
        if action not in READ_TOOLS | WRITE_TOOLS:
            raise ChurchError('Unknown church operation.')
        with self.db() as db:
            row = db.execute('SELECT uid FROM church_links WHERE actor=?', (actor,)).fetchone()
        if not row:
            raise ChurchError('Link your ChatGPT connection in Vision → FirstUPCJax Admins first.', 403)
        return self.member(row['uid'], write=action in WRITE_TOOLS)

    def public_job(self, row):
        return {k: row[k] for k in ('id','assignment_id','person_id','plan_id','service_type_id','state','before_sent','sent_at','detail','created_at','updated_at')}

    def submit(self, uid, args, credentials):
        self.member(uid, write=True)
        required = ('assignment_id','person_id','plan_id','service_type_id')
        if not isinstance(args, dict) or any(not isinstance(args.get(k), str) or not ID.fullmatch(args[k]) for k in required):
            raise ChurchError('Verified person, assignment, plan and service type IDs are required.')
        if not REQUEST_ID.fullmatch(str(args.get('request_id', ''))) or type(args.get('resend', False)) is not bool:
            raise ChurchError('A valid operation request ID and boolean resend setting are required.')
        if not isinstance(credentials, dict) or any(not isinstance(credentials.get(k), str) or not credentials[k] or len(credentials[k]) > 1024 for k in ('client_id','secret')):
            raise ChurchError('Planning Center authorization is unavailable.', 503)
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM church_email_jobs WHERE request_id=?', (args['request_id'],)).fetchone()
            if previous:
                if previous['uid'] != uid or any(previous[k] != args[k] for k in required) or bool(previous['resend']) != args.get('resend', False):
                    raise ChurchError('Request ID belongs to a different operation.', 409)
                return self.public_job(previous)
            existing = db.execute("SELECT * FROM church_email_jobs WHERE assignment_id=? AND state IN ('queued','running','waiting_for_login','sending','needs_review')", (args['assignment_id'],)).fetchone()
            if existing:
                if existing['uid'] != uid or any(existing[k] != args[k] for k in required):
                    raise ChurchError('Another administrator already has an operation for this assignment.', 409)
                return self.public_job(existing)
            job_id = secrets.token_hex(12)
            cipher = self.protect(json.dumps({k:credentials[k] for k in ('client_id','secret')}))
            now = time.time()
            db.execute('''INSERT INTO church_email_jobs(id,request_id,uid,assignment_id,person_id,plan_id,service_type_id,state,resend,credential,created_at,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (job_id,args['request_id'],uid,args['assignment_id'],args['person_id'],args['plan_id'],args['service_type_id'],'queued',int(args.get('resend', False)),cipher,now,now))
            self.audit(db, uid, 'email-queued', job_id)
            row = db.execute('SELECT * FROM church_email_jobs WHERE id=?', (job_id,)).fetchone()
        self.wake.set()
        return self.public_job(row)

    def register_routes(self):
        app = self.app

        @app.errorhandler(ChurchError)
        def church_error(exc):
            return jsonify(error=str(exc)), exc.status

        @app.post('/api/church-admin/bridge')
        def bridge():
            # server.authorize validates HMAC before this route; never trust public actor headers.
            if getattr(g, 'auth_kind', '') != 'church-bridge':
                raise ChurchError('Authenticated bridge required.', 401)
            body = request.get_json(silent=True) or {}
            actor = body.get('actor', '')
            if not isinstance(actor, str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,200}', actor):
                raise ChurchError('Missing authenticated ChatGPT identity.', 401)
            action = body.get('action')
            if action == 'link_vision_account':
                return jsonify(self.pair(actor, body.get('code')))
            member = self.linked_member(actor, action)
            if action == 'send_scheduling_email':
                return jsonify(self.submit(member['uid'], body.get('arguments'), body.get('credentials'))), 202
            if action == 'notification_job':
                job_id = str(body.get('arguments', {}).get('job_id', ''))
                with self.db() as db:
                    row = db.execute('SELECT * FROM church_email_jobs WHERE id=? AND uid=?', (job_id, member['uid'])).fetchone()
                if not row:
                    raise ChurchError('Notification operation not found.', 404)
                return jsonify(self.public_job(row))
            return jsonify(authorized=True, role=member['role'])

        @app.get('/api/church-admin')
        def dashboard():
            member = self.dashboard_identity()
            with self.db() as db:
                jobs = db.execute('SELECT * FROM church_email_jobs WHERE uid=? ORDER BY created_at DESC LIMIT 50', (member['uid'],)).fetchall()
                links = db.execute('SELECT COUNT(*) FROM church_links WHERE uid=?', (member['uid'],)).fetchone()[0]
                if hasattr(self,'oauth'): links += self.oauth.connections(db,member['uid'])
            return jsonify(member=member, jobs=[self.public_job(j) for j in jobs], linkedConnections=links,
                           browserReady=(self.directory / 'browser-state.dpapi').is_file(),
                           apiSendSupported=False, notificationMethod='FUPCJ browser worker')

        @app.post('/api/church-admin/link-code')
        def link_code():
            member = self.dashboard_identity()
            code = secrets.token_urlsafe(32)
            with self.db() as db:
                db.execute('DELETE FROM church_pair_codes WHERE uid=? OR expires_at<?', (member['uid'], time.time()))
                db.execute('INSERT INTO church_pair_codes VALUES(?,?,?)', (digest(code),member['uid'],time.time()+600))
            return jsonify(code=code, expiresInSeconds=600)

        @app.post('/api/church-admin/jobs/<job_id>/review')
        def review_job(job_id):
            member = self.dashboard_identity()
            self.member(member['uid'], write=True)
            body = request.get_json(silent=True) or {}
            if body.get('confirmedNoEmailSent') is not True:
                raise ChurchError('First check the exact assignment in Planning Center and confirm no email was sent.')
            with self.lock, self.db() as db:
                row = db.execute('SELECT * FROM church_email_jobs WHERE id=? AND uid=?', (job_id, member['uid'])).fetchone()
                if not row or row['state'] != 'needs_review':
                    raise ChurchError('This operation is not awaiting review.', 409)
                db.execute("UPDATE church_email_jobs SET state='cancelled',credential=NULL,detail='Operator checked Planning Center and closed the uncertain attempt.',updated_at=? WHERE id=?", (time.time(), job_id))
                self.audit(db, member['uid'], 'email-review-closed', job_id)
            return jsonify(closed=True, emailSent=False)

        @app.delete('/api/church-admin/link')
        def unlink():
            member = self.dashboard_identity()
            with self.db() as db:
                db.execute('DELETE FROM church_links WHERE uid=?', (member['uid'],))
                db.execute('DELETE FROM church_pair_codes WHERE uid=?', (member['uid'],))
                self.audit(db, member['uid'], 'chatgpt-unlinked', member['uid'])
                if hasattr(self,'oauth'): self.oauth.revoke_in(db,member['uid'])
            return jsonify(unlinked=True)

        @app.get('/api/church-admin/people')
        def people():
            member = self.dashboard_identity()
            self.member(member['uid'], owner=True)
            with self.db() as db:
                members = [dict(r) for r in db.execute('SELECT email,role,active FROM church_members ORDER BY email')]
                invites = [dict(r) for r in db.execute('SELECT email,role,active,expires_at FROM church_invites WHERE active=1 ORDER BY email')]
            return jsonify(members=members, invitations=invites)

        @app.post('/api/church-admin/people')
        def invite():
            member = self.dashboard_identity()
            self.member(member['uid'], owner=True)
            body = request.get_json(silent=True) or {}
            address, role = email(body.get('email')), body.get('role')
            if role not in ROLES:
                raise ChurchError('Choose Viewer or Scheduler.')
            with self.lock, self.db() as db:
                rows = db.execute('SELECT uid,role FROM church_members WHERE email=?', (address,)).fetchall()
                if any(r['role']=='owner' for r in rows):
                    raise ChurchError('The owner account cannot be changed here.')
                db.execute('INSERT OR REPLACE INTO church_invites VALUES(?,?,?,?,?)', (address,role,1,time.time()+14*86400,member['uid']))
                for row in rows:
                    db.execute('UPDATE church_members SET role=?,active=1 WHERE uid=?', (role,row['uid']))
                self.audit(db, member['uid'], 'access-granted', address)
            return jsonify(email=address,role=role,invitationUrl='https://jrdn-r.github.io/vision/admins/',emailSent=False)

        @app.delete('/api/church-admin/people')
        def revoke():
            member = self.dashboard_identity()
            self.member(member['uid'], owner=True)
            address = email((request.get_json(silent=True) or {}).get('email'))
            with self.lock, self.db() as db:
                rows = db.execute('SELECT uid,role FROM church_members WHERE email=?', (address,)).fetchall()
                if any(r['role']=='owner' for r in rows):
                    raise ChurchError('The owner account cannot be revoked here.')
                db.execute('UPDATE church_invites SET active=0 WHERE email=?', (address,))
                for row in rows:
                    db.execute('UPDATE church_members SET active=0 WHERE uid=?', (row['uid'],))
                    db.execute('DELETE FROM church_pair_codes WHERE uid=?', (row['uid'],))
                    db.execute('DELETE FROM church_links WHERE uid=?', (row['uid'],))
                    if hasattr(self,'oauth'): self.oauth.revoke_in(db,row['uid'])
                    db.execute("UPDATE church_email_jobs SET state='cancelled',detail='Access revoked.',credential=NULL WHERE uid=? AND state IN ('queued','waiting_for_login')", (row['uid'],))
                self.audit(db, member['uid'], 'access-revoked', address)
            return jsonify(revoked=True)

    def start(self):
        if self.settings.get('enabled') is True:
            from church_notifications import NotificationWorker
            self.worker = NotificationWorker(self)
            threading.Thread(target=self.worker.run, name='firstupcjax-notifications', daemon=True).start()
