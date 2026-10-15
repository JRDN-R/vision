"""Authenticated, stateless Streamable HTTP MCP endpoint on the FUPCJ PC."""
import json
import os
from pathlib import Path
import subprocess

from flask import g, jsonify, request, send_file
from church_admin import ChurchError, READ_TOOLS, WRITE_TOOLS
from church_oauth import ChurchOAuth, MCP_PATH


class ChurchMCP:
    def __init__(self,admin):
        self.admin,self.app=admin,admin.app
        self.oauth=ChurchOAuth(admin)
        self.catalog=[]
        self.register_routes()

    def initialize(self):
        self.oauth.initialize()

    def start(self):
        if self.admin.settings.get('enabled') is not True:return
        pending=self.admin.directory/'pco-credentials.pending.json'
        target=self.admin.directory/'pco-credentials.dpapi'
        if pending.is_file() and not pending.is_symlink():
            value=json.loads(pending.read_text(encoding='utf-8-sig'))
            self.validate_credentials(value)
            if target.exists():
                # Existing valid credentials take precedence; never overwrite on an upgrade.
                self.credentials()
            else:
                stage=target.with_suffix('.tmp');stage.write_bytes(self.admin.protect(json.dumps(value)));os.replace(stage,target)
            pending.unlink()
        self.catalog=self.engine_tools()

    @staticmethod
    def validate_credentials(value):
        if not isinstance(value,dict) or any(not isinstance(value.get(k),str) or not 1<=len(value[k])<=1024 for k in ('client_id','secret')):
            raise ChurchError('Planning Center authorization needs PC setup.',503)
        return value

    def credentials(self):
        try:return self.validate_credentials(json.loads(self.admin.unprotect((self.admin.directory/'pco-credentials.dpapi').read_bytes())))
        except ChurchError:raise
        except Exception:raise ChurchError('Planning Center authorization needs PC setup.',503) from None

    def node(self):
        value=self.admin.settings.get('nodePath')
        if not value or not Path(value).is_file():raise ChurchError('The private scheduling runtime needs PC setup.',503)
        return value

    def invoke(self,arguments,input=None,timeout=120):
        try:
            result=subprocess.run([self.node(),str(Path(__file__).with_name('church_runner.mjs')),*arguments],
                input=input,text=True,encoding='utf-8',capture_output=True,timeout=timeout,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:raise ChurchError('The scheduling engine could not finish. Check the assignment before retrying.',502)
            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:raise ChurchError('Scheduling timed out. Its result may be saved; inspect the exact assignment before retrying.',504) from None
        except ChurchError:raise
        except Exception:raise ChurchError('The scheduling engine could not start. Run PC setup again.',503) from None

    def engine_tools(self):return self.invoke(['--tools'],timeout=20)

    def run(self,name,args):
        if not isinstance(name,str) or name not in READ_TOOLS|WRITE_TOOLS:raise ChurchError('Unknown church operation.')
        self.admin.member(g.uid,write=name in WRITE_TOOLS)
        if name in WRITE_TOOLS and 'services.write' not in g.church_scopes:raise ChurchError('Reconnect with scheduling permission before making changes.',403)
        if not isinstance(args,dict):raise ChurchError('Arguments must be an object.')
        if name=='send_scheduling_email':return self.admin.submit(g.uid,args,self.credentials())
        if name=='notification_job':
            if set(args)!={'job_id'} or not isinstance(args['job_id'],str):raise ChurchError('A notification job ID is required.')
            with self.admin.db() as db:row=db.execute('SELECT * FROM church_email_jobs WHERE id=? AND uid=?',(args['job_id'],g.uid)).fetchone()
            if not row:raise ChurchError('Notification request not found.',404)
            return self.admin.public_job(row)
        result=self.invoke([],json.dumps({'name':name,'arguments':args,'uid':g.uid,'credentials':self.credentials(),'journal':str(self.admin.directory/'scheduling.sqlite3')}))
        if not result.get('ok'):raise ChurchError(result.get('error','Scheduling could not finish.'))
        value=result['result']
        if name=='connection_status':value.update(version='1.0.0',send_notifications=True,notification_transport='FUPCJ browser worker',browser_session_saved=(self.admin.directory/'browser-state.dpapi').is_file(),access_control='Vision invite list')
        return value

    def register_routes(self):
        @self.app.get('/firstupcjax/logo.svg')
        def church_logo():return send_file(Path(__file__).with_name('church-logo.svg'),mimetype='image/svg+xml',max_age=86400)

        @self.app.route(MCP_PATH,methods=['GET','POST','DELETE'])
        def mcp():
            # server.before_request has already verified the OAuth token and membership.
            if getattr(g,'auth_kind','')!='church-oauth':raise ChurchError('Connect FirstUPCJax Admins first.',401)
            if request.method!='POST':return ('',204 if request.method=='DELETE' else 405)
            if len(request.get_data(cache=True))>16384:return ('Request too large',413)
            message=request.get_json(silent=True)
            if not isinstance(message,dict) or message.get('jsonrpc')!='2.0' or not isinstance(message.get('method'),str):return jsonify(jsonrpc='2.0',id=None,error={'code':-32600,'message':'Invalid request'}),400
            ident=message.get('id');method=message['method']
            params=message.get('params',{})
            if not isinstance(params,dict):return jsonify(jsonrpc='2.0',id=ident,error={'code':-32602,'message':'Parameters must be an object'})
            if isinstance(ident,(dict,list,bool)):return jsonify(jsonrpc='2.0',id=None,error={'code':-32600,'message':'Invalid request ID'}),400
            if method.startswith('notifications/'):return ('',202)
            if method=='initialize':
                requested=params.get('protocolVersion')
                result={'protocolVersion':requested if requested in ('2025-11-25','2025-06-18','2025-03-26','2024-11-05') else '2025-03-26','capabilities':{'tools':{}},'serverInfo':{'name':'FirstUPCJax Admins','version':'1.0.0'},'instructions':'Use America/New_York. Resolve the exact person and service before changes. Official Scheduling Emails require an explicit request; queued is not sent. Poll notification_job and never automatically resend an uncertain attempt.'}
            elif method=='ping':result={}
            elif method=='tools/list':result={'tools':self.catalog or self.engine_tools()}
            elif method=='tools/call':
                try:
                    value=self.run(params.get('name'),params.get('arguments',{}))
                    result={'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],'structuredContent':value}
                except ChurchError as error:result={'isError':True,'content':[{'type':'text','text':str(error)}]}
                except Exception:
                    self.app.logger.error('Church MCP operation failed; review the exact assignment before retrying.')
                    result={'isError':True,'content':[{'type':'text','text':'Operation could not finish. Check the exact assignment before retrying.'}]}
            else:return jsonify(jsonrpc='2.0',id=ident,error={'code':-32601,'message':'Method not found'})
            return jsonify(jsonrpc='2.0',id=ident,result=result)
