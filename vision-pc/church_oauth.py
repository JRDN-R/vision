"""Narrow OAuth 2.1 authorization for FirstUPCJax MCP, backed by Vision identity.

Opaque access/refresh tokens are hashed at rest, bound to one resource and client,
and checked against current church membership. Provider PATs are never tokens.
"""
from __future__ import annotations
import base64
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlencode

from flask import g, jsonify, redirect, request
from church_admin import ChurchError, digest

SCOPES = {'services.read','services.write','offline_access'}
CALLBACK = 'https://chatgpt.com/connector_platform_oauth_redirect'
MCP_PATH = '/firstupcjax/mcp'
OAUTH_PATH = '/firstupcjax/oauth'
METADATA_PATH = '/.well-known/oauth-protected-resource/firstupcjax/mcp'
PUBLIC_PATHS = {METADATA_PATH,'/.well-known/oauth-protected-resource',
                '/.well-known/oauth-authorization-server',
                OAUTH_PATH+'/register',OAUTH_PATH+'/authorize',OAUTH_PATH+'/token'}


class OAuthError(Exception):
    def __init__(self, code, description, status=400):
        self.code,self.description,self.status=code,description,status


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode().rstrip('=')


class ChurchOAuth:
    def __init__(self, admin):
        self.admin,self.app,self.db=admin,admin.app,admin.db
        admin.oauth=self
        self.register_routes()

    @property
    def issuer(self):
        value=self.app.config.get('BACKEND_URL','')
        if not value.startswith('https://'):
            raise ChurchError('FUPCJ HTTPS address is not configured.',503)
        return value.rstrip('/')

    @property
    def resource(self):return self.issuer+MCP_PATH

    def initialize(self):
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS church_oauth_clients (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, redirects TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS church_oauth_requests (
                id TEXT PRIMARY KEY, client TEXT NOT NULL, redirect TEXT NOT NULL,
                challenge TEXT NOT NULL, state TEXT NOT NULL, resource TEXT NOT NULL,
                scopes TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS church_oauth_codes (
                hash TEXT PRIMARY KEY, uid TEXT NOT NULL, client TEXT NOT NULL, redirect TEXT NOT NULL,
                challenge TEXT NOT NULL, resource TEXT NOT NULL, scopes TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS church_oauth_tokens (
                hash TEXT PRIMARY KEY, kind TEXT NOT NULL, uid TEXT NOT NULL, client TEXT NOT NULL,
                resource TEXT NOT NULL, scopes TEXT NOT NULL, expires REAL NOT NULL,
                family TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS church_oauth_family ON church_oauth_tokens(family);
            CREATE TABLE IF NOT EXISTS church_oauth_rate (bucket TEXT PRIMARY KEY, count INTEGER NOT NULL);
            ''')

    def limit(self,kind,maximum=30):
        # No request headers, tokens, codes, email or provider credentials in rate keys.
        bucket=kind+':'+digest(request.remote_addr or '')+':'+str(int(time.time()//60))
        with self.db() as db:
            db.execute('DELETE FROM church_oauth_rate WHERE CAST(substr(bucket,-8) AS INTEGER)<?',(int(time.time()//60)-2,))
            db.execute('INSERT INTO church_oauth_rate VALUES(?,1) ON CONFLICT(bucket) DO UPDATE SET count=count+1',(bucket,))
            count=db.execute('SELECT count FROM church_oauth_rate WHERE bucket=?',(bucket,)).fetchone()[0]
        if count>maximum:raise OAuthError('temporarily_unavailable','Too many requests. Try again shortly.',429)

    def credentials(self,token):
        if not isinstance(token,str) or not re.fullmatch(r'fua_[A-Za-z0-9_-]{43}',token):
            raise OAuthError('invalid_token','Reconnect FirstUPCJax Admins.',401)
        with self.db() as db:
            row=db.execute("SELECT * FROM church_oauth_tokens WHERE hash=? AND kind='access' AND used=0 AND expires>?",(digest(token),time.time())).fetchone()
        if not row or row['resource']!=self.resource or 'services.read' not in row['scopes'].split():
            raise OAuthError('invalid_token','Reconnect FirstUPCJax Admins.',401)
        self.admin.member(row['uid'])
        return dict(row)

    def authenticate(self):
        self.admin.enabled()
        header=request.headers.get('Authorization','')
        try:
            token=self.credentials(header[7:] if header.startswith('Bearer ') else '')
        except OAuthError:
            response=jsonify(error='Sign in to connect FirstUPCJax Admins.');response.status_code=401
            response.headers['WWW-Authenticate']='Bearer resource_metadata="'+self.issuer+METADATA_PATH+'", scope="services.read services.write offline_access"'
            return response
        g.uid=token['uid'];g.auth_kind='church-oauth';g.church_scopes=set(token['scopes'].split())

    def issue(self,db,uid,client,resource,scopes,family=None):
        family=family or secrets.token_hex(16)
        access='fua_'+secrets.token_urlsafe(32)
        db.execute('INSERT INTO church_oauth_tokens VALUES(?,?,?,?,?,?,?,?,0)',(digest(access),'access',uid,client,resource,scopes,time.time()+900,family))
        result={'access_token':access,'token_type':'Bearer','expires_in':900,'scope':scopes}
        if 'offline_access' in scopes.split():
            refresh='fur_'+secrets.token_urlsafe(32)
            db.execute('INSERT INTO church_oauth_tokens VALUES(?,?,?,?,?,?,?,?,0)',(digest(refresh),'refresh',uid,client,resource,scopes,time.time()+30*86400,family))
            result['refresh_token']=refresh
        return result

    def connections(self,db,uid):
        return db.execute('SELECT COUNT(DISTINCT family) FROM church_oauth_tokens WHERE uid=? AND used=0 AND expires>?',(uid,time.time())).fetchone()[0]

    def revoke_in(self,db,uid):
        db.execute('DELETE FROM church_oauth_tokens WHERE uid=?',(uid,))
        db.execute('DELETE FROM church_oauth_codes WHERE uid=?',(uid,))

    def register_routes(self):
        app=self.app
        @app.errorhandler(OAuthError)
        def oauth_error(exc):
            response=jsonify(error=exc.code,error_description=exc.description);response.status_code=exc.status
            response.headers['Cache-Control']='no-store';return response

        @app.get(METADATA_PATH)
        @app.get('/.well-known/oauth-protected-resource')
        def protected_metadata():
            self.admin.enabled()
            return jsonify(resource=self.resource,authorization_servers=[self.issuer],scopes_supported=sorted(SCOPES),bearer_methods_supported=['header'])

        @app.get('/.well-known/oauth-authorization-server')
        def auth_metadata():
            self.admin.enabled()
            return jsonify(issuer=self.issuer,authorization_response_iss_parameter_supported=True,
                           authorization_endpoint=self.issuer+OAUTH_PATH+'/authorize',
                           token_endpoint=self.issuer+OAUTH_PATH+'/token',registration_endpoint=self.issuer+OAUTH_PATH+'/register',
                           token_endpoint_auth_methods_supported=['none'],response_types_supported=['code'],
                           grant_types_supported=['authorization_code','refresh_token'],code_challenge_methods_supported=['S256'],
                           scopes_supported=sorted(SCOPES))

        @app.post(OAUTH_PATH+'/register')
        def register_client():
            self.admin.enabled();self.limit('registration',10)
            if len(request.get_data(cache=True))>8192:raise OAuthError('invalid_client_metadata','Registration is too large.')
            body=request.get_json(silent=True)
            if not isinstance(body,dict):raise OAuthError('invalid_client_metadata','Expected registration JSON.')
            redirects=body.get('redirect_uris');allowed={CALLBACK,*self.admin.settings.get('oauthRedirectUris',[])}
            if not isinstance(redirects,list) or not 1<=len(redirects)<=3 or any(not isinstance(x,str) or not x.startswith('https://') or x not in allowed for x in redirects):
                raise OAuthError('invalid_redirect_uri','Use the exact approved ChatGPT redirect URI.')
            if body.get('token_endpoint_auth_method','none')!='none' or body.get('response_types',['code'])!=['code']:
                raise OAuthError('invalid_client_metadata','Public clients with authorization code and PKCE are supported.')
            grants=body.get('grant_types',['authorization_code','refresh_token'])
            if not isinstance(grants,list) or 'authorization_code' not in grants or any(x not in ('authorization_code','refresh_token') for x in grants):
                raise OAuthError('invalid_client_metadata','Unsupported grant type.')
            name=body.get('client_name','ChatGPT')
            if not isinstance(name,str) or not 1<=len(name)<=80:raise OAuthError('invalid_client_metadata','Invalid client name.')
            client_id=secrets.token_urlsafe(24)
            with self.db() as db:
                if db.execute('SELECT COUNT(*) FROM church_oauth_clients').fetchone()[0]>=10000:raise OAuthError('temporarily_unavailable','Client registration capacity reached.',503)
                db.execute('INSERT INTO church_oauth_clients VALUES(?,?,?,?)',(client_id,name,json.dumps(redirects),time.time()))
            return jsonify(client_id=client_id,client_id_issued_at=int(time.time()),client_name=name,redirect_uris=redirects,token_endpoint_auth_method='none',grant_types=grants,response_types=['code']),201

        @app.get(OAUTH_PATH+'/authorize')
        def authorize_client():
            self.admin.enabled();self.limit('authorization')
            a=request.args
            with self.db() as db:client=db.execute('SELECT * FROM church_oauth_clients WHERE id=?',(a.get('client_id',''),)).fetchone()
            if not client or a.get('redirect_uri') not in json.loads(client['redirects']):raise OAuthError('invalid_request','Unknown client or redirect URI.')
            # Invalid requests are displayed locally, never redirected to unverified URLs.
            if a.get('response_type')!='code' or a.get('code_challenge_method')!='S256' or not re.fullmatch(r'[A-Za-z0-9_-]{43}',a.get('code_challenge','')):
                raise OAuthError('invalid_request','Authorization code with S256 PKCE is required.')
            if a.get('resource')!=self.resource:raise OAuthError('invalid_target','The requested resource does not match this MCP server.')
            scopes=set(a.get('scope','services.read services.write offline_access').split())
            if 'services.read' not in scopes or scopes-SCOPES:raise OAuthError('invalid_scope','Unsupported scope.')
            state=a.get('state','')
            if not 16<=len(state)<=2048:raise OAuthError('invalid_request','A valid OAuth state is required.')
            ident=secrets.token_urlsafe(32)
            with self.db() as db:
                db.execute('DELETE FROM church_oauth_requests WHERE expires<?',(time.time(),))
                db.execute('INSERT INTO church_oauth_requests VALUES(?,?,?,?,?,?,?,?)',(ident,client['id'],a['redirect_uri'],a['code_challenge'],state,self.resource,' '.join(sorted(scopes)),time.time()+600))
            return redirect('https://jrdn-r.github.io/vision/admins/?'+urlencode({'connect':ident}),302)

        @app.get('/api/church-admin/oauth/<ident>')
        def request_details(ident):
            member=self.admin.dashboard_identity()
            with self.db() as db:row=db.execute('SELECT r.*,c.name FROM church_oauth_requests r JOIN church_oauth_clients c ON c.id=r.client WHERE r.id=? AND r.expires>?',(ident,time.time())).fetchone()
            if not row:raise ChurchError('Connection request expired. Start Connect again from ChatGPT.',404)
            scopes=set(row['scopes'].split())
            if member['role']=='viewer':scopes.discard('services.write')
            return jsonify(clientName=row['name'],scopes=sorted(scopes),role=member['role'])

        @app.post('/api/church-admin/oauth/<ident>')
        def approve(ident):
            member=self.admin.dashboard_identity();body=request.get_json(silent=True)
            if not isinstance(body,dict):raise ChurchError('Expected connection decision JSON.')
            if type(body.get('approve')) is not bool:raise ChurchError('Approve or decline this connection.')
            with self.admin.lock,self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT * FROM church_oauth_requests WHERE id=? AND expires>?',(ident,time.time())).fetchone()
                if not row:raise ChurchError('Connection request expired or already used.',409)
                db.execute('DELETE FROM church_oauth_requests WHERE id=?',(ident,))
                parameters={'iss':self.issuer,'state':row['state']}
                if body['approve']:
                    scopes=set(row['scopes'].split())
                    if member['role']=='viewer':scopes.discard('services.write')
                    code=secrets.token_urlsafe(32)
                    db.execute('INSERT INTO church_oauth_codes VALUES(?,?,?,?,?,?,?,?)',(digest(code),member['uid'],row['client'],row['redirect'],row['challenge'],row['resource'],' '.join(sorted(scopes)),time.time()+120))
                    parameters['code']=code;self.admin.audit(db,member['uid'],'oauth-approved',row['client'])
                else:parameters['error']='access_denied'
            return jsonify(redirectUrl=row['redirect']+('&' if '?' in row['redirect'] else '?')+urlencode(parameters))

        @app.post(OAUTH_PATH+'/token')
        def token():
            self.admin.enabled();self.limit('token',60)
            a=request.form
            if a.get('resource')!=self.resource:raise OAuthError('invalid_target','The resource does not match this MCP server.')
            client=a.get('client_id','')
            with self.db() as db:
                if not db.execute('SELECT 1 FROM church_oauth_clients WHERE id=?',(client,)).fetchone():raise OAuthError('invalid_client','Register this connection again.')
            result=None;reused=False
            with self.admin.lock,self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM church_oauth_tokens WHERE expires<?',(time.time(),))
                if a.get('grant_type')=='authorization_code':
                    row=db.execute('SELECT * FROM church_oauth_codes WHERE hash=? AND expires>?',(digest(a.get('code','')),time.time())).fetchone()
                    verifier=a.get('code_verifier','')
                    if not row or row['client']!=client or row['redirect']!=a.get('redirect_uri') or row['resource']!=self.resource or not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}',verifier) or not secrets.compare_digest(challenge(verifier),row['challenge']):
                        raise OAuthError('invalid_grant','Authorization code or PKCE did not verify.')
                    try:self.admin.member(row['uid'])
                    except ChurchError:raise OAuthError('invalid_grant','This Vision account no longer has access.') from None
                    db.execute('DELETE FROM church_oauth_codes WHERE hash=?',(row['hash'],))
                    result=self.issue(db,row['uid'],client,row['resource'],row['scopes'])
                elif a.get('grant_type')=='refresh_token':
                    row=db.execute("SELECT * FROM church_oauth_tokens WHERE hash=? AND kind='refresh' AND expires>?",(digest(a.get('refresh_token','')),time.time())).fetchone()
                    if not row or row['client']!=client or row['resource']!=self.resource:raise OAuthError('invalid_grant','Reconnect FirstUPCJax Admins.')
                    if row['used']:
                        db.execute('DELETE FROM church_oauth_tokens WHERE family=?',(row['family'],));reused=True
                    else:
                        try:self.admin.member(row['uid'])
                        except ChurchError:raise OAuthError('invalid_grant','This Vision account no longer has access.') from None
                        if a.get('scope') and not set(a['scope'].split())<=set(row['scopes'].split()):raise OAuthError('invalid_scope','A refresh cannot expand access.')
                        scopes=a.get('scope') or row['scopes']
                        db.execute('UPDATE church_oauth_tokens SET used=1 WHERE hash=?',(row['hash'],))
                        result=self.issue(db,row['uid'],client,row['resource'],scopes,row['family'])
                else:raise OAuthError('unsupported_grant_type','Use authorization_code or refresh_token.')
            if reused:raise OAuthError('invalid_grant','Refresh token was already used. Reconnect this account.')
            response=jsonify(result);response.headers['Cache-Control']='no-store';response.headers['Pragma']='no-cache';return response
