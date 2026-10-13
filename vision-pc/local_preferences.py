"""Versioned, account-wide model selection; activation is an operator action."""
import json
import time

MODEL = 'qwen3.8-local'
EPOCH = 'qwen3_8_local_v1'
DISCLOSURE = 'qwen-local-vision-v2'


def local_settings(settings, max_output=2048):
    return {**settings, 'model': MODEL, 'maxOutputTokens': max_output,
            'runOptions': dict(mode='auto', effort='auto', verbosity='auto',
                               webSearch=False, codeInterpreter=False)}


class LocalPreferences:
    def __init__(self, local):
        self.local = local
        self.v = local.sessions.venture
        self.db, self.Error = self.v.db, self.v.Error

    def initialize(self):
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS venture_selection (
                uid TEXT PRIMARY KEY, epoch TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0,
                explicit_at REAL, disclosure TEXT, profiles_json TEXT NOT NULL DEFAULT '{}')''')

    def _read(self, db, uid):
        from venture import DEFAULT_SETTINGS
        db.execute('INSERT OR IGNORE INTO venture_selection(uid) VALUES(?)', (uid,))
        meta = dict(db.execute('SELECT * FROM venture_selection WHERE uid=?', (uid,)).fetchone())
        row = db.execute('SELECT settings_json FROM venture_preferences WHERE uid=?', (uid,)).fetchone()
        settings = json.loads(row[0]) if row else dict(DEFAULT_SETTINGS)
        if self.local.settings.get('defaultEpoch') == EPOCH and meta['epoch'] != EPOCH and self.local.migration_eligible():
            profiles = json.loads(meta['profiles_json'])
            profiles[settings['model']] = settings
            settings = local_settings(settings, self.local.max_output)
            meta.update(epoch=EPOCH, revision=meta['revision'] + 1, profiles_json=json.dumps(profiles))
            db.execute('UPDATE venture_selection SET epoch=?,revision=?,profiles_json=? WHERE uid=?',
                       (EPOCH, meta['revision'], meta['profiles_json'], uid))
            self._save(db, uid, settings)
        return settings, meta

    @staticmethod
    def _save(db, uid, settings):
        db.execute('''INSERT INTO venture_preferences(uid,settings_json,updated_at) VALUES(?,?,?)
            ON CONFLICT(uid) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at''',
                   (uid, json.dumps(settings), time.time()))

    def get(self, uid):
        from venture import DEFAULT_SETTINGS
        with self.db() as db:
            row = db.execute('SELECT * FROM venture_selection WHERE uid=?', (uid,)).fetchone()
            if row and not (self.local.settings.get('defaultEpoch') == EPOCH and row['epoch'] != EPOCH):
                meta = dict(row)
                saved = db.execute('SELECT settings_json FROM venture_preferences WHERE uid=?', (uid,)).fetchone()
                settings = json.loads(saved[0]) if saved else dict(DEFAULT_SETTINGS)
            else:
                db.execute('BEGIN IMMEDIATE')
                settings, meta = self._read(db, uid)
        return dict(settings=settings, preferenceRevision=meta['revision'], defaultEpoch=meta['epoch'],
                    explicitSelection=bool(meta['explicit_at']), disclosureVersion=DISCLOSURE,
                    disclosureAcknowledged=meta['disclosure'] == DISCLOSURE,
                    profiles=json.loads(meta['profiles_json']))

    def put(self, uid, body):
        if not isinstance(body, dict):
            raise self.Error('Invalid preferences.')
        modern = isinstance(body.get('settings'), dict)
        settings = self.v.settings(body['settings'] if modern else body)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old, meta = self._read(db, uid)
            if modern:
                if (body.get('preferenceRevision') != meta['revision'] or
                        body.get('defaultEpoch', '') != meta['epoch']):
                    raise self.Error('Your model settings changed on another device. Refresh before saving.', 409)
                explicit = body.get('explicitModelSelection') is True
                if settings['model'] != old['model'] and not explicit:
                    raise self.Error('Select the model explicitly before changing its account default.', 409)
            else:
                if meta['epoch'] or meta['explicit_at']:
                    raise self.Error('Refresh Vision to update its versioned model settings.', 409)
                explicit = False
            profiles = json.loads(meta['profiles_json'])
            profiles[old['model']] = old
            profiles[settings['model']] = settings
            self._save(db, uid, settings)
            db.execute('UPDATE venture_selection SET revision=revision+1,explicit_at=?,profiles_json=? WHERE uid=?',
                       (time.time() if explicit else meta['explicit_at'], json.dumps(profiles), uid))
        return self.get(uid)

    def effective(self, uid, settings):
        value = self.get(uid)
        return value['settings'] if value['defaultEpoch'] or value['explicitSelection'] else settings

    def validate_send(self, uid, options):
        value = self.get(uid)
        if value['defaultEpoch'] or value['explicitSelection']:
            if (options.get('preferenceRevision') != value['preferenceRevision'] or
                    options.get('model') != value['settings']['model'] or
                    (options.get('model') == MODEL and
                     options.get('qwenUncensored',False) != value['settings'].get('qwenUncensored',False))):
                raise self.Error('Your account model changed. Refresh Venture; your draft has been kept.', 409)
        if options.get('model') == MODEL and not value['disclosureAcknowledged']:
            raise self.Error('Acknowledge the Qwen local-server notice before sending.', 409, 'local-disclosure-required')

    def acknowledge(self, uid, version):
        if version != DISCLOSURE:
            raise self.Error('Refresh the local-model notice.', 409)
        self.get(uid)
        with self.db() as db:
            db.execute('UPDATE venture_selection SET disclosure=? WHERE uid=?', (version, uid))
        return self.get(uid)
