"""Offline regression tests for activity access, real SQLite queries and setup.
Run: python -m unittest discover -s tests -p test_activity_dashboard.py -v
Flask's tiny response adapter is mocked; real Firebase/Windows integration needs
an installed PC. No live accounts or network credentials are used in these tests.
"""
import importlib.util
import json
import logging
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vision-pc'))
def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'vision-pc' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
api = load('activity_dashboard')
audit_module = load('audit_logs')
setup = load('setup_activity')
SCHEMA = '''CREATE TABLE audit_users(uid TEXT PRIMARY KEY,name TEXT,email TEXT,
 first_seen REAL,last_seen REAL,auth_time REAL,sign_in_sightings INTEGER);
 CREATE TABLE audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT,uid TEXT,
 created_at REAL,event TEXT,details TEXT);'''

class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try: return super().__exit__(*args)
        finally: self.close()

class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.database = self.data / 'vision.sqlite3'
        self.config = {'DATA_DIR': self.data, 'BACKEND_URL': 'https://pc.example.ts.net'}
        with self.db() as db:
            db.executescript(SCHEMA)
            db.executemany('INSERT INTO audit_users VALUES(?,?,?,?,?,?,?)',[
                ('firebase:owner','Jordan','one@example.test',100,300,100,2),
                ('firebase:second','Jordan','two@example.test',200,400,200,1)])
            db.executemany('INSERT INTO audit_events(uid,created_at,event,details) VALUES(?,?,?,?)',[
                ('firebase:owner',110,'google_sign_in','{}'),
                ('firebase:owner',250,'project_saved','{"requestBytes":1234,"httpStatus":200,"prompt":"PRIVATE","apiKey":"SECRET"}'),
                ('firebase:second',410,'video_uploaded','{"outcome":"rejected","httpStatus":413}')])
    def tearDown(self): self.temp.cleanup()
    def db(self):
        db = sqlite3.connect(self.database, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        return db
    def access(self, value=None):
        (self.data / 'activity-admins.json').write_text(json.dumps(value or {'uids':['firebase:owner']}))
    def test_access_is_explicit_and_fail_closed(self):
        self.assertFalse(api.allowed(self.config,'firebase:owner','firebase-google'))
        self.access()
        self.assertTrue(api.allowed(self.config,'firebase:owner','firebase-google','https://jrdn-r.github.io'))
        for uid,kind,origin in [('firebase:second','firebase-google',None),('firebase:owner','private-pc',None),('installation-owner','private-pc',None),('firebase:owner','firebase-google','null'),('firebase:owner','firebase-google','https://evil.example')]:
            self.assertFalse(api.allowed(self.config,uid,kind,origin))
    def test_revocation_is_effective_on_next_read(self):
        self.access();self.assertTrue(api.allowed(self.config,'firebase:owner','firebase-google'))
        self.access({'uids':['firebase:second']})
        self.assertFalse(api.allowed(self.config,'firebase:owner','firebase-google'))
    def test_bad_access_file_never_allows(self):
        for value in [[],{}, {'uids':'firebase:owner'}, {'uids':['firebase:owner',None]}, {'uids':[]}, {'uids':['firebase:']}, {'uids':['firebase:owner']*101}]:
            self.access(value)
            if not value: (self.data / 'activity-admins.json').write_text(json.dumps(value))
            self.assertFalse(api.allowed(self.config,'firebase:owner','firebase-google'))
        (self.data / 'activity-admins.json').write_text('{invalid')
        self.assertFalse(api.allowed(self.config,'firebase:owner','firebase-google'))
    def test_snapshot_names_separate_accounts_and_safe_metadata(self):
        value=api.snapshot_payload(self.db)
        self.assertEqual([u['name'] for u in value['users']],['Jordan','Jordan'])
        self.assertEqual(len(set(u['id'] for u in value['users'])),2)
        self.assertEqual(value['totalEvents'],3)
        self.assertEqual([e['id'] for e in value['events']],[3,2,1])
        text=json.dumps(value)
        for secret in ('PRIVATE','SECRET','firebase:owner','firebase:second'):
            self.assertNotIn(secret,text)
        self.assertEqual(value['events'][1]['details'],{'requestBytes':1234,'httpStatus':200})
    def test_selected_person_and_load_older(self):
        owner=api.public_id('firebase:owner')
        value=api.snapshot_payload(self.db,owner,1)
        self.assertEqual(len(value['events']),1);self.assertTrue(value['hasMore'])
        self.assertEqual(value['totalEvents'],2)
        all_events=api.snapshot_payload(self.db,owner,2000)
        self.assertEqual(len(all_events['events']),2)
        self.assertTrue(all(e['userId']==owner for e in all_events['events']))
    def test_revision_changes_for_user_and_event(self):
        initial=api.snapshot_payload(self.db)
        self.assertTrue(api.snapshot_payload(self.db,version=initial['version'])['unchanged'])
        with self.db() as db:
            db.execute('INSERT INTO audit_users VALUES(?,?,?,?,?,?,?)',('firebase:new','Andrea','three@example.test',450,450,450,1))
        changed=api.snapshot_payload(self.db,version=initial['version'])
        self.assertNotIn('unchanged',changed);self.assertEqual(len(changed['users']),3)
        with self.db() as db:
            db.execute("INSERT INTO audit_events(uid,created_at,event,details) VALUES('firebase:new',460,'google_sign_in','{}')")
        self.assertNotEqual(api.snapshot_payload(self.db)['version'],changed['version'])
    def test_empty_users_and_unknown_person(self):
        with self.db() as db: db.execute('DELETE FROM audit_users');db.execute('DELETE FROM audit_events')
        value=api.snapshot_payload(self.db);self.assertEqual(value['users'],[]);self.assertEqual(value['events'],[])
        with self.assertRaises(LookupError):api.snapshot_payload(self.db,'0'*64)
    def test_input_validation(self):
        for limit in (0,2001,-1,True,'100'):
            with self.assertRaises(ValueError):api.snapshot_payload(self.db,limit=limit)
        for person in ("' OR 1=1 --",'../Users.txt','firebase:owner'):
            with self.assertRaises(ValueError):api.snapshot_payload(self.db,person)
    def test_details_whitelist_and_controls(self):
        self.assertEqual(api.details('{"requestBytes":true,"outputBytes":NaN,"outcome":"<script>","jobType":"youtube","secret":1}'),{'jobType':'youtube'})
        for raw in ('null','[]','not json',None):self.assertEqual(api.details(raw),{})
        self.assertEqual(api.clean('A\nB\u202eC'),'A B C')
    def test_route_denies_before_reading_database(self):
        fake_g=SimpleNamespace(uid='firebase:second',auth_kind='firebase-google')
        fake_request=SimpleNamespace(headers={'Origin':'https://jrdn-r.github.io'},args={})
        fake_flask=SimpleNamespace(g=fake_g,request=fake_request,jsonify=lambda value:SimpleNamespace(json=value,status_code=200,headers={}))
        app=SimpleNamespace(config=self.config,view_functions={})
        app.add_url_rule=lambda url,name,fn,methods:app.view_functions.update({name:fn})
        calls=[]
        self.access()
        with patch.dict(sys.modules,{'flask':fake_flask}):
            api.register(app,lambda:calls.append('db'))
        reply=app.view_functions['vision_activity']()
        self.assertEqual(reply.status_code,403);self.assertEqual(calls,[])
        self.assertIn('no-store',reply.headers['Cache-Control'])
        reply=app.view_functions['vision_vortex_admin']()
        self.assertEqual(reply.status_code,403);self.assertEqual(calls,[])

    def test_modules_filter_before_limit_and_change_revision(self):
        with self.db() as db:
            db.execute("INSERT INTO audit_events(uid,created_at,event,details) VALUES('firebase:owner',120,'audio_received','{}')")
            db.executemany("INSERT INTO audit_events(uid,created_at,event,details) VALUES('firebase:owner',500,'project_saved','{}')", [()] * 110)
        all_events=api.snapshot_payload(self.db,limit=1)
        logins=api.snapshot_payload(self.db,limit=1,module='logins',version=all_events['version'])
        self.assertEqual(logins['events'][0]['kind'],'google_sign_in')
        self.assertEqual(logins['totalEvents'],1)
        self.assertFalse(logins['hasMore'])
        transcription=api.snapshot_payload(self.db,limit=1,module='transcriptions')
        self.assertEqual(transcription['events'][0]['kind'],'audio_received')
        self.assertEqual(transcription['moduleCounts']['transcriptions'],1)
        with self.assertRaises(ValueError): api.snapshot_payload(self.db,module="all' OR 1=1")

    def test_providers_apps_and_shared_session_do_not_inflate_signins(self):
        audit=audit_module.AuditLogs(self.root/'logs',self.db,logging.getLogger('test'))
        audit.initialize()
        # Existing rows are not retroactively described as Google accounts.
        self.assertTrue(all(u['provider']=='unknown' for u in api.snapshot_payload(self.db)['users']))
        claims={'name':'Casey','email':'casey@example.test','auth_time':600,'firebase':{'sign_in_provider':'password'}}
        audit.identity('firebase:casey',claims,app='vision')
        audit.identity('firebase:casey',claims,app='vortex')
        audit.identity('firebase:casey',claims,app='vortex')
        value=api.snapshot_payload(self.db,api.public_id('firebase:casey'),module='logins')
        person=next(u for u in value['users'] if u['id']==api.public_id('firebase:casey'))
        self.assertEqual(person['provider'],'password');self.assertEqual(person['signIns'],1)
        self.assertEqual([a['app'] for a in person['apps']],['vision','vortex'])
        self.assertEqual([e['kind'] for e in value['events']],['app_first_seen','account_sign_in'])
        audit.identity('firebase:casey',{**claims,'auth_time':700,'firebase':{'sign_in_provider':'google.com'}},app='vortex')
        value=api.snapshot_payload(self.db,api.public_id('firebase:casey'),module='logins')
        self.assertEqual(value['events'][0]['details']['authProvider'],'google')
        audit.request_event('firebase:casey',SimpleNamespace(path='/api/vortex/jobs',method='POST',content_length=10),202,.5)
        value=api.snapshot_payload(self.db,api.public_id('firebase:casey'),module='vortex')
        self.assertEqual(value['events'][0]['kind'],'vortex_submitted')
        self.assertEqual(value['events'][0]['details']['app'],'vortex')

    def test_vortex_existing_jobs_are_private_scoped_and_expiry_aware(self):
        self.assertFalse(api.vortex_payload(self.db)['available'])
        with self.db() as db:
            db.execute('''CREATE TABLE vortex_jobs(id TEXT,uid TEXT,kind TEXT,quality TEXT,status TEXT,
                progress REAL,output_size INTEGER,options_json TEXT,created_at REAL,updated_at REAL,
                completed_at REAL,expires_at REAL,delete_requested INTEGER,input TEXT,filename TEXT,media_json TEXT)''')
            for id,uid,status,expires,deleted in [('a','owner','complete',1100,0),('b','second','complete',900,0),('c','owner','queued',None,0),('d','owner','complete',1200,1)]:
                db.execute('INSERT INTO vortex_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (id,'firebase:'+uid,'download','max',status,50,1234,'{"downloadMode":"audio","audioFormat":"mp3","secret":"PRIVATE"}',100,200,200,expires,deleted,'SECRET-URL','SECRET-FILE','SECRET-TITLE'))
        with patch.object(api.time,'time',return_value=1000):
            value=api.vortex_payload(self.db,limit=1)
            scoped=api.vortex_payload(self.db,api.public_id('firebase:owner'))
        self.assertEqual(value['totals']['jobs'],3);self.assertEqual(value['totals']['expired'],1)
        self.assertEqual(value['totals']['readyDownloads'],1);self.assertEqual(value['totals']['retainedBytes'],1234)
        self.assertTrue(value['hasMore']);self.assertEqual(len(value['jobs']),1)
        self.assertEqual(scoped['totals']['jobs'],2);self.assertEqual(scoped['totals']['queued'],1)
        self.assertEqual(scoped['jobs'][0]['format'],'mp3')
        for secret in ('SECRET','PRIVATE','firebase:owner','firebase:second'):
            self.assertNotIn(secret,json.dumps(value));self.assertNotIn(secret,json.dumps(scoped))
        with self.assertRaises(ValueError):api.vortex_payload(self.db,limit=2001)
    def test_setup_patch_apply_and_rollback(self):
        raw=b"from pathlib import Path\ndef connect_db(): pass\ndef authorize(): pass\n# g.uid, g.auth_kind = 'firebase:'\n# app.config['AUDIT_LOGS']\nif __name__ == '__main__':\n    pass\n"
        (self.root/'server.py').write_bytes(raw)
        config={'firebaseAuth':{'enabled':True},'dataDir':'data'}
        (self.root/'config.json').write_text(json.dumps(config))
        stage=self.root/'stage';stage.mkdir()
        (stage/'activity_dashboard.py').write_text((ROOT/'vision-pc/activity_dashboard.py').read_text())
        (stage/'account_administration.py').write_text((ROOT/'vision-pc/account_administration.py').read_text())
        with patch('builtins.input',return_value='YES'):
            setup.prepare(self.root,stage,'one@example.test')
        self.assertEqual((self.root/'server.py').read_bytes(),raw)
        setup.apply(stage)
        self.assertIn(setup.HOOK,(self.root/'server.py').read_text())
        self.assertEqual(setup.patch_server((self.root/'server.py').read_bytes()),(self.root/'server.py').read_bytes())
        self.assertTrue(api.allowed(self.config,'firebase:owner','firebase-google'))
        self.assertFalse(api.allowed(self.config,'firebase:second','firebase-google'))
        setup.rollback(stage)
        self.assertEqual((self.root/'server.py').read_bytes(),raw)
        self.assertFalse((self.data/'activity-admins.json').exists())
        self.assertEqual(json.loads((self.root/'config.json').read_text()),config)
    def test_setup_refuses_unknown_or_modified_startup(self):
        with self.assertRaises(ValueError):setup.patch_server(b'print("custom")')
    def test_html_has_no_dynamic_html_insertion(self):
        source=(ROOT/'activity.html').read_text()
        for unsafe in ('.innerHTML','insertAdjacentHTML','document.write','eval('):self.assertNotIn(unsafe,source)
        self.assertIn('[hidden]{display:none!important}',source)
        self.assertIn("credentials:'omit'",source)
        self.assertIn('PREVIEW · Sample data only',source)
        self.assertIn('clearPrivate()',source)

if __name__=='__main__':unittest.main()
