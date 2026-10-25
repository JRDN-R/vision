"""Account upgrade policy and global processing choices; no client can grant access."""
import json
from flask import g, jsonify, request
from activity_dashboard import allowed
from luna_captions import LunaCaptionError

FIELDS = {'transcriptionProvider': ('local', 'gemini'), 'frameDescriptionEngine': ('auto', 'luna', 'local'),
          'describeFrames': bool, 'includeSoundEvents': bool}


def approved(app, uid):
    access = app.config.get('GEMINI_ACCESS')
    return bool(access and access.status(uid) == 'approved')


def project_owner(store, project_id):
    with store.db() as db:
        row = db.execute('SELECT owner_uid FROM projects WHERE id=?', (project_id,)).fetchone()
    return row['owner_uid'] if row else None


def luna_key(store, project_id):
    """Approval authorizes a saved account key, or the approving admin's key.

    The billing sponsor is recorded by the existing audited admin decision, never
    supplied by the browser. Revocation and removal from the admin allowlist take
    effect before the next provider request.
    """
    from sessions import unprotect_secret
    uid = project_owner(store, project_id)
    if not approved(store.app, uid):
        raise LunaCaptionError('Account Upgrade approval is required for Luna.')
    with store.db() as db:
        row = db.execute('SELECT openai_key_cipher FROM account_credentials WHERE uid=?', (uid,)).fetchone()
        if row is None:
            decision = db.execute("SELECT decided_by FROM gemini_access WHERE uid=? AND status='approved'", (uid,)).fetchone()
            sponsor = decision['decided_by'] if decision else None
            if sponsor and allowed(store.app.config, sponsor, 'firebase-google'):
                row = db.execute('SELECT openai_key_cipher FROM account_credentials WHERE uid=?', (sponsor,)).fetchone()
    if row is None:
        raise LunaCaptionError('Luna needs a saved OpenAI key on this account or its approving admin account.')
    try:
        return unprotect_secret(row[0])
    except Exception:
        raise LunaCaptionError('FUPCJ Server could not unlock the approved Luna credential.') from None


class AccountUpgrade:
    def __init__(self, app, db, Error):
        self.app, self.db, self.Error = app, db, Error
        app.add_url_rule('/api/account/upgrade', 'account_upgrade', self.access, methods=['GET', 'POST'])
        app.add_url_rule('/api/account/processing-preferences', 'account_processing_preferences', self.preferences, methods=['GET', 'PUT'])

    def initialize(self):
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS account_processing_preferences (uid TEXT PRIMARY KEY, settings_json TEXT NOT NULL DEFAULT '{}')")

    def identity(self):
        if getattr(g, 'auth_kind', '') != 'firebase-google':
            raise self.Error('Sign in to manage account upgrades.', 403)
        return g.uid

    def access(self):
        uid = self.identity()
        policy = self.app.config['GEMINI_ACCESS']
        status = policy.request_access(uid) if request.method == 'POST' else policy.status(uid)
        with self.db() as db:
            decision = db.execute('SELECT COALESCE(MAX(id),0) FROM gemini_access_decisions WHERE uid=?', (uid,)).fetchone()[0]
        return jsonify(status=status, decisionID=decision, approved=status == 'approved', preferences=self.values(uid),
                       canAdmin=allowed(self.app.config, uid, g.auth_kind, request.headers.get('Origin')))

    def values(self, uid):
        upgraded = approved(self.app, uid)
        defaults = dict(transcriptionProvider='gemini' if upgraded else 'local',
                        frameDescriptionEngine='auto' if upgraded else 'local',
                        describeFrames=True, includeSoundEvents=False)
        with self.db() as db:
            row = db.execute('SELECT settings_json FROM account_processing_preferences WHERE uid=?', (uid,)).fetchone()
        if row:
            defaults.update(json.loads(row['settings_json']))
        if not upgraded:
            defaults.update(transcriptionProvider='local', frameDescriptionEngine='local')
        return defaults

    def preferences(self):
        uid = self.identity()
        if request.method == 'PUT':
            patch = request.get_json(silent=True)
            if not isinstance(patch, dict) or not patch or set(patch) - FIELDS.keys():
                raise self.Error('Choose valid global processing preferences.', 400)
            for key, value in patch.items():
                rule = FIELDS[key]
                if (type(value) is not bool if rule is bool else not isinstance(value, str) or value not in rule):
                    raise self.Error('Invalid processing preference: ' + key, 400)
            if not approved(self.app, uid) and (patch.get('transcriptionProvider') == 'gemini' or patch.get('frameDescriptionEngine') in ('auto', 'luna')):
                raise self.Error('Request Account Upgrade approval before enabling cloud models.', 403)
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT settings_json FROM account_processing_preferences WHERE uid=?', (uid,)).fetchone()
                value = json.loads(row['settings_json']) if row else {}
                value.update(patch)
                db.execute('INSERT OR REPLACE INTO account_processing_preferences(uid,settings_json) VALUES(?,?)', (uid, json.dumps(value)))
        return jsonify(preferences=self.values(uid))
