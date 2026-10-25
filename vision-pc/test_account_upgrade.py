"""Paid authorization and global account preferences; no real API calls."""
import json
import unittest
from unittest.mock import patch, Mock
import server
from gemini_access import public_id
from account_upgrade import luna_key
from luna_captions import LunaCaptionError
from test_gemini_server import GeminiServerTests

class AccountUpgradeTests(unittest.TestCase):
    setUpClass = GeminiServerTests.__dict__['setUpClass']
    setUp = GeminiServerTests.setUp
    configure = GeminiServerTests.configure
    headers = GeminiServerTests.headers
    def access(self, headers=None, method='get'):
        return getattr(self.client, method)('/api/account/upgrade', headers=headers or self.alice)
    def decide(self, decision='approved', headers=None):
        return self.client.post('/api/admin/gemini/access', headers=headers or self.owner,
            json={'userId':public_id('firebase:alice'), 'decision':decision})
    def prefs(self, body=None, headers=None):
        args={'headers':headers or self.alice}
        if body is not None:args['json']=body
        return (self.client.put if body is not None else self.client.get)('/api/account/processing-preferences', **args)
    def test_self_approval_denial_and_invalid_preferences(self):
        self.assertEqual(self.client.get('/api/account/upgrade').status_code,401)
        self.assertEqual(self.access().json['status'],'unrequested')
        self.assertEqual(self.access(method='post').json['status'],'pending')
        self.assertEqual(self.decide(headers=self.alice).status_code,403)
        for value in [{'transcriptionProvider':'gemini'},{'frameDescriptionEngine':'auto'}]:self.assertEqual(self.prefs(value).status_code,403)
        self.assertEqual(self.prefs({'approved':True}).status_code,400)
        self.assertEqual(self.decide('denied').status_code,200)
        self.assertEqual(self.access(method='post').json['status'],'denied')
    def test_defaults_overrides_revocation_restart_and_account_isolation(self):
        self.access(method='post');self.decide()
        defaults=self.prefs().json['preferences']
        self.assertEqual(defaults['transcriptionProvider'],'gemini')
        self.assertEqual(defaults['frameDescriptionEngine'],'auto')
        self.assertTrue(defaults['describeFrames'])
        choice={'transcriptionProvider':'local','frameDescriptionEngine':'local','describeFrames':False}
        self.assertEqual(self.prefs(choice).status_code,200)
        self.configure()
        for name,value in choice.items():self.assertEqual(self.prefs().json['preferences'][name],value)
        self.assertTrue(self.prefs(headers=self.bob).json['preferences']['describeFrames'])
        self.prefs({'transcriptionProvider':'gemini','frameDescriptionEngine':'auto'});self.decide('revoked')
        for name in ['transcriptionProvider','frameDescriptionEngine']:self.assertEqual(self.prefs().json['preferences'][name],'local')
        self.assertFalse(self.prefs().json['preferences']['describeFrames'])
        self.assertEqual(self.access(method='post').json['status'],'revoked')
    def saved_key(self, uid='firebase:owner'):
        with server.connect_db() as db:
            columns={r[1] for r in db.execute('PRAGMA table_info(account_credentials)')}
            fields={'uid':uid,'openai_key_cipher':'fixture-cipher'}
            if 'updated_at' in columns:fields['updated_at']=1
            db.execute('INSERT OR REPLACE INTO account_credentials('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(fields.values()))
    def test_luna_requires_approval_and_sponsor_must_remain_admin(self):
        self.saved_key();store=server.frame_analysis
        with patch('sessions.unprotect_secret',return_value='fixture-key'):
            with self.assertRaises(LunaCaptionError):luna_key(store,'project_gemini_alice_01')
            self.access(method='post');self.decide()
            self.assertEqual(luna_key(store,'project_gemini_alice_01'),'fixture-key')
            (server.app.config['DATA_DIR']/'activity-admins.json').write_text(json.dumps({'uids':['firebase:someone-else']}))
            with self.assertRaises(LunaCaptionError):luna_key(store,'project_gemini_alice_01')
            self.saved_key('firebase:alice')
            self.assertEqual(luna_key(store,'project_gemini_alice_01'),'fixture-key')
            server.app.config['GEMINI_ACCESS'].decide(public_id('firebase:alice'),'revoked','firebase:owner')
            with self.assertRaises(LunaCaptionError):luna_key(store,'project_gemini_alice_01')
    def queue_title(self):
        old=self.client.get(self.project,headers=self.alice).json
        body={'title':'Untitled timeline','settings':{'autoNameProject':True},'nodes':[{'kind':'image','title':'Church renovation'}]}
        self.client.put(self.project,headers=self.alice,json={'revision':old['revision'],'project':body})
        return self.client.post(self.project+'/auto-name',headers=self.alice,json={'requestId':'new-name'})
    def test_upgraded_title_uses_luna_without_local_model_and_never_writes_project_title(self):
        self.access(method='post');self.decide();self.saved_key();self.queue_title()
        result={'status':'completed','usage':{'input_tokens':30,'output_tokens':5},'output':[{'type':'message','content':[{'type':'output_text','text':'Church Renovation Plan'}]}]}
        self.transport.side_effect=None;self.transport.return_value=Mock(status_code=200,json=lambda:result)
        with patch('sessions.unprotect_secret',return_value='fixture-key'):self.assertTrue(server.frame_analysis.naming.work_once())
        row=server.frame_analysis.naming.row('project_gemini_alice_01')
        self.assertEqual((row['status'],row['provider'],row['title']),('complete','luna','Church Renovation Plan'))
        self.assertEqual(self.transport.call_args.kwargs['json']['model'],'gpt-6-luna')
        self.assertEqual(self.client.get(self.project,headers=self.alice).json['project']['title'],'Untitled timeline')
    def test_uncertain_paid_title_never_replays_after_restart(self):
        self.queue_title()
        with server.connect_db() as db:db.execute("UPDATE project_naming SET status='processing',provider='luna',paid_inflight=1")
        server.frame_analysis.naming.initialize()
        self.assertEqual(server.frame_analysis.naming.row('project_gemini_alice_01')['status'],'paused')
        self.assertFalse(server.frame_analysis.naming.work_once());self.transport.assert_not_called()

if __name__=='__main__':unittest.main()
