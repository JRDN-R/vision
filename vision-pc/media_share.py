"""Durable MediaShare uploads and Firestore outbox; OCR uses TitleEyes unchanged.

Only fupc-media Firebase tokens reach these routes. MediaShare is an existing
shared anonymous-auth workspace, not an extension of Vision account access.
"""
import base64
from datetime import datetime, timezone
import json
import re
import secrets
import threading
import time
from urllib.parse import urlencode

import requests
from flask import g, jsonify, request
from firebase_auth import FirebaseIdentity, InvalidIdentity, IdentityUnavailable
from title_eyes import MAX_BYTES, ACTIVE, prepare_image

PROJECT = 'fupc-media'
API_KEY = 'AIzaSyCZXC3ALGAKcv8eJRMaO_rX-_u5d5FBZ24'  # Public Firebase client identifier.
ORIGIN = 'https://firstupc.github.io'
BASE = f'https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents/drops/'
RETENTION = 30 * 86400


def field(value):
    if value is None:return {'nullValue':None}
    if isinstance(value,bool):return {'booleanValue':value}
    if isinstance(value,int):return {'integerValue':str(value)}
    if isinstance(value,list):return {'arrayValue':{'values':[field(v) for v in value]}}
    if isinstance(value,dict):return {'mapValue':{'fields':{k:field(v) for k,v in value.items()}}}
    return {'stringValue':str(value)}


class Publisher:
    """Uses the same anonymous Firebase access as MediaShare, without browser tokens."""
    def __init__(self, data_dir):
        self.path=data_dir/'mediashare-writer.json'
        self.token='';self.refresh='';self.expires=0
        try:self.refresh=json.loads(self.path.read_text())['refresh']
        except (OSError,ValueError,KeyError):pass

    def auth(self):
        if time.monotonic()<self.expires:return self.token
        if self.refresh:
            with requests.post('https://securetoken.googleapis.com/v1/token',params={'key':API_KEY},
                               data={'grant_type':'refresh_token','refresh_token':self.refresh},timeout=(5,15)) as r:
                if r.ok:
                    data=r.json();self.token=data['id_token'];self.refresh=data['refresh_token']
                elif r.status_code in (400,401):self.refresh=''
                else:r.raise_for_status()
        if not self.refresh:
            with requests.post('https://identitytoolkit.googleapis.com/v1/accounts:signUp',params={'key':API_KEY},
                               json={'returnSecureToken':True},timeout=(5,15)) as r:
                r.raise_for_status();data=r.json();self.token=data['idToken'];self.refresh=data['refreshToken']
        self.expires=time.monotonic()+3000
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps({'refresh':self.refresh}));temp.chmod(0o600);temp.replace(self.path)
        return self.token

    def write(self, ident, values=None):
        params=urlencode([('updateMask.fieldPaths',k) for k in values]) if values is not None else ''
        body={'fields':{k:field(v) for k,v in values.items()}} if values is not None else None
        if body and 'createdAt' in values:
            body['fields']['createdAt']={'timestampValue':values['createdAt']}
        with requests.request('PATCH' if values is not None else 'DELETE',BASE+ident+('?' +params if params else ''),
                              json=body,headers={'Authorization':'Bearer '+self.auth()},timeout=(5,20)) as r:
            if r.status_code==401:self.expires=0
            if values is None and r.status_code==404:return
            r.raise_for_status()

    def publish(self,row):
        if row['deleted']:
            self.write(row['id']);return
        parts=[]
        if row['image'] is not None:
            encoded='data:image/jpeg;base64,'+base64.b64encode(row['image']).decode('ascii')
            parts=[encoded[i:i+700000] for i in range(0,len(encoded),700000)]
            for i,piece in enumerate(parts):
                self.write(f"chunk_{row['image_key']}_{i}",dict(kind='chunk',key=row['image_key'],index=i,data=piece))
        metadata=dict(kind='drop',managedBy='fupcj-title-eyes',text=row['text'],
            images=[dict(key=row['image_key'],chunks=row['chunks'],name=row['name'],type='image/jpeg')],
            createdAt=datetime.fromtimestamp(row['created'],timezone.utc).isoformat(),
            imageRevision=row['image_revision'],revision=row['revision'],autoTitle=row['title'],
            titleStatus=row['title_status'],titleRevision=row['title_revision'],
            titleImageKey=row['image_key'],titleUncertain=bool(row['uncertain']))
        self.write(row['id'],metadata)


