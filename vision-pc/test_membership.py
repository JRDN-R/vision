"""Signed-token membership tests, independent of paid Account Upgrade."""
import time
import unittest
from unittest.mock import Mock, patch
import requests
import server
from membership import Membership
from activity_dashboard import public_id
from test_firebase_auth import FirebaseTests

class MembershipTests(unittest.TestCase):
    setUpClass = FirebaseTests.__dict__['setUpClass']
    setUp = FirebaseTests.setUp
    tearDown = FirebaseTests.tearDown
    def headers(self,uid,**claims):
        return {'Authorization':'Bearer '+self.token(uid,**claims),'Origin':'https://jrdn-r.github.io'}
    token = FirebaseTests.token
    def new(self, uid='new', **claims):
        self.membership_lookup.return_value=False
        return self.headers(uid, **claims)
    def status(self,h):return self.client.get('/api/account/membership',headers=h)
    def owner(self):return self.headers('owner',email='rappsongs@gmail.com',email_verified=True)
    def decide(self, uid='new', decision='approved', headers=None):
        return self.client.post('/api/admin/memberships/decide',headers=headers or self.owner(),json={'userId':public_id('firebase:'+uid),'decision':decision})
    def test_google_and_password_pending_every_workspace(self):
        for provider in ['google.com','password']:
            h=self.new(provider,firebase={'sign_in_provider':provider})
            self.assertEqual(self.status(h).json['status'],'pending')
            for path in ['/api/projects','/api/venture/conversations','/api/vortex/jobs','/api/account/upgrade','/api/admin/memberships','/api/account/openai-key']:
                r=self.client.get(path,headers=h);self.assertEqual(r.status_code,403,(path,r.json));self.assertEqual(r.json['code'],'membership-required')
            self.assertEqual(self.client.put('/api/projects/project_new_member_01',headers=h,json={'revision':0,'project':{}}).status_code,403)
    def test_owner_approval_is_separate_from_upgrade(self):
        h=self.new();self.status(h)
        self.assertEqual(self.decide(headers=h).status_code,403)
        self.assertEqual(self.decide().status_code,200)
        self.assertTrue(self.status(h).json['approved'])
        self.assertEqual(self.client.get('/api/projects',headers=h).status_code,200)
        self.assertFalse(self.client.get('/api/account/upgrade',headers=h).json['approved'])
        self.assertEqual(self.decide().status_code,409)
    def test_denial_survives_restart_and_admin_can_reconsider(self):
        h=self.new();self.status(h);self.decide(decision='denied');server.membership.initialize()
        self.assertEqual(self.status(h).json['status'],'denied')
        self.assertEqual(self.decide().status_code,200);self.assertTrue(self.status(h).json['approved'])
    def test_email_spoof_and_owner_rebinding_rejected(self):
        for uid,changes in [('unverified',{'email_verified':False}),('password',{'email_verified':True,'firebase':{'sign_in_provider':'password'}})]:
            h=self.new(uid,email='rappsongs@gmail.com',**changes)
            self.assertFalse(self.status(h).json['admin']);self.assertEqual(self.client.get('/api/admin/memberships',headers=h).status_code,403)
        self.assertTrue(self.status(self.owner()).json['admin'])
        self.assertFalse(self.status(self.new('other',email='rappsongs@gmail.com',email_verified=True)).json['admin'])
    def test_existing_firebase_accounts_remain_approved(self):
        self.assertTrue(self.status(self.a).json['approved']);self.membership_lookup.return_value=False;server.membership.initialize()
        self.assertTrue(self.status(self.a).json['approved']);self.assertFalse(self.status(self.b).json['approved'])
    def test_audit_snapshot_only_runs_once(self):
        with server.connect_db() as db:
            db.execute("INSERT INTO audit_users(uid,name,email,first_seen,last_seen,auth_time,sign_in_sightings) VALUES('firebase:legacy','Legacy','legacy@example.test',1,1,1,1)")
            db.execute("DELETE FROM membership_settings WHERE key='cutoff'")
        server.membership.initialize();self.membership_lookup.return_value=False
        self.assertTrue(self.status(self.headers('legacy')).json['approved'])
        h=self.new();self.status(h);server.membership.initialize();self.assertFalse(self.status(h).json['approved'])
    def test_guest_entry_and_old_tokens_closed(self):
        self.assertEqual(self.client.post('/api/trial/start',json={'deviceId':'a'*64}).status_code,403)
        self.assertEqual(self.client.get('/api/projects',headers={'Authorization':'Bearer trial_old'}).status_code,403)
    def test_owner_persists_and_can_view_status(self):
        self.assertTrue(self.status(self.owner()).json['admin']);server.membership.initialize()
        self.assertEqual(self.client.get('/api/admin/memberships',headers=self.owner()).status_code,200)
        self.assertEqual(self.client.get('/api/admin/activity',headers=self.owner()).status_code,200)
    def test_age_lookup_checks_uid_and_fails_closed(self):
        method=Membership.created_before_cutoff
        for created,expected in [('1000',True),(str(int((time.time()+60)*1000)),False)]:
            r=Mock();r.json.return_value={'users':[{'localId':'old','createdAt':created}]}
            with patch('membership.requests.post',return_value=r):self.assertEqual(method(server.membership,'firebase:old','token'),expected)
            r.close.assert_called_once()
        for data in [{'users':[{'localId':'attacker','createdAt':'1000'}]},{'users':[{'localId':'old','createdAt':'NaN'}]},{'users':[]}]:
            r=Mock();r.json.return_value=data
            with patch('membership.requests.post',return_value=r):
                with self.assertRaises(server.APIError):method(server.membership,'firebase:old','token')
        with patch('membership.requests.post',side_effect=requests.Timeout()):
            with self.assertRaises(server.APIError):method(server.membership,'firebase:old','token')
    def test_input_and_directory_privacy(self):
        self.status(self.new());self.assertEqual(self.decide(uid='missing').status_code,404);self.assertEqual(self.decide(decision='anything').status_code,400)
        data=self.client.get('/api/admin/memberships',headers=self.owner()).json
        self.assertEqual(data['pendingCount'],1);self.assertNotIn('uid',data['requests'][0]);self.assertEqual(self.client.get('/api/account/membership').status_code,401)

if __name__=='__main__':unittest.main()
