"""Offline boundary and durable-send tests. No provider writes or real email."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import time

import pytest
from flask import Flask, g, request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'vision-pc'))
from church_admin import ChurchAdmin, ChurchError
from church_notifications import NotificationWorker, LoginRequired, BrowserReviewRequired, PlanningCenter, timestamp

KEY='a'*64
CREDENTIALS={'client_id':'fixture-id','secret':'fixture-secret'}
ARGS={'assignment_id':'11','person_id':'12','plan_id':'13','service_type_id':'14','request_id':'fixture-request-0001'}

@pytest.fixture
def system(tmp_path):
    app=Flask(__name__);app.config['TESTING']=True;app.config['DATA_DIR']=tmp_path
    @contextmanager
    def db():
        conn=sqlite3.connect(tmp_path/'test.sqlite');conn.row_factory=sqlite3.Row
        try:
            with conn: yield conn
        finally:conn.close()
    admin=ChurchAdmin(app,db,lambda text:b'encrypted:'+text.encode(),lambda blob:bytes(blob)[10:].decode())
    admin.initialize({'churchAdmins':{'enabled':True,'ownerEmail':'owner@example.test','bridgeSecret':KEY}})
    @app.before_request
    def identity():
        if request.path.endswith('/bridge'):admin.check_bridge();return
        g.auth_kind=request.headers.get('Kind','firebase-google');g.uid=request.headers.get('Uid','firebase:owner')
        g.identity_claims={'email':request.headers.get('Email','owner@example.test'),'email_verified':request.headers.get('Verified','yes')=='yes'}
    client=app.test_client()
    return admin,client

def owner(system):
    admin,client=system
    assert client.get('/api/church-admin').status_code==200
    return admin,client

def signed(body,stamp=None,nonce=None):
    raw=json.dumps(body).encode();stamp=str(int(time.time())) if stamp is None else str(stamp);nonce=nonce or secrets.token_hex(16)
    signature=hmac.new(KEY.encode(),(stamp+'\n'+nonce+'\n').encode()+raw,hashlib.sha256).hexdigest()
    return dict(data=raw,content_type='application/json',headers={'X-FUPCJ-Time':stamp,'X-FUPCJ-Nonce':nonce,'X-FUPCJ-Signature':signature})

def job(admin,args=None):
    result=admin.submit('firebase:owner',args or ARGS,CREDENTIALS)
    with admin.db() as db:return dict(db.execute('SELECT * FROM church_email_jobs WHERE id=?',(result['id'],)).fetchone())

def state(admin,j):
    with admin.db() as db:return dict(db.execute('SELECT * FROM church_email_jobs WHERE id=?',(j['id'],)).fetchone())

def test_owner_bootstrap_exact_verified_email(system):
    admin,client=system
    assert client.get('/api/church-admin',headers={'Email':'other@example.test'}).status_code==403
    assert client.get('/api/church-admin',headers={'Verified':'no'}).status_code==403
    assert client.get('/api/church-admin').json['member']['role']=='owner'

def test_nonfirebase_credentials_cannot_enter(system):
    assert system[1].get('/api/church-admin',headers={'Kind':'installation-owner'}).status_code==401

@pytest.mark.parametrize('role',['viewer','scheduler'])
def test_invite_accepts_verified_email_and_role(system,role):
    admin,client=owner(system)
    assert client.post('/api/church-admin/people',json={'email':'guest@example.test','role':role}).json['emailSent'] is False
    response=client.get('/api/church-admin',headers={'Uid':'firebase:guest','Email':'guest@example.test'})
    assert response.json['member']['role']==role
    if role=='viewer':
        with pytest.raises(ChurchError):admin.submit('firebase:guest',ARGS,CREDENTIALS)

def test_uninvited_user_rejected(system):
    owner(system)
    assert system[1].get('/api/church-admin',headers={'Uid':'firebase:guest','Email':'guest@example.test'}).status_code==403

def test_owner_cannot_be_revoked_or_downgraded(system):
    _,client=owner(system)
    assert client.delete('/api/church-admin/people',json={'email':'owner@example.test'}).status_code==400
    assert client.post('/api/church-admin/people',json={'email':'owner@example.test','role':'viewer'}).status_code==400

def test_pair_code_single_use_hashed(system):
    admin,client=owner(system);code=client.post('/api/church-admin/link-code').json['code']
    with admin.db() as db:assert code not in str([tuple(r) for r in db.execute('SELECT * FROM church_pair_codes')])
    assert admin.pair('site-actor',code)['linked']
    with pytest.raises(ChurchError):admin.pair('other-actor',code)

def test_expired_pair_code_rejected(system):
    admin,client=owner(system);code=client.post('/api/church-admin/link-code').json['code']
    with admin.db() as db:db.execute('UPDATE church_pair_codes SET expires_at=0')
    with pytest.raises(ChurchError):admin.pair('actor',code)

def test_unlink_revokes_actor(system):
    admin,client=owner(system);admin.pair('actor',client.post('/api/church-admin/link-code').json['code'])
    assert admin.linked_member('actor','find_people')['role']=='owner'
    client.delete('/api/church-admin/link')
    with pytest.raises(ChurchError):admin.linked_member('actor','find_people')

def test_revocation_blocks_old_links_and_cancels_queue(system):
    admin,client=owner(system);client.post('/api/church-admin/people',json={'email':'guest@example.test','role':'scheduler'})
    headers={'Uid':'firebase:guest','Email':'guest@example.test'};client.get('/api/church-admin',headers=headers)
    admin.pair('guest',client.post('/api/church-admin/link-code',headers=headers).json['code'])
    j=admin.submit('firebase:guest',ARGS,CREDENTIALS)
    client.delete('/api/church-admin/people',json={'email':'guest@example.test'})
    with pytest.raises(ChurchError):admin.linked_member('guest','schedule_person')
    assert state(admin,j)['state']=='cancelled'
    assert state(admin,j)['credential'] is None

def test_removed_vision_account_blocked(system):
    admin,_=owner(system);admin.account_blocked=lambda uid:True
    with pytest.raises(ChurchError):admin.member('firebase:owner')

def test_bridge_rejects_missing_bad_expired_signatures(system):
    _,client=owner(system);body={'actor':'a','action':'find_people'}
    assert client.post('/api/church-admin/bridge',json=body).status_code==401
    assert client.post('/api/church-admin/bridge',**signed(body,stamp=1)).status_code==401
    kwargs=signed(body);kwargs['headers']['X-FUPCJ-Signature']='0'*64
    assert client.post('/api/church-admin/bridge',**kwargs).status_code==401

def test_bridge_nonce_cannot_replay(system):
    _,client=owner(system);kwargs=signed({'actor':'a','action':'find_people'})
    assert client.post('/api/church-admin/bridge',**kwargs).status_code==403
    assert client.post('/api/church-admin/bridge',**kwargs).status_code==409

def test_valid_bridge_still_requires_membership(system):
    _,client=owner(system)
    assert client.post('/api/church-admin/bridge',**signed({'actor':'unknown','action':'find_people'})).status_code==403

def test_repeated_request_and_assignment_deduplicated(system):
    admin,_=owner(system);first=job(admin);second=job(admin);third=job(admin,{**ARGS,'request_id':'another-request-0002'})
    assert first['id']==second['id']==third['id']

def test_request_id_or_assignment_identity_collision_rejected(system):
    admin,_=owner(system);job(admin)
    for change in ({'person_id':'88'},{'request_id':'another-request-0002','person_id':'88'}):
        with pytest.raises(ChurchError):job(admin,{**ARGS,**change})

def test_public_result_hides_credentials(system):
    admin,_=owner(system);j=job(admin);result=admin.public_job(j)
    assert 'credential' not in result and 'secret' not in json.dumps(result) and 'client_id' not in result
    assert j['credential'].startswith(b'encrypted:')

@pytest.mark.parametrize('old,expected',[('sending','needs_review'),('running','queued')])
def test_restart_does_not_repeat_uncertain_send(system,old,expected):
    admin,_=owner(system);j=job(admin)
    with admin.db() as db:db.execute('UPDATE church_email_jobs SET state=?',(old,))
    admin.initialize({'churchAdmins':admin.settings});assert state(admin,j)['state']==expected

class FakeAPI:
    def __init__(self,creds):self.sent=None;self.closed=False
    def assignment(self,j):return {'attributes':{'notification_sent_at':self.sent,'name':'Fixture Person'}}
    def plan(self,j):return {'data':{'id':j['plan_id']}}
    def close(self):self.closed=True

def worker(system,send=None):
    admin,_=owner(system);api=FakeAPI(CREDENTIALS)
    class Sender:
        def __init__(self,a):pass
        def send(self,j,a,p,s,before):
            if send:return send(api,before,admin)
            before();api.sent=datetime.now(timezone.utc).isoformat()
    admin.directory.joinpath('browser-state.dpapi').write_bytes(admin.protect(json.dumps({'cookies':[],'origins':[]})))
    return admin,api,NotificationWorker(admin,api_factory=lambda c:api,sender_factory=Sender,sleep=lambda _:None)

def test_verified_send_clears_credentials(system):
    admin,api,w=worker(system);j=job(admin);w.process(j);r=state(admin,j)
    assert r['state']=='sent' and r['attempted_at'] and r['sent_at'];assert r['credential'] is None;assert api.closed

def test_existing_timestamp_no_resend(system):
    admin,api,w=worker(system,lambda *a:pytest.fail('No new send allowed'));api.sent='2026-10-14T12:00:00Z';j=job(admin);w.process(j)
    assert state(admin,j)['state']=='already_sent'

def test_login_missing_waits_without_click(system):
    admin,api,w=worker(system,lambda *a:pytest.fail('No browser call allowed'));(admin.directory/'browser-state.dpapi').unlink();j=job(admin);w.process(j)
    assert state(admin,j)['state']=='waiting_for_login' and state(admin,j)['attempted_at'] is None

def test_timeout_after_click_reconciles_without_retry(system):
    count=[]
    def send(api,before,admin):before();count.append(1);api.sent=datetime.now(timezone.utc).isoformat();raise TimeoutError()
    admin,api,w=worker(system,send);j=job(admin);w.process(j)
    assert count==[1] and state(admin,j)['state']=='sent'

def test_uncertain_send_retained_for_review(system):
    admin,api,w=worker(system,lambda api,before,admin:before());j=job(admin);w.process(j)
    assert state(admin,j)['state']=='needs_review'
    assert job(admin,{**ARGS,'request_id':'new-request-0000001'})['id']==j['id']

def test_revoked_before_click_blocks_send(system):
    def send(api,before,admin):
        with admin.db() as db:db.execute('UPDATE church_members SET active=0')
        before();pytest.fail('Revoked user reached click')
    admin,api,w=worker(system,send);j=job(admin);w.process(j);assert state(admin,j)['state']=='failed' and state(admin,j)['attempted_at'] is None

def test_wrong_browser_layout_fails_before_click(system):
    def send(*args):raise BrowserReviewRequired('Recipient was ambiguous.')
    admin,api,w=worker(system,send);j=job(admin);w.process(j);assert state(admin,j)['state']=='failed' and state(admin,j)['attempted_at'] is None

@pytest.mark.parametrize('origin',['https://services.planningcenteronline.com.evil.test','http://services.planningcenteronline.com'])
def test_login_import_rejects_unexpected_origins(system,origin):
    admin,_,w=worker(system);(admin.directory/'browser-state.pending.json').write_text(json.dumps({'cookies':[],'origins':[{'origin':origin}]}))
    with pytest.raises(ChurchError):w.import_login()

def test_login_import_encrypted_and_plaintext_removed(system):
    admin,_,w=worker(system);pending=admin.directory/'browser-state.pending.json';pending.write_text(json.dumps({'cookies':[{'domain':'.planningcenteronline.com'}],'origins':[]}));w.import_login()
    assert not pending.exists();assert (admin.directory/'browser-state.dpapi').read_bytes().startswith(b'encrypted:')

def test_timestamp_requires_timezone():
    assert timestamp('2026-10-15T10:00:00') is None
    assert timestamp('2026-10-15T10:00:00-04:00')==timestamp('2026-10-15T14:00:00Z')

def test_api_assignment_rejects_wrong_identity():
    api=PlanningCenter(CREDENTIALS)
    api.get=lambda path:{'data':{'id':'11','attributes':{'status':'U'},'relationships':{k:{'data':{'id':v}} for k,v in [('person','999'),('plan','13'),('service_type','14')]}}}
    with pytest.raises(ChurchError):api.assignment(ARGS)
    api.close()
