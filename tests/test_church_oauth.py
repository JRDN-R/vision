"""Offline protocol, privilege and native-worker boundary regression tests."""
from contextlib import contextmanager
from pathlib import Path
import hashlib,json,shutil,sqlite3,sys,time
from urllib.parse import parse_qs,urlparse
import pytest
from flask import Flask,g,request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'vision-pc'))
from church_admin import ChurchAdmin,digest
from church_mcp import ChurchMCP
from church_oauth import CALLBACK,MCP_PATH,OAUTH_PATH,PUBLIC_PATHS,challenge

ORIGIN='https://server.example.test'
VERIFIER='v'*64
@pytest.fixture
def native(tmp_path):
    app=Flask(__name__);app.config.update(TESTING=True,DATA_DIR=tmp_path,BACKEND_URL=ORIGIN)
    @contextmanager
    def db():
        conn=sqlite3.connect(tmp_path/'native.sqlite');conn.row_factory=sqlite3.Row
        try:
            with conn:yield conn
        finally:conn.close()
    admin=ChurchAdmin(app,db,lambda s:b'protected:'+s.encode(),lambda b:bytes(b)[10:].decode())
    admin.initialize({'churchAdmins':{'enabled':True,'ownerEmail':'owner@example.test','nodePath':shutil.which('node')}})
    mcp=ChurchMCP(admin);mcp.initialize()
    @app.before_request
    def identity():
        if request.path==MCP_PATH:return mcp.oauth.authenticate()
        if request.path in PUBLIC_PATHS:return
        g.uid=request.headers.get('Uid','firebase:owner');g.auth_kind='firebase-google'
        g.identity_claims={'email':request.headers.get('Email','owner@example.test'),'email_verified':request.headers.get('Verified','yes')=='yes'}
    client=app.test_client();assert client.get('/api/church-admin').status_code==200
    return admin,mcp,client

def register(c,**changes):
    return c.post(OAUTH_PATH+'/register',json={'client_name':'ChatGPT','redirect_uris':[CALLBACK],**changes})
def begin(c,client,**changes):
    return c.get(OAUTH_PATH+'/authorize',query_string={'client_id':client,'redirect_uri':CALLBACK,'response_type':'code','code_challenge_method':'S256','code_challenge':challenge(VERIFIER),'state':'fixture-state-123456789','resource':ORIGIN+MCP_PATH,'scope':'services.read services.write offline_access',**changes})
def authorize(c,headers=None):
    cid=register(c).json['client_id'];r=begin(c,cid);assert r.status_code==302
    ident=parse_qs(urlparse(r.location).query)['connect'][0]
    response=c.post('/api/church-admin/oauth/'+ident,json={'approve':True},headers=headers or {})
    assert response.status_code==200
    values=parse_qs(urlparse(response.json['redirectUrl']).query)
    assert values['iss']==[ORIGIN] and values['state']==['fixture-state-123456789']
    return cid,values['code'][0],ident
def exchange(c,cid,code,**changes):
    return c.post(OAUTH_PATH+'/token',data={'grant_type':'authorization_code','client_id':cid,'code':code,'redirect_uri':CALLBACK,'code_verifier':VERIFIER,'resource':ORIGIN+MCP_PATH,**changes})
def tokens(c,headers=None):
    cid,code,_=authorize(c,headers);r=exchange(c,cid,code);assert r.status_code==200;return cid,r.json
def rpc(c,token,method='tools/list',params=None):
    return c.post(MCP_PATH,headers={'Authorization':'Bearer '+token},json={'jsonrpc':'2.0','id':1,'method':method,'params':params or {}})
def refresh(c,cid,token,**changes):
    return c.post(OAUTH_PATH+'/token',data={'grant_type':'refresh_token','client_id':cid,'resource':ORIGIN+MCP_PATH,'refresh_token':token,**changes})

