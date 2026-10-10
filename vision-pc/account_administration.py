"""Owner-only Firebase account and local-data removal for Vision, Venture and Vortex.

Never expose service-account credentials to GitHub Pages. A permanently deleted
account leaves only a SHA-256 UID tombstone, so already-minted Firebase ID tokens
cannot re-enter the PC (the ordinary token verifier does not check revocation).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import shutil
import threading
import time

from flask import g, jsonify, request

from activity_dashboard import allowed, clean, public_id

PROJECT_ID = re.compile(r'[A-Za-z0-9_-]{16,120}\Z')
MEDIA_ID = re.compile(r'[0-9a-f]{24}\Z')
HASH_ID = re.compile(r'[0-9a-f]{64}\Z')


def name_parts(value):
    parts = clean(value, 200).split(None, 1)
    return (parts[0] if parts else '', parts[1] if len(parts) > 1 else '')


def remove_owned(path):
    """Only call with a server-constructed, ID-validated path."""
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


class AccountAdministration:
    def __init__(self, app, connect_db, sessions, vortex, transcriptions, media, documents):
        self.app, self.db = app, connect_db
        self.sessions, self.vortex = sessions, vortex
        self.transcriptions, self.media, self.documents = transcriptions, media, documents
        self.lock = threading.RLock()
        self.firebase_app = None
        self.register_routes()

    def initialize(self, config):
        # This is a private machine-local path, not a Firebase web API key.
        self.credential_path = str(config.get('firebaseAdminServiceAccount') or '')
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS account_deletions (
                id TEXT PRIMARY KEY, uid TEXT, email TEXT, state TEXT NOT NULL,
                requested_at REAL NOT NULL, completed_at REAL)""")

    def blocked(self, uid):
        if not uid or not uid.startswith('firebase:'):
            return False
        with self.db() as db:
            return db.execute('SELECT 1 FROM account_deletions WHERE id=?', (public_id(uid),)).fetchone() is not None

    def owner(self):
        return allowed(self.app.config, getattr(g, 'uid', ''), getattr(g, 'auth_kind', ''),
                       request.headers.get('Origin'))

    def administrators(self):
        path = Path(self.app.config['DATA_DIR']) / 'activity-admins.json'
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        return set(value['uids'])

    def firebase(self):
        if self.firebase_app is not None:
            return self.firebase_app
        path = Path(self.credential_path) if self.credential_path else None
        if not path or not path.is_file() or path.is_symlink():
            raise RuntimeError('Configure firebaseAdminServiceAccount on the Vision PC to enable account administration.')
        import firebase_admin
        from firebase_admin import credentials
        expected = self.app.config.get('FIREBASE_IDENTITY')
        if expected is None:
            raise RuntimeError('Enable Firebase identity verification on the Vision PC first.')
        # A credential for another Firebase project must never delete its users.
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if data.get('type') != 'service_account' or data.get('project_id') != expected.project_id:
            raise RuntimeError('The Firebase Admin service account must belong to this Vision Firebase project.')
        with self.lock:
            if self.firebase_app is None:
                self.firebase_app = firebase_admin.initialize_app(
                    credentials.Certificate(str(path)), {'projectId': expected.project_id},
                    name='vision-account-administration')
            return self.firebase_app

    def firebase_users(self):
        from firebase_admin import auth
        users = []
        page = auth.list_users(app=self.firebase())
        while page:
            users.extend(page.users)
            if len(users) > 20000:
                raise RuntimeError('The account directory exceeds the safe listing limit.')
            page = page.get_next_page()
        return users

    def directory(self, remote_users):
        by_id = {}
        with self.db() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            observed = list(db.execute('SELECT * FROM audit_users'))
            providers = dict(db.execute('SELECT uid,provider FROM audit_account_metadata')) if 'audit_account_metadata' in tables else {}
            apps = {}
            if 'audit_user_apps' in tables:
                for row in db.execute('SELECT uid,app FROM audit_user_apps'):
                    apps.setdefault(row['uid'], []).append(row['app'])
            counts = {}
            for label, table, col in [('projects', 'projects', 'owner_uid'),
                                      ('conversations', 'venture_conversations', 'uid'),
                                      ('vortex', 'vortex_jobs', 'uid')]:
                if table in tables:
                    counts[label] = dict(db.execute('SELECT '+col+',COUNT(*) FROM '+table+' GROUP BY '+col))
            pending = list(db.execute("SELECT id,uid,email,state FROM account_deletions WHERE state!='complete'"))
        for row in observed:
            identifier = public_id(row['uid'])
            first, last = name_parts(row['name'])
            by_id[identifier] = dict(id=identifier, name=clean(row['name']),
                firstName=first, lastName=last, email=clean(row['email']), provider=providers.get(row['uid'], 'unknown'),
                apps=apps.get(row['uid'], []), lastSeen=row['last_seen'],
                signIns=row['sign_in_sightings'], projects=counts.get('projects', {}).get(row['uid'], 0),
                conversations=counts.get('conversations', {}).get(row['uid'], 0),
                vortex=counts.get('vortex', {}).get(row['uid'], 0),
                deleteStatus='', canDelete=True)
        for record in remote_users:
            uid = 'firebase:' + record.uid
            identifier = public_id(uid)
            row = by_id.get(identifier)
            display = clean(record.display_name or (row or {}).get('name'), 200)
            first, last = name_parts(display)
            # Never fabricate a name from an email address.
            provider_ids = {p.provider_id for p in record.provider_data}
            provider = 'google' if 'google.com' in provider_ids else ('password' if record.email else 'unknown')
            if row is None:
                row = dict(id=identifier, name=display, firstName=first, lastName=last,
                           email=clean(record.email), provider=provider, apps=[], lastSeen=None,
                           signIns=0, projects=counts.get('projects', {}).get(uid, 0),
                           conversations=counts.get('conversations', {}).get(uid, 0),
                           vortex=counts.get('vortex', {}).get(uid, 0), deleteStatus='', canDelete=True)
                by_id[identifier] = row
            else:
                row.update(name=display or row['name'], firstName=first or row['firstName'],
                           lastName=last or row['lastName'], email=clean(record.email) or row['email'],
                           provider=providers.get(uid, provider))
        for record in pending:
            row = by_id.get(record['id'])
            if row is None:
                first, last = '', ''
                row = dict(id=record['id'], name='', firstName=first, lastName=last,
                           email=clean(record['email']), provider='unknown', apps=[], lastSeen=None,
                           signIns=0, projects=counts.get('projects', {}).get(record['uid'], 0),
                           conversations=counts.get('conversations', {}).get(record['uid'], 0),
                           vortex=counts.get('vortex', {}).get(record['uid'], 0),
                           canDelete=True)
                by_id[record['id']] = row
            row['deleteStatus'] = record['state']
        admin_ids = {public_id(uid) for uid in self.administrators()}
        for row in by_id.values():
            row['canDelete'] = row['id'] not in admin_ids
        return sorted(by_id.values(), key=lambda v: ((v['name'] or v['email']).lower(), v['id']))

    def identify(self, identifier):
        """Resolve only server-known Firebase UIDs; a client never supplies a raw UID."""
        with self.db() as db:
            record = db.execute('SELECT uid,email,state FROM account_deletions WHERE id=?',
                                (identifier,)).fetchone()
            if record:
                return record['uid'], clean(record['email']), record['state']
            for row in db.execute('SELECT uid,email FROM audit_users'):
                if public_id(row['uid']) == identifier:
                    return row['uid'], clean(row['email']), ''
        for account in self.firebase_users():
            uid = 'firebase:' + account.uid
            if public_id(uid) == identifier:
                return uid, clean(account.email), ''
        raise LookupError('This account no longer exists.')

    def context_db(self):
        engine = self.sessions.context.engine
        if engine:
            return engine.db()
        path = Path(self.app.config['DATA_DIR']) / 'context' / 'context.sqlite3'
        if not path.is_file() or path.is_symlink():
            return None
        return sqlite3.connect(path, timeout=30)

    def active_work(self, uid, projects):
        with self.db() as db:
            for table, where, args in (
                ('vortex_jobs', "uid=? AND status IN ('queued','processing')", (uid,)),
                ('jobs', "uid=? AND status IN ('queued','processing')", (uid,)),
                ('venture_dictation_requests', "uid=? AND status='processing'", (uid,))):
                if db.execute('SELECT 1 FROM '+table+' WHERE '+where+' LIMIT 1', args).fetchone():
                    return True
            for pid in projects:
                for table, where in (
                    ('project_runs', "project_id=? AND status IN ('queued','preparing','submitting','in_progress','saving')"),
                    ('local_transcriptions', "project_id=? AND status IN ('queued','processing')"),
                    ('uploaded_media', "project_id=? AND status IN ('queued','processing')"),
                    ('document_jobs', "project_id=? AND status IN ('queued','processing')")):
                    if db.execute('SELECT 1 FROM '+table+' WHERE '+where+' LIMIT 1', (pid,)).fetchone():
                        return True
        context = self.context_db()
        if context:
            try:
                if context.execute("SELECT 1 FROM context_jobs WHERE owner=? AND status IN ('queued','processing') LIMIT 1",
                                   (uid,)).fetchone():
                    return True
            finally:
                context.close()
        return False

    def owned(self, uid):
        with self.db() as db:
            projects = {row[0] for row in db.execute('SELECT id FROM projects WHERE owner_uid=?', (uid,))}
            # A native Venture conversation is an account-owned project too.
            projects.update(row[0] for row in db.execute(
                'SELECT id FROM venture_conversations WHERE uid=? AND native=1', (uid,)))
            media_ids = {table: [row[0] for row in db.execute(
                'SELECT id FROM '+table+' WHERE project_id IN (SELECT id FROM projects WHERE owner_uid=?)', (uid,))]
                for table in ('local_transcriptions', 'uploaded_media', 'document_jobs')}
            # Include jobs belonging to native Venture projects (normally no media jobs).
            for table in media_ids:
                media_ids[table].extend(row[0] for pid in projects for row in db.execute(
                    'SELECT id FROM '+table+' WHERE project_id=?', (pid,)))
            uploads = [(row[0],row[1]) for row in db.execute(
                'SELECT project_id,request_id FROM uploaded_media_uploads WHERE project_id IN '
                '(SELECT id FROM projects WHERE owner_uid=?)', (uid,))]
            vortex_ids = [row[0] for row in db.execute('SELECT id FROM vortex_jobs WHERE uid=?', (uid,))]
            youtube_ids = [row[0] for row in db.execute('SELECT id FROM jobs WHERE uid=?', (uid,))]
        return projects, media_ids, uploads, vortex_ids, youtube_ids

    def erase_files(self, uid, projects, media_ids, uploads, vortex_ids, youtube_ids):
        base = Path(self.app.config['DATA_DIR'])
        for pid in projects:
            if not PROJECT_ID.fullmatch(pid):
                raise ValueError('A saved project identifier is invalid; no unverified path was removed.')
            remove_owned(base / 'projects' / pid)
            remove_owned(base / 'context' / 'sources' / hashlib.sha256((uid+'\0'+pid).encode()).hexdigest())
        for table, directory in [('local_transcriptions','transcriptions'),
                                 ('uploaded_media','uploaded-media'), ('document_jobs','documents')]:
            for job in set(media_ids[table]):
                if not MEDIA_ID.fullmatch(job):
                    raise ValueError('A saved media identifier is invalid; cleanup was stopped.')
                remove_owned(base / directory / job)
        for pid, request_id in uploads:
            remove_owned(base / 'uploaded-media' / '.uploads' /
                hashlib.sha256((pid+'\0'+request_id).encode()).hexdigest())
        for job in vortex_ids:
            if not MEDIA_ID.fullmatch(job):
                raise ValueError('A Vortex identifier is invalid; cleanup was stopped.')
            remove_owned(base / 'vortex' / job)
        for job in youtube_ids:
            if not MEDIA_ID.fullmatch(job):
                raise ValueError('A YouTube identifier is invalid; cleanup was stopped.')
            for suffix in ('.json','.tmp'):
                remove_owned(base / 'results' / (job+suffix))
        remove_owned(self.sessions.venture.user_root(uid))
        audit = self.app.config.get('AUDIT_LOGS')
        if audit:
            remove_owned(audit.directory / audit._filename(uid))

    def erase_context(self, uid):
        context = self.context_db()
        if not context:
            return
        try:
            context.execute('BEGIN IMMEDIATE')
            context.execute('DELETE FROM context_fts WHERE rowid IN (SELECT id FROM context_chunks WHERE owner=?)', (uid,))
            for table in ('context_chunks','context_sources','context_cache','context_vectors',
                          'context_heads','context_generations','context_jobs'):
                context.execute('DELETE FROM '+table+' WHERE owner=?', (uid,))
            context.commit()
        except Exception:
            context.rollback()
            raise
        finally:
            context.close()

    def erase_database(self, uid, projects):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
            for pid in projects:
                # No arbitrary, client-supplied IDs ever reach this method.
                for table, col in (('run_events','run_id'),('run_artifacts','run_id'),
                                   ('context_responses','run_id'),('context_uploads','run_id'),
                                   ('venture_memory_indexed','run_id')):
                    db.execute('DELETE FROM '+table+' WHERE '+col+' IN '
                               '(SELECT id FROM project_runs WHERE project_id=?)', (pid,))
                for table, col in (('local_transcriptions','project_id'),('uploaded_media','project_id'),
                                   ('uploaded_media_uploads','project_id'),('document_jobs','project_id'),
                                   ('venture_search','cid'),('context_sync_jobs','project_id'),
                                   ('project_runs','project_id')):
                    if table in existing:
                        db.execute('DELETE FROM '+table+' WHERE '+col+'=?', (pid,))
                db.execute('DELETE FROM venture_memory WHERE cid=?', (pid,))
                db.execute('DELETE FROM venture_conversations WHERE id=? AND uid=?', (pid,uid))
                db.execute('DELETE FROM projects WHERE id=? AND owner_uid=?', (pid,uid))
            # Non-native Venture records can also exist, without owning their board project.
            db.execute('DELETE FROM venture_memory WHERE uid=?', (uid,))
            db.execute('DELETE FROM venture_conversations WHERE uid=?', (uid,))
            for table, col in (('venture_preferences','uid'),('venture_workspace_preferences','uid'),
                               ('venture_funding','uid'),('venture_calibrations','uid'),
                               ('venture_ledger','uid'),('venture_container_costs','uid'),
                               ('venture_dictation_requests','uid'),('account_credentials','uid'),
                               ('gemini_access','uid'),('gemini_access_decisions','uid'),
                               ('gemini_usage','uid'),('vortex_jobs','uid'),('responses','uid'),
                               ('jobs','uid'),('audit_events','uid'),('audit_signins','uid'),
                               ('audit_account_metadata','uid'),('audit_user_apps','uid'),
                               ('audit_users','uid')):
                if table in existing:
                    db.execute('DELETE FROM '+table+' WHERE '+col+'=?', (uid,))
            # Decisions performed by this former admin must not retain their identity.
            if 'gemini_access_decisions' in existing:
                db.execute('DELETE FROM gemini_access_decisions WHERE actor_uid=?', (uid,))
            db.execute("""UPDATE account_deletions SET uid=NULL,email=NULL,state='complete',completed_at=?
                          WHERE id=?""", (time.time(),public_id(uid)))

    def delete(self, identifier, confirmation):
        if not HASH_ID.fullmatch(identifier):
            raise ValueError('Choose a valid account.')
        with self.lock:
            uid, email, state = self.identify(identifier)
            if state == 'complete':
                return {'deleted': True, 'pending': False}
            if not uid or uid == g.uid or uid in self.administrators():
                raise PermissionError('Owner and administrator accounts cannot be deleted here.')
            if not email or confirmation != 'DELETE '+email:
                raise ValueError('Type DELETE followed by the exact account email to confirm.')
            from firebase_admin import auth
            app = self.firebase()  # No tombstone or local changes unless admin credentials are ready.
            projects, media_ids, uploads, vortex_ids, youtube_ids = self.owned(uid)
            if self.active_work(uid, projects):
                raise BlockingIOError('This user still has running or queued work. Finish or cancel their jobs, then retry deletion.')
            # Tombstone BEFORE revoking Firebase or removing files. A valid, already-
            # issued JWT is otherwise usable for up to an hour after Firebase removal.
            with self.db() as db:
                db.execute("""INSERT OR IGNORE INTO account_deletions(id,uid,email,state,requested_at)
                              VALUES(?,?,?,'pending',?)""", (identifier,uid,email,time.time()))
            try:
                auth.delete_user(uid.removeprefix('firebase:'), app=app)
            except auth.UserNotFoundError:
                pass  # Retry after an interrupted successful Firebase deletion.
            # If the API or disk fails, the tombstone stays and another owner
            # request can safely resume. Never report success for a partial purge.
            if self.active_work(uid, projects):
                raise BlockingIOError('Work is still stopping. This account is blocked; retry to finish removing its files.')
            with self.sessions.venture.mirror_lock:
                self.erase_files(uid, projects, media_ids, uploads, vortex_ids, youtube_ids)
                self.erase_context(uid)
                self.erase_database(uid, projects)
            audit = self.app.config.get('AUDIT_LOGS')
            if audit:
                audit.seen = {key:val for key,val in audit.seen.items() if key[0] != uid}
                audit._index()
                audit.event(g.uid, 'account_deleted', outcome='complete')
            return {'deleted': True, 'pending': False}

    def register_routes(self):
        def respond(value, status=200):
            response = jsonify(value)
            response.status_code = status
            response.headers['Cache-Control'] = 'private, no-store'
            return response

        @self.app.get('/api/admin/accounts')
        def admin_accounts():
            if not self.owner():
                return respond({'code':'ACTIVITY_FORBIDDEN','error':'Owner access required.'},403)
            try:
                accounts = self.directory(self.firebase_users())
                return respond({'available':True,'users':accounts})
            except Exception as error:
                self.app.logger.warning('Firebase account directory unavailable (%s)',type(error).__name__)
                return respond({'available':False,'users':[],
                                'error':str(error) if isinstance(error,RuntimeError) else
                                        'Firebase account directory is temporarily unavailable.'},503)

        @self.app.post('/api/admin/accounts/delete')
        def admin_account_delete():
            if not self.owner():
                return respond({'code':'ACTIVITY_FORBIDDEN','error':'Owner access required.'},403)
            request.max_content_length=4096
            body = request.get_json(silent=True)
            if not isinstance(body,dict) or set(body)!={'userId','confirmation'}:
                return respond({'error':'Select one account and confirm its email.'},400)
            try:
                result=self.delete(body['userId'],body['confirmation'])
                return respond(result)
            except (TypeError,ValueError,LookupError) as error:
                return respond({'error':str(error)},404 if isinstance(error,LookupError) else 400)
            except PermissionError as error:
                return respond({'error':str(error)},403)
            except BlockingIOError as error:
                return respond({'error':str(error)},409)
            except Exception as error:
                # Never return raw SDK exceptions, file paths or credentials.
                self.app.logger.error('Account deletion needs retry (%s)',type(error).__name__)
                return respond({'error':'Account deletion could not finish. No success was reported. '
                                        'If already started, the account remains blocked. '
                                        'Check PC connectivity and retry.'},503)