class MediaShare:
    def __init__(self,app,db,error,titles):
        self.app,self.db,self.Error,self.titles=app,db,error,titles
        self.identity=FirebaseIdentity(PROJECT,providers=('anonymous','google.com','password'))
        self.lock=threading.RLock();self.changed=threading.Condition();self.sequence=secrets.token_hex(8)
        self.waiters=threading.BoundedSemaphore(3)
        self.stop=threading.Event();self.wake=threading.Event()
        self.register()

    def initialize(self):
        self.stop.clear()
        self.publisher=Publisher(self.app.config['DATA_DIR'])
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS media_share_garbage (key TEXT PRIMARY KEY,chunks INTEGER NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS media_share_drops (
                id TEXT PRIMARY KEY, uid TEXT NOT NULL, request_id TEXT NOT NULL,
                image_key TEXT NOT NULL, image BLOB, chunks INTEGER NOT NULL,
                name TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', job TEXT NOT NULL,
                image_revision INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 1,
                title TEXT, title_status TEXT NOT NULL DEFAULT 'queued', title_revision INTEGER NOT NULL DEFAULT 0,
                uncertain INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL,
                published INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
                retry_at REAL NOT NULL DEFAULT 0, publish_error TEXT NOT NULL DEFAULT '', UNIQUE(uid,request_id))''')

    def authenticate(self):
        origin=request.headers.get('Origin')
        if origin and origin!=ORIGIN:raise self.Error('This is a MediaShare-only endpoint.',403)
        if request.method=='OPTIONS':return
        token=request.headers.get('Authorization','').removeprefix('Bearer ')
        try:claims=self.identity.verify(token)
        except InvalidIdentity:raise self.Error('Connect to MediaShare again.',401) from None
        except IdentityUnavailable:raise self.Error('MediaShare sign-in is temporarily unavailable.',503) from None
        g.uid='mediashare:'+claims['sub'];g.auth_kind='mediashare'
        request.max_content_length=MAX_BYTES+65536

    def row(self,ident):
        with self.db() as db:row=db.execute('SELECT * FROM media_share_drops WHERE id=?',(ident,)).fetchone()
        if not row or row['deleted']:raise self.Error('This drop is no longer available on FUPCJ Server.',404)
        return dict(row)

    def public(self,row):
        job=self.titles.row(row['job'])
        return dict(id=row['id'],revision=row['revision'],imageKey=row['image_key'],imageRevision=row['image_revision'],
            title=row['title'],titleStatus=row['title_status'],titleRevision=row['title_revision'],uncertain=bool(row['uncertain']),
            phase=(job or {}).get('phase',''),text=row['text'],published=row['published']>=row['revision'],
            publishError=row['publish_error'])

    def signal(self):
        with self.changed:self.sequence=secrets.token_hex(8);self.changed.notify_all()
        self.wake.set()

    def register(self):
        @self.app.get('/api/media-share/changes')
        def changes():
            try:wait=max(0,min(20,float(request.args.get('wait','20'))))
            except ValueError:raise self.Error('Invalid wait duration.')
            if self.waiters.acquire(blocking=False):
                try:
                    with self.changed:
                        if request.args.get('after')==self.sequence:self.changed.wait(wait)
                finally:self.waiters.release()
            return jsonify(sequence=self.sequence)

        @self.app.get('/api/media-share/requests/<rid>')
        def recover(rid):
            with self.db() as db:row=db.execute('SELECT * FROM media_share_drops WHERE uid=? AND request_id=? AND deleted=0',(g.uid,rid)).fetchone()
            if not row:raise self.Error('Upload not received.',404)
            return jsonify(self.public(dict(row)))

        @self.app.post('/api/media-share/drops')
        def create():return self.upload()

        @self.app.route('/api/media-share/drops/<ident>',methods=['GET','PATCH','DELETE','POST'])
        def item(ident):
            if request.method=='POST':return self.upload(ident)
            with self.lock:
                row=self.row(ident)
                if request.method=='GET':return jsonify(self.public(row))
                value=request.get_json(silent=True) or {}
                if type(value.get('revision')) is not int or value['revision']!=row['revision']:
                    raise self.Error('This drop changed. Refresh before saving.',409)
                with self.db() as db:
                    if request.method=='DELETE':
                        db.execute('INSERT OR IGNORE INTO media_share_garbage VALUES(?,?)',(row['image_key'],row['chunks']))
                        db.execute('UPDATE media_share_drops SET deleted=1,image=NULL,revision=revision+1,retry_at=0 WHERE id=?',(ident,))
                        db.execute('DELETE FROM title_eyes_jobs WHERE id=?',(row['job'],))
                    else:
                        if 'title' in value:
                            if row['title_status']!='complete':raise self.Error('Wait until the model finishes before editing.',409)
                            title=value['title']
                            if not isinstance(title,str) or not title.strip() or len(title)>100 or re.search(r'[<>\x00-\x1f]',title):
                                raise self.Error('Use a title of 1–100 characters, without line breaks or angle brackets.')
                            db.execute("UPDATE media_share_drops SET title=?,uncertain=0,title_revision=title_revision+1 WHERE id=?",(title.strip(),ident))
                        if 'text' in value:
                            if not isinstance(value['text'],str) or len(value['text'])>20000:raise self.Error('Text is too long.')
                            db.execute('UPDATE media_share_drops SET text=? WHERE id=?',(value['text'],ident))
                        db.execute('UPDATE media_share_drops SET revision=revision+1,updated=?,retry_at=0 WHERE id=?',(time.time(),ident))
            self.signal()
            return jsonify(deleted=True) if request.method=='DELETE' else jsonify(self.public(self.row(ident)))

    def upload(self,ident=None):
        rid=request.form.get('requestId','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',rid):raise self.Error('A unique upload request ID is required.')
        with self.lock,self.db() as db:
            old=db.execute('SELECT * FROM media_share_drops WHERE uid=? AND request_id=?',(g.uid,rid)).fetchone()
            if old:
                if old['deleted']:raise self.Error('That upload has been removed. Choose the image again.',410)
                return jsonify(self.public(dict(old))),202
        settings=self.titles.sessions.local.settings
        if not settings.get('enabled') or not settings.get('visualEnabled'):raise self.Error('Enable the title-eyes model on FUPCJ Server.',503)
        file=request.files.get('image')
        if not file:raise self.Error('Choose a slide image.')
        try:picture=prepare_image(file.read(MAX_BYTES+1))
        except ValueError as e:raise self.Error(str(e)) from None
        with self.lock,self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM media_share_drops WHERE uid=? AND request_id=?',(g.uid,rid)).fetchone()
            if old:
                if old['deleted']:raise self.Error('That upload has been removed. Choose the image again.',410)
                return jsonify(self.public(dict(old))),202
            if db.execute(f'SELECT COUNT(*) FROM title_eyes_jobs WHERE status IN {ACTIVE}').fetchone()[0]>=20:
                raise self.Error('The title queue is full. Try again shortly.',429)
            if db.execute(f'SELECT COUNT(*) FROM title_eyes_jobs WHERE uid=? AND status IN {ACTIVE}',(g.uid,)).fetchone()[0]>=3:
                raise self.Error('Three slides are already waiting for this device.',429)
            if db.execute('SELECT COUNT(*) FROM media_share_drops WHERE created>?',(time.time()-3600,)).fetchone()[0]>=100:
                raise self.Error('The hourly MediaShare upload limit has been reached.',429)
            if not ident and db.execute('SELECT COUNT(*) FROM media_share_drops').fetchone()[0]>=500:
                raise self.Error('MediaShare server storage is full. Remove older drops or try later.',429)
            prior=self.row(ident) if ident else None
            if prior and str(prior['revision'])!=request.form.get('revision'):raise self.Error('The slide changed. Refresh before replacing it.',409)
            ident=ident or secrets.token_hex(16);key=secrets.token_hex(16);job=secrets.token_hex(12);now=time.time()
            chunks=(len('data:image/jpeg;base64,')+4*((len(picture)+2)//3)+699999)//700000
            if prior:
                db.execute('INSERT OR IGNORE INTO media_share_garbage VALUES(?,?)',(prior['image_key'],prior['chunks']))
                db.execute('DELETE FROM title_eyes_jobs WHERE id=?',(prior['job'],))
                db.execute('''UPDATE media_share_drops SET uid=?,request_id=?,image_key=?,image=?,chunks=?,name=?,job=?,
                    image_revision=image_revision+1,revision=revision+1,title=NULL,title_status='queued',title_revision=0,
                    uncertain=0,created=?,updated=?,retry_at=0 WHERE id=?''',
                    (g.uid,rid,key,picture,chunks,'slide.jpg',job,now,now,ident))
            else:
                db.execute('INSERT INTO media_share_drops(id,uid,request_id,image_key,image,chunks,name,job,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (ident,g.uid,rid,key,picture,chunks,'slide.jpg',job,now,now))
            db.execute('INSERT INTO title_eyes_jobs(id,uid,request_id,status,phase,image,created,expires) VALUES(?,?,?,?,?,?,?,?)',
                       (job,g.uid,rid,'queued','Waiting for title worker',picture,now,now+7*86400))
        self.titles.wake.set();self.signal()
        return jsonify(self.public(self.row(ident))),202

    def sync_once(self):
        now=time.time()
        with self.lock,self.db() as db:
            # Joining the current job ID prevents a replaced slide's late result
            # from touching the replacement or a subsequent manual correction.
            rows=db.execute("SELECT m.id,j.status,j.result,j.error FROM media_share_drops m JOIN title_eyes_jobs j ON j.id=m.job WHERE m.deleted=0 AND m.title_status!='complete' AND m.title_status!=j.status").fetchall()
            for row in rows:
                result=json.loads(row['result']) if row['result'] else {}
                db.execute('UPDATE media_share_drops SET title_status=?,title=?,uncertain=?,title_revision=title_revision+1,revision=revision+1,retry_at=0 WHERE id=?',
                           (row['status'],result.get('title'),int(result.get('uncertain',False)),row['id']))
            db.execute("UPDATE media_share_drops SET title_status='error',revision=revision+1,retry_at=0 WHERE deleted=0 AND title_status IN ('queued','processing') AND job NOT IN (SELECT id FROM title_eyes_jobs)")
            db.execute('DELETE FROM media_share_drops WHERE updated<? AND published>=revision',(now-RETENTION,))
            pending=db.execute('SELECT * FROM media_share_drops WHERE published<revision AND retry_at<=? ORDER BY updated LIMIT 1',(now,)).fetchone()
        if rows:self.signal()
        if not pending:
            # Delete replaced image chunks only after all pending metadata has
            # published, so readers can still use the previous committed image.
            with self.db() as db:
                waiting=db.execute('SELECT 1 FROM media_share_drops WHERE published<revision LIMIT 1').fetchone()
                garbage=db.execute('SELECT * FROM media_share_garbage LIMIT 1').fetchone()
            if garbage and not waiting:
                try:
                    for i in range(garbage['chunks']):self.publisher.write(f"chunk_{garbage['key']}_{i}")
                    with self.db() as db:db.execute('DELETE FROM media_share_garbage WHERE key=?',(garbage['key'],))
                except Exception:pass
            return
        row=dict(pending)
        try:self.publisher.publish(row)
        except Exception:
            with self.db() as db:db.execute("UPDATE media_share_drops SET retry_at=?,publish_error='Waiting to sync with MediaShare; FUPCJ Server retained this upload.' WHERE id=?",(now+15,row['id']))
            return
        with self.db() as db:
            db.execute("UPDATE media_share_drops SET published=?,publish_error='',image=CASE WHEN image_key=? THEN NULL ELSE image END WHERE id=?",
                       (row['revision'],row['image_key'],row['id']))
        self.signal()

    def start(self):
        def run():
            while not self.stop.is_set():
                try:self.sync_once()
                except Exception:self.app.logger.error('MediaShare background sync will retry.')
                self.wake.wait(2);self.wake.clear()
        threading.Thread(target=run,name='mediashare-sync',daemon=True).start()