def test_metadata_and_unauthenticated_challenge(native):
    _,_,c=native;r=c.post(MCP_PATH,json={});assert r.status_code==401 and 'resource_metadata' in r.headers['WWW-Authenticate']
    metadata=c.get('/.well-known/oauth-authorization-server').json
    assert metadata['code_challenge_methods_supported']==['S256'] and metadata['authorization_response_iss_parameter_supported'] is True
    assert c.get('/.well-known/oauth-protected-resource/firstupcjax/mcp').json['resource']==ORIGIN+MCP_PATH

@pytest.mark.parametrize('uris',[[CALLBACK+'?evil=1'],['https://evil.test/callback'],['http://chatgpt.com/connector_platform_oauth_redirect'],[]])
def test_client_redirect_allowlist(native,uris):assert register(native[2],redirect_uris=uris).status_code==400

@pytest.mark.parametrize('change',[{'code_challenge_method':'plain'},{'resource':'https://wrong.test/mcp'},{'state':'short'},{'scope':'admin'},{'redirect_uri':'https://evil.test'}])
def test_authorize_rejects_invalid_request(native,change):
    c=native[2];cid=register(c).json['client_id'];assert begin(c,cid,**change).status_code==400

def test_consent_is_single_use_and_requires_invited_identity(native):
    c=native[2];cid=register(c).json['client_id'];ident=parse_qs(urlparse(begin(c,cid).location).query)['connect'][0]
    assert c.get('/api/church-admin/oauth/'+ident,headers={'Email':'guest@example.test','Uid':'firebase:guest'}).status_code==403
    assert c.post('/api/church-admin/oauth/'+ident,json=[]).status_code==400
    r=c.post('/api/church-admin/oauth/'+ident,json={'approve':False});values=parse_qs(urlparse(r.json['redirectUrl']).query)
    assert values['error']==['access_denied'] and values['iss']==[ORIGIN] and values['state']==['fixture-state-123456789']
    assert c.post('/api/church-admin/oauth/'+ident,json={'approve':True}).status_code==409

@pytest.mark.parametrize('change',[{'code_verifier':'x'*64},{'redirect_uri':CALLBACK+'/'},{'resource':'https://wrong.test/mcp'},{'client_id':'unknown'}])
def test_code_binding(native,change):
    c=native[2];cid,code,_=authorize(c);assert exchange(c,cid,code,**change).status_code==400
    assert exchange(c,cid,code).status_code==200

def test_code_is_one_use_tokens_hashed_and_cache_disabled(native):
    a,_,c=native;cid,code,_=authorize(c);r=exchange(c,cid,code)
    assert r.headers['Cache-Control']=='no-store' and 'refresh_token' in r.json
    assert exchange(c,cid,code).status_code==400
    with a.db() as db:
        raw=str([tuple(row) for row in db.execute('SELECT * FROM church_oauth_tokens')]);assert r.json['access_token'] not in raw and r.json['refresh_token'] not in raw

def test_refresh_rotation_and_replay_revoke_family(native):
    _,_,c=native;cid,t=tokens(c);new=refresh(c,cid,t['refresh_token']);assert new.status_code==200
    assert new.json['refresh_token']!=t['refresh_token']
    assert refresh(c,cid,t['refresh_token']).json['error']=='invalid_grant'
    assert rpc(c,new.json['access_token']).status_code==401

def test_refresh_cannot_expand_scopes_or_change_client(native):
    _,_,c=native;cid,t=tokens(c)
    assert refresh(c,cid,t['refresh_token'],scope='services.read admin').json['error']=='invalid_scope'
    other=register(c).json['client_id'];assert refresh(c,other,t['refresh_token']).json['error']=='invalid_grant'
    assert refresh(c,cid,t['refresh_token']).status_code==200

def test_expired_access_rejected(native):
    a,_,c=native;_,t=tokens(c)
    with a.db() as db:db.execute("UPDATE church_oauth_tokens SET expires=0 WHERE kind='access'")
    assert rpc(c,t['access_token']).status_code==401

