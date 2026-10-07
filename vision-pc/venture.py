"""Vision Venture: account-owned conversations around the existing durable worker.

A conversation uses an existing project identity for authorization and paid-run
recovery. It never replaces or mutates the user's open board. SQLite is the
transactional source of truth; readable files are a recoverable on-disk mirror.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import time

import requests

from flask import g, jsonify, request, send_file
from venture_billing import Funding, key_id

DEFAULT_SETTINGS = dict(model='gpt-6-astra', maxOutputTokens=16000, memoryEnabled=False,
                        runOptions=dict(mode='auto',effort='auto',verbosity='auto',webSearch=False,codeInterpreter=True))
CONVERSATION_ID = re.compile(r'^[A-Za-z0-9_-]{16,120}$')
MIB = 1024*1024
UPLOAD_LIMIT = 128*MIB


def disk_name(value: str) -> str:
    value = str(value).replace('\\','/').rsplit('/',1)[-1]
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', value).rstrip(' .')[:150]
    if not value or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?',value,re.I):
        value = 'file-' + (value or 'upload')
    return value


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+secrets.token_hex(6)+'.tmp')
    try:
        with temp.open('w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def local_title(prompt: str, reply: str) -> str:
    """Small extractive statistical title model. Local, deterministic, no API calls.

    Scores contiguous prompt phrases against repeated meaningful words in the
    first exchange. This intentionally cannot let a source document instruct a
    remote title agent, and never downloads model weights in an update.
    """
    prompt=re.sub(r'https?://\S+|```[\s\S]*?```',' ',prompt)
    prompt=re.sub(r'^(?:please\s+|can you\s+|could you\s+|i (?:want|need) (?:you )?to\s+)+','',prompt.strip(),flags=re.I)
    words=re.findall(r"[\w][\w'’./-]*",prompt,flags=re.U)[:150]
    if not words:
        words=re.findall(r"[\w][\w'’./-]*",re.sub(r'https?://\S+|```[\s\S]*?```',' ',reply[:6000]),flags=re.U)[:150]
    if not words:return 'New conversation'
    stop=set('a an the to of on in is it its this that for with and or but you your me my i we our can could would will please make help want need tell do does how what why when where about some something'.split())
    corpus=Counter(w.lower() for w in re.findall(r'\w+',prompt+' '+reply[:6000]) if len(w)>2 and w.lower() not in stop)
    best=(float('-inf'),0,4)
    for start in range(min(len(words),60)):
        for length in range(1,5):
            phrase=words[start:start+length]
            if len(phrase)<min(3,len(words)):continue
            useful=[w for w in phrase if w.lower() not in stop]
            score=sum(min(3,corpus[w.lower()]) for w in useful)-.38*length-.025*start
            if phrase[0].lower() in stop:score-=2
            if phrase[-1].lower() in stop:score-=2
            if score>best[0]:best=(score,start,length)
    title=' '.join(words[best[1]:best[1]+min(4,best[2])])[:72].strip(' .-/')
    return title[:1].upper()+title[1:] if title else 'Untitled venture'


def local_graphic(prompt: str, reply: str) -> str:
    """Small local topic classifier. Returns a fixed icon key, never HTML or URLs."""
    topics={
        'plane':'aircraft airplane aviation cowling fuselage wing rivet spar flight',
        'code':'code python javascript html css github api function software programming',
        'music':'music song songs singer singing drum drums piano guitar lyrics',
        'image':'image images photo photos picture drawing design illustration logo',
        'repair':'repair fix screw screws sealant plumbing handler maintenance',
        'science':'science physics chemistry experiment biology molecule',
        'travel':'travel trip hotel vacation itinerary destination',
        'food':'food recipe dinner lunch breakfast cooking restaurant',
        'document':'document report spreadsheet presentation slides letter email writing',
    }
    primary=Counter(re.findall(r'\w+',prompt.lower()[:6000]))
    secondary=Counter(re.findall(r'\w+',reply.lower()[:6000]))
    scores={icon:sum(3*primary[w]+secondary[w] for w in keywords.split()) for icon,keywords in topics.items()}
    winner=max(scores,key=scores.get)
    return winner if scores[winner] else 'conversation'


class Venture:
    def __init__(self,sessions):
        self.sessions,self.app,self.db,self.Error=sessions,sessions.app,sessions.db,sessions.Error
        self.funding=Funding(sessions)
        self.mirror_lock=threading.RLock()
        self.last_mirror={}
        self.model_cache={}
        self.documents=None
        self.transcription=None
        from venture_dictation import Dictation
        from venture_profile import Profile
        from venture_memory import Memory
        self.dictation=Dictation(self)
        self.profile=Profile(self)
        self.memory=Memory(self)
        self.register_routes()

    def initialize(self):
        self.last_mirror.clear()
        self.model_cache.clear()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS venture_conversations (
                    id TEXT PRIMARY KEY,uid TEXT NOT NULL,title TEXT NOT NULL,
                    manual_title INTEGER NOT NULL DEFAULT 0,native INTEGER NOT NULL DEFAULT 1,
                    settings_json TEXT NOT NULL DEFAULT '{}',revision INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,updated_at REAL NOT NULL,storage_warning TEXT);
                CREATE INDEX IF NOT EXISTS venture_conversations_owner ON venture_conversations(uid,updated_at DESC,id);
                CREATE TABLE IF NOT EXISTS venture_preferences (
                    uid TEXT PRIMARY KEY,settings_json TEXT NOT NULL,updated_at REAL NOT NULL);
            ''')
            columns={r[1] for r in db.execute('PRAGMA table_info(venture_conversations)')}
            if 'deleted_at' not in columns:
                db.execute('ALTER TABLE venture_conversations ADD COLUMN deleted_at REAL')
            if 'purge_pending' not in columns:
                db.execute('ALTER TABLE venture_conversations ADD COLUMN purge_pending INTEGER NOT NULL DEFAULT 0')
            if 'graphic' not in columns:
                db.execute("ALTER TABLE venture_conversations ADD COLUMN graphic TEXT NOT NULL DEFAULT 'conversation'")
            if 'metadata_version' not in columns:
                db.execute('ALTER TABLE venture_conversations ADD COLUMN metadata_version INTEGER NOT NULL DEFAULT 0')
            columns={r[1] for r in db.execute('PRAGMA table_info(project_runs)')}
            if 'venture_memory_enabled' not in columns:
                db.execute('ALTER TABLE project_runs ADD COLUMN venture_memory_enabled INTEGER NOT NULL DEFAULT 0')
            if 'venture_memory_sources' not in columns:
                db.execute('ALTER TABLE project_runs ADD COLUMN venture_memory_sources TEXT')
            if 'venture_retry_of' not in columns:
                db.execute('ALTER TABLE project_runs ADD COLUMN venture_retry_of TEXT')
            if 'venture_preparation_json' not in columns:
                db.execute('ALTER TABLE project_runs ADD COLUMN venture_preparation_json TEXT')
            # Non-destructive discovery of older account-owned conversations.
            db.execute('''INSERT OR IGNORE INTO venture_conversations(id,uid,title,native,created_at,updated_at)
                SELECT p.id,p.owner_uid,p.title,0,p.created_at,
                COALESCE((SELECT MAX(r.updated_at) FROM project_runs r WHERE r.project_id=p.id),p.updated_at)
                FROM projects p WHERE p.owner_uid IS NOT NULL AND EXISTS(SELECT 1 FROM project_runs r WHERE r.project_id=p.id)''')
            # Give existing answered chats the same short titles and graphics,
            # preserving names the user assigned and legacy board project names.
            for conversation in db.execute('SELECT id,native,manual_title FROM venture_conversations WHERE deleted_at IS NULL AND metadata_version=0').fetchall():
                first=db.execute("SELECT message,project_prompt,text FROM project_runs WHERE project_id=? AND length(trim(text))>0 ORDER BY created_at,id LIMIT 1",(conversation['id'],)).fetchone()
                if first:
                    prompt=first['message'] or first['project_prompt']
                    db.execute('UPDATE venture_conversations SET title=CASE WHEN native=1 AND manual_title=0 THEN ? ELSE title END,graphic=?,metadata_version=1 WHERE id=?',
                               (local_title(prompt,first['text']),local_graphic(prompt,first['text']),conversation['id']))
        self.funding.initialize()
        self.memory.initialize()
        self.dictation.initialize()
        with self.db() as db:
            pending=db.execute('SELECT * FROM venture_conversations WHERE deleted_at IS NOT NULL AND purge_pending=1').fetchall()
        for row in pending:
            try:self.purge_deleted(dict(row))
            except OSError:self.app.logger.warning('A deleted conversation needs another disk cleanup attempt.')

    def account(self) -> str:
        if getattr(g,'auth_kind','')!='firebase-google':
            raise self.Error('Sign in to your Vision account to use Venture.',403)
        return g.uid

    def user_root(self,uid: str) -> Path:
        # Firebase UIDs may contain path separators; no raw UID becomes a path.
        return self.app.config['DATA_DIR']/'users'/hashlib.sha256(uid.encode()).hexdigest()

    def identity(self,uid: str):
        claims=getattr(g,'identity_claims',{})
        atomic_json(self.user_root(uid)/'user.json',dict(uid=uid,name=str(claims.get('name',''))[:200],
                    email=str(claims.get('email',''))[:254],note='Private to this Vision account in the app. The FUPCJ machine administrator can recover these files.'))

    def lookup(self,cid: str,uid: str | None=None,db=None):
        if not CONVERSATION_ID.fullmatch(str(cid)):
            raise self.Error('This conversation is unavailable.',404)
        if db is None:
            with self.db() as connection:return self.lookup(cid,uid,connection)
        row=db.execute('SELECT * FROM venture_conversations WHERE id=?',(cid,)).fetchone()
        if row is None or row['deleted_at'] is not None or (uid is not None and row['uid']!=uid):
            raise self.Error('This conversation is unavailable.',404)
        return dict(row)

    def ensure(self,cid: str):
        """Register an existing account project on its first new run, not legacy trials."""
        with self.db() as db:
            self.assert_not_deleted(cid,db)
            row=db.execute('SELECT owner_uid,title,created_at FROM projects WHERE id=?',(cid,)).fetchone()
            if not row or not row['owner_uid']:return None
            db.execute('INSERT OR IGNORE INTO venture_conversations(id,uid,title,native,created_at,updated_at) VALUES(?,?,?,0,?,?)',
                       (cid,row['owner_uid'],row['title'],row['created_at'],time.time()))
        return self.lookup(cid)

    def root(self,cid: str) -> Path:
        row=self.lookup(cid)
        return self.user_root(row['uid'])/'conversations'/cid

    def run_directory(self,cid: str,rid: str) -> Path:
        row=self.ensure(cid)
        if not row:
            return self.app.config['DATA_DIR']/'projects'/cid/rid
        return self.root(cid)/'uploads'/rid

    def artifact_path(self,row: dict,aid: str,name: str) -> Path:
        if not self.ensure(row['project_id']):
            return self.app.config['DATA_DIR']/'projects'/row['project_id']/row['id']/(aid+'.artifact')
        return self.root(row['project_id'])/'generated'/row['id']/(aid+'-'+disk_name(name))

    def metadata(self,row: dict) -> dict:
        return dict(id=row['id'],title=row['title'],graphic=row.get('graphic','conversation'),manualTitle=bool(row['manual_title']),
                    boardProject=not bool(row['native']),settings=json.loads(row['settings_json']),
                    revision=row['revision'],createdAt=row['created_at'],updatedAt=row['updated_at'],
                    storageWarning=row.get('storage_warning'))

    def settings(self,value) -> dict:
        from sessions import normalize_run_options
        if not isinstance(value,dict) or set(value)-{'model','maxOutputTokens','runOptions','memoryEnabled'}:
            raise self.Error('Invalid Venture settings.')
        model=value.get('model',DEFAULT_SETTINGS['model'])
        limit=value.get('maxOutputTokens',16000)
        if not isinstance(model,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',model):
            raise self.Error('Enter a valid API model ID.')
        if isinstance(limit,bool) or not isinstance(limit,int) or not 512<=limit<=64000:
            raise self.Error('Choose an output limit from 512 to 64000 tokens.')
        try:options=normalize_run_options(value.get('runOptions'))
        except ValueError as error:raise self.Error(str(error))
        memory=value.get('memoryEnabled',False)
        if not isinstance(memory,bool):raise self.Error('Past-conversation memory must be on or off.')
        return dict(model=model,maxOutputTokens=limit,runOptions=options,memoryEnabled=memory)

    def current_key_id(self,uid: str):
        from sessions import unprotect_secret
        with self.db() as db:
            row=db.execute('SELECT openai_key_cipher FROM account_credentials WHERE uid=?',(uid,)).fetchone()
        if not row:return None
        try:return key_id(unprotect_secret(row[0]))
        except Exception:raise self.Error('The saved API connection cannot be unlocked. Save your key again.',503)

    def after_update(self,rid: str):
        """Persist metadata outside the paid run transaction. Never replay a paid request."""
        from sessions import TERMINAL
        row=self.sessions.row(rid)
        conversation=self.ensure(row['project_id'])
        if not conversation:return
        terminal=row['status'] in TERMINAL
        if terminal:
            self.memory.index_run(row)
            try:self.funding.account_for(row)
            except Exception:self.app.logger.warning('Venture usage will be reconciled on the next meter refresh.')
        now=time.monotonic()
        first_reply=bool(row['text'].strip()) and not conversation.get('metadata_version')
        if not terminal and not first_reply and now-self.last_mirror.get(rid,0)<1:return
        self.last_mirror[rid]=now
        # Bounded bookkeeping memory even on an always-on PC.
        if len(self.last_mirror)>500:self.last_mirror={rid:now}
        try:
            with self.mirror_lock:
                with self.db() as db:
                    db.execute('UPDATE venture_conversations SET updated_at=? WHERE id=?',(row['updated_at'],row['project_id']))
                    artifacts=db.execute('SELECT name FROM run_artifacts WHERE run_id=? AND path IS NOT NULL',(rid,)).fetchall() if not row['text'].strip() else []
                    if row['text'].strip() or artifacts:
                        first=db.execute("SELECT id FROM project_runs r WHERE project_id=? AND (length(trim(text))>0 OR EXISTS(SELECT 1 FROM run_artifacts a WHERE a.run_id=r.id AND a.path IS NOT NULL)) ORDER BY created_at,id LIMIT 1",(row['project_id'],)).fetchone()
                        if first and first[0]==rid:
                            prompt=row['message'] or row['project_prompt']
                            reply=row['text'] or 'Document '+ ' '.join(a['name'] for a in artifacts)
                            db.execute('UPDATE venture_conversations SET title=CASE WHEN manual_title=0 THEN ? ELSE title END,graphic=?,metadata_version=1 WHERE id=?',
                                       (local_title(prompt,reply),local_graphic(prompt,reply),row['project_id']))
                self.mirror(row)
        except OSError:
            with self.db() as db:
                db.execute('UPDATE venture_conversations SET storage_warning=? WHERE id=?',
                           ('The readable recovery copy could not be updated. Database messages remain saved; check free disk space.',row['project_id']))

    def recovery_file(self,source: Path,target: Path):
        if source.resolve()==target.resolve() or target.is_file():return
        target.parent.mkdir(parents=True,exist_ok=True)
        temp=target.with_name(target.name+'.'+secrets.token_hex(5)+'.part')
        try:
            try:os.link(source,temp)
            except OSError:shutil.copyfile(source,temp)
            os.replace(temp,target)
        finally:temp.unlink(missing_ok=True)

    def mirror(self,row: dict):
        root=self.root(row['project_id'])
        snapshot=self.sessions.snapshot(row)
        for index,item in enumerate(json.loads(row['inputs_json'])):
            target=root/'uploads'/row['id']/(f'{index+1:02d}-'+disk_name(item['name']))
            source=Path(item['path'])
            if source.is_file():
                self.recovery_file(source,target)
                snapshot['attachments'][index]['recoveryPath']=target.relative_to(root).as_posix()
        with self.db() as db:
            files=db.execute('SELECT * FROM run_artifacts WHERE run_id=?',(row['id'],)).fetchall()
        for item in files:
            if item['path'] and Path(item['path']).is_file():
                target=root/'generated'/row['id']/(item['id']+'-'+disk_name(item['name']))
                self.recovery_file(Path(item['path']),target)
                for artifact in snapshot['artifacts']:
                    if artifact['id']==item['id']:artifact['recoveryPath']=target.relative_to(root).as_posix()
        atomic_json(root/'messages'/(row['id']+'.json'),snapshot)
        self.mirror_metadata(row['project_id'])
        with self.db() as db:
            db.execute('UPDATE venture_conversations SET storage_warning=NULL WHERE id=?',(row['project_id'],))

    def mirror_metadata(self,cid: str):
        root=self.root(cid)
        with self.db() as db:
            runs=db.execute('SELECT id FROM project_runs WHERE project_id=? ORDER BY created_at,id',(cid,)).fetchall()
        atomic_json(root/'conversation.json',{**self.metadata(self.lookup(cid)),
                    'schema':'vision-venture-conversation-v1','messages':['messages/'+r['id']+'.json' for r in runs],
                    'recovery':'Uploads and generated files are ordinary files. SQLite in the parent data directory is authoritative. Back up the whole data directory with the server stopped.'})

    def assert_not_deleted(self,cid,db=None):
        if db is None:
            with self.db() as connection:return self.assert_not_deleted(cid,connection)
        row=db.execute('SELECT deleted_at FROM venture_conversations WHERE id=?',(cid,)).fetchone()
        if row and row['deleted_at'] is not None:raise self.Error('This conversation is unavailable.',404)

    def purge_deleted(self,row):
        # Only fixed, validated conversation roots are removed; no browser paths.
        if not CONVERSATION_ID.fullmatch(row['id']):raise ValueError('Invalid conversation ID')
        roots=[self.user_root(row['uid'])/'conversations'/row['id'],self.app.config['DATA_DIR']/'projects'/row['id']]
        for path in roots:
            if path.is_symlink():path.unlink()
            elif path.exists():shutil.rmtree(path)
        with self.db() as db:
            db.execute('UPDATE venture_conversations SET purge_pending=0 WHERE id=?',(row['id'],))

    def delete(self,cid,uid):
        request.max_content_length=8192
        with self.mirror_lock:
            # Reconcile completed usage before deleting its source records. A
            # deleted conversation never refunds tokens or loses a pending debit.
            from sessions import TERMINAL
            with self.db() as db:
                owned=db.execute('SELECT * FROM venture_conversations WHERE id=? AND uid=?',(cid,uid)).fetchone()
                if owned is None:raise self.Error('This conversation is unavailable.',404)
                runs=db.execute('SELECT * FROM project_runs WHERE project_id=?',(cid,)).fetchall()
            if any(r['status'] not in TERMINAL for r in runs):
                raise self.Error('Stop the active response before deleting this conversation.',409)
            for run in runs:
                try:self.funding.account_for(dict(run))
                except Exception:
                    raise self.Error('Usage is still being saved. Try deleting this conversation again shortly.',503) from None
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT * FROM venture_conversations WHERE id=? AND uid=?',(cid,uid)).fetchone()
                if row is None:raise self.Error('This conversation is unavailable.',404)
                row=dict(row)
                if row['deleted_at'] is None:
                    active=db.execute("SELECT 1 FROM project_runs WHERE project_id=? AND status IN ('queued','preparing','submitting','in_progress','saving')",(cid,)).fetchone()
                    if active:raise self.Error('Stop the active response before deleting this conversation.',409)
                    # Tombstone prevents startup migration or a late tab reviving this ID.
                    db.execute("UPDATE venture_conversations SET deleted_at=?,purge_pending=1,title='',settings_json='{}',storage_warning=NULL,revision=revision+1 WHERE id=?",(time.time(),cid))
                    db.execute('DELETE FROM venture_memory WHERE cid=?',(cid,))
                    db.execute('DELETE FROM venture_memory_indexed WHERE run_id IN (SELECT id FROM project_runs WHERE project_id=?)',(cid,))
                    # Funding rows deliberately survive. Deletion is never a refund.
                    db.execute('DELETE FROM run_events WHERE run_id IN (SELECT id FROM project_runs WHERE project_id=?)',(cid,))
                    db.execute('DELETE FROM run_artifacts WHERE run_id IN (SELECT id FROM project_runs WHERE project_id=?)',(cid,))
                    db.execute('DELETE FROM project_runs WHERE project_id=?',(cid,))
                    if row['native']:db.execute('DELETE FROM projects WHERE id=?',(cid,))
            warning=None
            try:self.purge_deleted(row)
            except OSError:
                warning='Conversation removed from history and memory. Some files are locked; FUPCJ Server will retry disk cleanup after restart.'
            return dict(deleted=True,conversationId=cid,storageWarning=warning)

    def register_routes(self):
        app=self.app
        @app.route('/api/venture/conversations',methods=['GET','POST'])
        def venture_conversations():
            uid=self.account()
            if request.method=='POST':
                request.max_content_length=16384
                body=request.get_json(silent=True) or {}
                cid=body.get('id')
                if not isinstance(cid,str) or not CONVERSATION_ID.fullmatch(cid):
                    raise self.Error('A unique conversation ID is required.')
                settings=self.settings(body.get('settings') or DEFAULT_SETTINGS)
                now=time.time()
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    existing=db.execute('SELECT * FROM venture_conversations WHERE id=?',(cid,)).fetchone()
                    if existing:
                        if existing['deleted_at'] is not None:raise self.Error('This conversation was deleted. Start a new venture.',410)
                        if existing['uid']!=uid:raise self.Error('This conversation is unavailable.',404)
                        return jsonify(conversation=self.metadata(dict(existing))),200
                    if db.execute('SELECT 1 FROM projects WHERE id=?',(cid,)).fetchone():
                        raise self.Error('This identifier is already in use. Start another venture.',409)
                    db.execute('INSERT INTO projects(id,key_hash,project_json,revision,created_at,updated_at,owner_uid,title) VALUES(?,?,?,1,?,?,?,?)',
                               (cid,hashlib.sha256(secrets.token_bytes(32)).hexdigest(),'{"venture":true}',now,now,uid,'New venture'))
                    db.execute('INSERT INTO venture_conversations(id,uid,title,settings_json,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                               (cid,uid,'New venture',json.dumps(settings),now,now))
                self.identity(uid)
                self.mirror_metadata(cid)
                return jsonify(conversation=self.metadata(self.lookup(cid,uid))),201
            term=str(request.args.get('q','')).strip()[:150]
            cursor=request.args.get('before','')
            # Drafts and requests with no assistant output remain recoverable by
            # ID, but never take up a place in the user's conversation history.
            clauses=['uid=?','deleted_at IS NULL',"EXISTS(SELECT 1 FROM project_runs r WHERE r.project_id=venture_conversations.id AND (length(trim(r.text))>0 OR EXISTS(SELECT 1 FROM run_artifacts a WHERE a.run_id=r.id AND a.path IS NOT NULL)))"];params=[uid]
            if term:clauses.append("title LIKE ? ESCAPE '\\'");params.append('%'+re.sub(r'([%_\\])',r'\\\1',term)+'%')
            if cursor:
                try:
                    when,cid=json.loads(cursor)
                    when=float(when)
                    if not CONVERSATION_ID.fullmatch(cid):raise ValueError()
                except (ValueError,TypeError):raise self.Error('Invalid history cursor.')
                clauses.append('(updated_at<? OR (updated_at=? AND id<?))');params.extend([when,when,cid])
            with self.db() as db:
                rows=db.execute('SELECT * FROM venture_conversations WHERE '+' AND '.join(clauses)+' ORDER BY updated_at DESC,id DESC LIMIT 51',params).fetchall()
            more=len(rows)>50;rows=rows[:50]
            return jsonify(conversations=[self.metadata(dict(r)) for r in rows],
                           nextCursor=json.dumps([rows[-1]['updated_at'],rows[-1]['id']]) if more else None)

        @app.route('/api/venture/conversations/<cid>',methods=['GET','PATCH','DELETE'])
        def venture_conversation(cid):
            uid=self.account()
            if request.method=='DELETE':return jsonify(self.delete(cid,uid))
            row=self.lookup(cid,uid)
            if request.method=='PATCH':
                request.max_content_length=16384
                body=request.get_json(silent=True) or {}
                fields={}
                if set(body)-{'title','settings','revision'}:raise self.Error('Unknown conversation setting.')
                if 'title' in body:
                    title=body['title']
                    if not isinstance(title,str) or not 1<=len(title.strip())<=120 or re.search(r'[\x00-\x1f]',title):
                        raise self.Error('Use a conversation title from 1 to 120 characters.')
                    fields.update(title=title.strip(),manual_title=1)
                if 'settings' in body:fields['settings_json']=json.dumps(self.settings(body['settings']))
                if not fields:raise self.Error('No changes supplied.')
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row=self.lookup(cid,uid,db)
                    if body.get('revision')!=row['revision']:
                        raise self.Error('Conversation settings changed on another device. Reload before editing.',409)
                    db.execute('UPDATE venture_conversations SET '+','.join(k+'=?' for k in fields)+',revision=revision+1,updated_at=? WHERE id=?',
                               [*fields.values(),time.time(),cid])
                self.mirror_metadata(cid)
                return jsonify(conversation=self.metadata(self.lookup(cid,uid)))
            try:
                since=max(0,float(request.args.get('since',0)))
            except ValueError:raise self.Error('Invalid conversation cursor.')
            before=request.args.get('before')
            params=[cid];where='project_id=?'
            if since:where+=' AND updated_at>=?';params.append(since)
            if before:
                try:when,rid=json.loads(before);when=float(when)
                except (TypeError,ValueError):raise self.Error('Invalid message cursor.')
                where+=' AND (created_at<? OR (created_at=? AND id<?))';params.extend([when,when,rid])
            with self.db() as db:
                rows=db.execute('SELECT * FROM project_runs WHERE '+where+' ORDER BY created_at DESC,id DESC LIMIT 101',params).fetchall()
            more=len(rows)>100;rows=rows[:100]
            # Historical records are mirrored on demand without deleting their originals.
            if not since:
                for value in rows:
                    if value['status'] in ('completed','incomplete','cancelled','error'):
                        try:self.mirror(dict(value))
                        except OSError:pass
            runs=[{**self.sessions.snapshot(dict(r)),'retryOf':r['venture_retry_of']} for r in reversed(rows)]
            watermark=max([since]+[r['updated_at'] for r in rows])
            return jsonify(conversation=self.metadata(self.lookup(cid,uid)),runs=runs,cursor=watermark,
                           nextCursor=json.dumps([rows[-1]['created_at'],rows[-1]['id']]) if more else None)

        @app.get('/api/venture/conversations/<cid>/uploads/<rid>/<int:index>')
        def venture_original(cid,rid,index):
            uid=self.account();self.lookup(cid,uid)
            row=self.sessions.row(rid,cid);files=json.loads(row['inputs_json'])
            if index<0 or index>=len(files):raise self.Error('This attachment is unavailable.',404)
            file=files[index]
            if not Path(file['path']).is_file():raise self.Error('This attachment is missing on FUPCJ Server.',404)
            return send_file(file['path'],as_attachment=True,download_name=disk_name(file['name']),mimetype=file['mime'],max_age=0)

        @app.route('/api/venture/preferences',methods=['GET','PUT'])
        def venture_preferences():
            uid=self.account()
            if request.method=='PUT':
                request.max_content_length=16384
                settings=self.settings(request.get_json(silent=True))
                with self.db() as db:
                    db.execute('INSERT INTO venture_preferences VALUES(?,?,?) ON CONFLICT(uid) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at',
                               (uid,json.dumps(settings),time.time()))
            with self.db() as db:row=db.execute('SELECT settings_json FROM venture_preferences WHERE uid=?',(uid,)).fetchone()
            return jsonify(settings=json.loads(row[0]) if row else DEFAULT_SETTINGS)

        @app.get('/api/venture/models')
        def venture_models():
            from sessions import OPENAI, unprotect_secret
            uid=self.account()
            with self.db() as db:
                saved=db.execute('SELECT openai_key_cipher FROM account_credentials WHERE uid=?',(uid,)).fetchone()
            if not saved:return jsonify(models=[],status='no-key')
            try:key=unprotect_secret(saved[0])
            except Exception:raise self.Error('Save your API key again to load models.',503) from None
            cache_key=(uid,key_id(key));cached=self.model_cache.get(cache_key)
            if cached and time.monotonic()-cached[0]<300 and request.args.get('refresh')!='1':
                return jsonify(models=cached[1],status='available')
            try:
                # Read-only discovery, never a model request or a billed probe.
                with requests.get(OPENAI+'/models',headers={'Authorization':'Bearer '+key},timeout=(5,10)) as response:
                    if response.status_code!=200:raise self.Error('OpenAI could not list models. Check this API key’s model permissions.',502)
                    payload=response.json()
                items=payload.get('data')
                if not isinstance(items,list):raise ValueError('Invalid model list')
                ids={m['id'] for m in items if isinstance(m,dict) and isinstance(m.get('id'),str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',m['id'])}
                models=[dict(id=id) for id in sorted(ids)]
            except (requests.RequestException,ValueError,AttributeError,TypeError):
                raise self.Error('OpenAI’s model list is temporarily unavailable. Try refreshing it.',502) from None
            if len(self.model_cache)>200:self.model_cache.clear()
            self.model_cache[cache_key]=(time.monotonic(),models)
            return jsonify(models=models,status='available')

        @app.route('/api/venture/funding',methods=['GET','POST'])
        def venture_funding():
            uid=self.account();kid=self.current_key_id(uid)
            if request.method=='POST':
                request.max_content_length=8192
                body=request.get_json(silent=True)
                if not isinstance(body,dict):raise self.Error('Send a calibration amount.')
                if not kid:raise self.Error('Save your API key before calibrating its funding estimate.')
                if body.get('connectionId',kid)!=kid:raise self.Error('Your API connection changed. Check its balance before saving.',409)
                return jsonify(self.funding.calibrate(uid,kid,body))
            return jsonify(self.funding.status(uid,kid))
