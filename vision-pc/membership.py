"""Vision membership is independent of paid-feature approval; deny by default."""
import time
import requests
import math
from flask import g, jsonify, request
from activity_dashboard import allowed, public_id, clean

OWNER_EMAIL = 'rappsongs@gmail.com'

class Membership:
    def __init__(self, app, db, Error, accounts):
        self.app, self.db, self.Error, self.accounts = app, db, Error, accounts
        app.add_url_rule('/api/account/membership', 'membership_status', self.current)
        app.add_url_rule('/api/admin/memberships', 'membership_directory', self.directory)
        app.add_url_rule('/api/admin/memberships/decide', 'membership_decide', self.decide, methods=['POST'])

    def initialize(self):
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS membership_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memberships (
                    uid TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('pending','approved','denied')),
                    requested_at REAL NOT NULL, decided_at REAL, decided_by TEXT);
                CREATE TABLE IF NOT EXISTS membership_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL,
                    actor_uid TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL);
            ''')
            db.execute('BEGIN IMMEDIATE')
            first = db.execute("SELECT value FROM membership_settings WHERE key='cutoff'").fetchone()
            if not first:
                cutoff = time.time()
                db.execute("INSERT INTO membership_settings VALUES('cutoff',?)", (str(cutoff),))
                # Snapshot BEFORE any new request is audited. Reboots never rerun it.
                db.execute("INSERT OR IGNORE INTO memberships SELECT uid,'approved',first_seen,?,'migration' FROM audit_users WHERE uid LIKE 'firebase:%'", (cutoff,))
            self.cutoff = float(db.execute("SELECT value FROM membership_settings WHERE key='cutoff'").fetchone()[0])
            owner = db.execute("SELECT value FROM membership_settings WHERE key='owner_uid'").fetchone()
            self.app.config['MEMBERSHIP_OWNER_UID'] = owner[0] if owner else ''
    def observe(self, uid, claims, token):
        with self.db() as db:
            known = db.execute("SELECT 1 FROM memberships WHERE uid=?", (uid,)).fetchone()
        existing = False
        admin = allowed(self.app.config, uid, "firebase-google", request.headers.get("Origin"))
        owner_claim = (claims.get("email", "").lower() == OWNER_EMAIL and claims.get("email_verified") is True
                       and claims.get("firebase", {}).get("sign_in_provider") == "google.com")
        if not known and not admin and not owner_claim:
            existing = self.created_before_cutoff(uid, token)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if (claims.get('email', '').lower() == OWNER_EMAIL and claims.get('email_verified') is True
                    and claims.get('firebase', {}).get('sign_in_provider') == 'google.com'):
                db.execute("INSERT OR IGNORE INTO membership_settings VALUES('owner_uid',?)", (uid,))
                self.app.config['MEMBERSHIP_OWNER_UID'] = db.execute("SELECT value FROM membership_settings WHERE key='owner_uid'").fetchone()[0]
            admin = allowed(self.app.config, uid, 'firebase-google', request.headers.get('Origin'))
            db.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?,NULL,NULL)', (uid, 'approved' if admin or existing else 'pending', time.time()))
            if admin:
                db.execute("UPDATE memberships SET status='approved' WHERE uid=?", (uid,))
        return self.status(uid)

    def created_before_cutoff(self, uid, token):
        # Firebase REST getAccountInfo returns server-owned creation time for
        # this token. Never trust client metadata or JWT auth_time as account age.
        key = 'AIzaSyDh1AHhi41cAXcSFnvFkfeZWmxc8gI0zSg'  # Existing public Vision Firebase web key.
        try:
            response = requests.post('https://identitytoolkit.googleapis.com/v1/accounts:lookup',
                params={'key': key}, json={'idToken': token}, timeout=(3, 6))
            try:
                response.raise_for_status()
                users = response.json()['users']
                if len(users) != 1 or users[0]['localId'] != uid.removeprefix('firebase:') or users[0].get('disabled'):
                    raise ValueError('Identity mismatch')
                created = float(users[0]['createdAt']) / 1000
                if not math.isfinite(created) or created <= 0:
                    raise ValueError('Invalid account creation time')
                return created < self.cutoff
            finally:
                response.close()
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):
            raise self.Error('Could not check existing account access. Please retry when Firebase is available.', 503, 'membership-check-unavailable') from None

    def status(self, uid):
        with self.db() as db:
            row = db.execute('SELECT status FROM memberships WHERE uid=?', (uid,)).fetchone()
        return row[0] if row else 'pending'

    def require(self, uid):
        if uid.startswith('firebase:') and self.status(uid) != 'approved':
            raise self.Error('Thank you for your request. Awaiting account approval.', 403, 'membership-required')

    def current(self):
        if getattr(g, 'auth_kind', '') != 'firebase-google':
            raise self.Error('Sign in to check membership.', 401)
        status = self.status(g.uid)
        return jsonify(status=status, approved=status == 'approved', admin=self.accounts.owner())

    def directory(self):
        if not self.accounts.owner():
            raise self.Error('Administrator access required.', 403)
        with self.db() as db:
            rows = db.execute('''SELECT m.*,a.name,a.email FROM memberships m
                LEFT JOIN audit_users a ON a.uid=m.uid ORDER BY m.requested_at DESC LIMIT 2000''').fetchall()
        return jsonify(requests=[dict(userId=public_id(r['uid']), name=clean(r['name']), email=clean(r['email']),
                    status=r['status'], requestedAt=r['requested_at']) for r in rows],
                    pendingCount=sum(r['status']=='pending' for r in rows))

    def decide(self):
        if not self.accounts.owner():
            raise self.Error('Administrator access required.', 403)
        request.max_content_length = 4096
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {'userId','decision'} or body['decision'] not in ('approved','denied'):
            raise self.Error('Choose approved or denied.', 400)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = next((r for r in db.execute('SELECT uid,status FROM memberships') if public_id(r['uid']) == body['userId']), None)
            if not row:
                raise self.Error('Account request not found.', 404)
            # Membership admission is separate from paid-feature revocation.
            if row['status'] == 'approved':
                raise self.Error('This account is already approved.', 409)
            if self.accounts.blocked(row['uid']):
                raise self.Error('This account has been removed.', 409)
            now = time.time()
            db.execute('UPDATE memberships SET status=?,decided_at=?,decided_by=? WHERE uid=?', (body['decision'],now,g.uid,row['uid']))
            db.execute('INSERT INTO membership_decisions(uid,actor_uid,status,created_at) VALUES(?,?,?,?)', (row['uid'],g.uid,body['decision'],now))
        return jsonify(status=body['decision'])