def test_unlink_revokes_access_refresh_and_codes(native):
    a,_,c=native;cid,t=tokens(c);_,code,_=authorize(c)
    assert c.delete('/api/church-admin/link').status_code==200
    assert rpc(c,t['access_token']).status_code==401 and refresh(c,cid,t['refresh_token']).status_code==400
    with a.db() as db:assert not db.execute('SELECT * FROM church_oauth_codes').fetchone()

def test_viewer_scope_downgrade_and_no_write(native):
    _,m,c=native;c.post('/api/church-admin/people',json={'email':'viewer@example.test','role':'viewer'})
    _,t=tokens(c,{'Uid':'firebase:viewer','Email':'viewer@example.test'})
    assert 'services.write' not in t['scope']
    r=rpc(c,t['access_token'],'tools/call',{'name':'schedule_person','arguments':{}})
    assert r.json['result']['isError'] and 'credential' not in json.dumps(r.json)

def test_role_revocation_blocks_existing_oauth(native):
    _,_,c=native;c.post('/api/church-admin/people',json={'email':'staff@example.test','role':'scheduler'})
    cid,t=tokens(c,{'Uid':'firebase:staff','Email':'staff@example.test'})
    assert c.delete('/api/church-admin/people',json={'email':'staff@example.test'}).status_code==200
    assert rpc(c,t['access_token']).status_code==401 and refresh(c,cid,t['refresh_token']).status_code==400

def test_native_catalog_initialization_and_logo(native):
    _,m,c=native;m.start();_,t=tokens(c)
    r=rpc(c,t['access_token']);names=[x['name'] for x in r.json['result']['tools']]
    assert len(names)==13 and 'send_scheduling_email' in names and 'link_vision_account' not in names
    assert rpc(c,t['access_token'],'initialize',{'protocolVersion':'2025-11-25'}).json['result']['protocolVersion']=='2025-11-25'
    assert c.get('/firstupcjax/logo.svg').status_code==200

@pytest.mark.parametrize('params',[[],None,'invalid',True])
def test_bad_mcp_params_safe_error(native,params):
    c=native[2];_,t=tokens(c)
    r=c.post(MCP_PATH,headers={'Authorization':'Bearer '+t['access_token']},json={'jsonrpc':'2.0','id':1,'method':'initialize','params':params})
    assert r.status_code==200 and r.json['error']['code']==-32602

def test_credentials_import_preserves_existing_and_removes_plaintext(native):
    a,m,_=native;p=a.directory/'pco-credentials.pending.json';p.write_text(json.dumps({'client_id':'fixture-a','secret':'fixture-b'}));m.start()
    assert not p.exists() and m.credentials()=={'client_id':'fixture-a','secret':'fixture-b'}
    p.write_text(json.dumps({'client_id':'old','secret':'old'}));m.start();assert m.credentials()['client_id']=='fixture-a'

def test_native_send_is_queued_and_poll_is_private(native):
    a,m,c=native;(a.directory/'pco-credentials.pending.json').write_text(json.dumps({'client_id':'fixture','secret':'fixture'}));m.start();_,t=tokens(c)
    args={'assignment_id':'11','person_id':'12','plan_id':'13','service_type_id':'14','request_id':'native-fixture-12345'}
    r=rpc(c,t['access_token'],'tools/call',{'name':'send_scheduling_email','arguments':args}).json['result']['structuredContent']
    assert r['state']=='queued'
    r2=rpc(c,t['access_token'],'tools/call',{'name':'send_scheduling_email','arguments':args}).json['result']['structuredContent'];assert r2['id']==r['id']
    c.post('/api/church-admin/people',json={'email':'viewer@example.test','role':'viewer'})
    _,other=tokens(c,{'Uid':'firebase:viewer','Email':'viewer@example.test'})
    assert rpc(c,other['access_token'],'tools/call',{'name':'notification_job','arguments':{'job_id':r['id']}}).json['result']['isError']
    assert 'fixture' not in json.dumps(r)
