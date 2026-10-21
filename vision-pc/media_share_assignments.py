"""Central, durable claims for MediaShare slide images across stream managers.

Claims are retained even when uploads expire, disappear, or are replaced. A
failed/ambiguous destination request never releases an image to another service.
Claimed Firestore image chunks are also retained for durable destination retries;
they require explicit future maintenance rather than automatic upload cleanup.
"""
from datetime import datetime
import json
import re
import time
from zoneinfo import ZoneInfo

import requests
from flask import jsonify, request

EASTERN = ZoneInfo('America/New_York')


def decode_field(value):
    for kind in ('stringValue','timestampValue','booleanValue','nullValue'):
        if kind in value:return value[kind]
    if 'integerValue' in value:return int(value['integerValue'])
    if 'doubleValue' in value:return float(value['doubleValue'])
    if 'arrayValue' in value:return [decode_field(item) for item in value['arrayValue'].get('values',[])]
    if 'mapValue' in value:return {key:decode_field(item) for key,item in value['mapValue'].get('fields',{}).items()}
    return None


class Assignments:
    def __init__(self,media):
        self.media=media
        self.now=lambda:datetime.now(EASTERN)

    def initialize(self):
        with self.media.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS media_share_assignments (
                image_key TEXT PRIMARY KEY, service_id TEXT NOT NULL, drop_id TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'reserved', snapshot TEXT NOT NULL,
                destinations TEXT NOT NULL DEFAULT '[]', receipts TEXT NOT NULL DEFAULT '{}',
                created REAL NOT NULL, updated REAL NOT NULL)''')
            columns={row['name'] for row in db.execute('PRAGMA table_info(media_share_assignments)')}
            if 'receipts' not in columns:
                db.execute("ALTER TABLE media_share_assignments ADD COLUMN receipts TEXT NOT NULL DEFAULT '{}'")
            if 'service_start' not in columns:
                db.execute("ALTER TABLE media_share_assignments ADD COLUMN service_start TEXT NOT NULL DEFAULT ''")
            if 'selection_mode' not in columns:
                db.execute("ALTER TABLE media_share_assignments ADD COLUMN selection_mode TEXT NOT NULL DEFAULT 'auto'")
            db.execute('CREATE INDEX IF NOT EXISTS media_share_assignment_service ON media_share_assignments(service_id)')

    def identifiers(self,value):
        if not isinstance(value,dict):raise self.media.Error('A JSON object is required.')
        result=[]
        for name,pattern in [('serviceId',r'[A-Za-z0-9_:-]{1,160}'),
                             ('dropId',r'[A-Za-z0-9_-]{1,160}'),
                             ('imageKey',r'[A-Za-z0-9_-]{1,160}')]:
            item=value.get(name)
            if not isinstance(item,str) or not re.fullmatch(pattern,item):
                raise self.media.Error('Invalid '+name+'.')
            result.append(item)
        return result

    @staticmethod
    def local_drop(row):
        return dict(id=row['id'],kind='drop',
            images=[dict(key=row['image_key'],chunks=row['chunks'],name=row['name'],type='image/jpeg')],
            createdAt=datetime.fromtimestamp(row['created'],EASTERN).isoformat(),
            availableStartingDate=row['available_starting_date'],autoTitle=row['title'],
            targetServicePeriod=row.get('target_service_period',''),
            titleStatus=row['title_status'],titleRevision=row['title_revision'],
            titleImageKey=row['image_key'],titleUncertain=bool(row['uncertain']),
            imageRevision=row['image_revision'],revision=row['revision'],text=row['text'])

    def local(self,ident,db=None):
        if db is None:
            with self.media.db() as connection:return self.local(ident,connection)
        row=db.execute('SELECT * FROM media_share_drops WHERE id=?',(ident,)).fetchone()
        if row is None:return None
        if row['deleted']:raise self.media.Error('The reserved slide has been removed.',404)
        return self.local_drop(dict(row))

    def source(self,ident):
        local=self.local(ident)
        if local is not None:return local
        # Existing/older Firestore-only drops remain eligible. Read with the
        # already-verified MediaShare caller identity; never trust supplied dates.
        from media_share import BASE
        try:
            with requests.get(BASE+ident,headers={'Authorization':request.headers.get('Authorization','')},timeout=(5,20)) as response:
                if response.status_code==404:raise self.media.Error('The reserved slide has been removed.',404)
                response.raise_for_status()
                document=response.json()
        except (requests.RequestException,ValueError):
            raise self.media.Error('Could not verify MediaShare slide metadata. Retry when connected.',503) from None
        drop={key:decode_field(value) for key,value in document.get('fields',{}).items()}
        drop['id']=ident
        if drop.get('kind')!='drop':raise self.media.Error('This is not a MediaShare slide.',409)
        return drop

    def service_start(self,value):
        if value is None or value=='':return ''
        if not isinstance(value,str) or len(value)>80:
            raise self.media.Error('serviceStart must be an ISO date and time with a timezone offset.')
        try:
            start=datetime.fromisoformat(value.replace('Z','+00:00'))
            if start.tzinfo is None or start.utcoffset() is None:raise ValueError()
            return start.astimezone(EASTERN).isoformat()
        except (ValueError,OverflowError):
            raise self.media.Error('serviceStart must be an ISO date and time with a timezone offset.') from None

    def validate_candidate(self,drop,image_key,service_start='',manual=False):
        images=drop.get('images') or []
        if not isinstance(images,list) or not images or not isinstance(images[0],dict) or images[0].get('key')!=image_key:
            raise self.media.Error('The slide image changed. Refresh before reserving.',409)
        from media_share import available_starting_date, target_service_period
        try:
            available=available_starting_date(drop.get('availableStartingDate'))
            period=target_service_period(drop.get('targetServicePeriod'),available)
        except ValueError:raise self.media.Error('The slide availability date or Sunday service is invalid.',409) from None
        if manual:
            if not service_start:raise self.media.Error('Manual selection requires the service time.',409)
            return  # Explicit operator choice overrides dates, never ownership.
        now=self.now()
        if now.tzinfo is None:now=now.replace(tzinfo=EASTERN)
        if available and available>now.astimezone(EASTERN).date().isoformat():
            raise self.media.Error('This slide is not available yet in America/New_York.',409)
        if available and not service_start:
            raise self.media.Error('Update Livestream Manager to provide the service time for dated slides.',409)
        if service_start:
            start=datetime.fromisoformat(service_start)
            service_date=start.date().isoformat()
            if available and available!=service_date:
                raise self.media.Error('This slide is scheduled for a different service date in America/New_York.',409)
            if available==service_date and period and period!=('am' if start.hour<12 else 'pm'):
                raise self.media.Error('This slide is reserved for the other Sunday service period.',409)

    def existing_claim(self,row,service_id,drop_id,service_start,mode='auto',db=None):
        if row['service_id']!=service_id or row['drop_id']!=drop_id:
            raise self.media.Error('This slide is already reserved for another service.',409)
        if row['service_start'] and service_start and row['service_start']!=service_start:
            raise self.media.Error('This service already reserved the slide with a different start time.',409)
        # Legacy claims remain in history, but a retry cannot authorize applying
        # another day's slide under the old available-starting-date rule.
        self.validate_candidate(json.loads(row['snapshot']),row['image_key'],row['service_start'] or service_start,mode=='manual')
        if row['selection_mode']!=mode:
            if db is None:
                with self.media.db() as connection:
                    connection.execute('UPDATE media_share_assignments SET selection_mode=? WHERE image_key=?',(mode,row['image_key']))
            else:db.execute('UPDATE media_share_assignments SET selection_mode=? WHERE image_key=?',(mode,row['image_key']))
        return self.public({**dict(row),'selection_mode':mode})

    @staticmethod
    def public(row):
        return dict(serviceId=row['service_id'],dropId=row['drop_id'],imageKey=row['image_key'],
            serviceStart=row['service_start'],
            selectionMode=row['selection_mode'],
            state=row['state'],destinations=json.loads(row['destinations']),
            receipts=json.loads(row['receipts']),drop=json.loads(row['snapshot']))

    def reserve(self,value):
        service_id,drop_id,image_key=self.identifiers(value)
        service_start=self.service_start(value.get('serviceStart'))
        mode=value.get('selectionMode','auto')
        if mode not in ('auto','manual'):raise self.media.Error('Invalid selectionMode.')
        with self.media.db() as db:
            existing=db.execute('SELECT * FROM media_share_assignments WHERE image_key=?',(image_key,)).fetchone()
        if existing:
            return self.existing_claim(existing,service_id,drop_id,service_start,mode)
        drop=self.source(drop_id)
        self.validate_candidate(drop,image_key,service_start,mode=='manual')
        with self.media.lock,self.media.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # A competing manager may have claimed the same image during fetch.
            existing=db.execute('SELECT * FROM media_share_assignments WHERE image_key=?',(image_key,)).fetchone()
            if existing:
                return self.existing_claim(existing,service_id,drop_id,service_start,mode,db)
            # Recheck local edits inside the claim transaction.
            drop=self.local(drop_id,db) or drop
            self.validate_candidate(drop,image_key,service_start,mode=='manual')
            now=time.time()
            db.execute('INSERT INTO media_share_assignments(image_key,service_id,drop_id,snapshot,service_start,selection_mode,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                       (image_key,service_id,drop_id,json.dumps(drop),service_start,mode,now,now))
            return self.public(db.execute('SELECT * FROM media_share_assignments WHERE image_key=?',(image_key,)).fetchone())

    def used(self,value):
        service_id,drop_id,image_key=self.identifiers(value)
        destination=value.get('destination')
        if destination not in ('youtube','planning_center'):raise self.media.Error('Invalid destination.')
        kind=value.get('kind','image')
        if kind not in ('image','title'):raise self.media.Error('Invalid receipt kind.')
        version=value.get('version',image_key if kind=='image' else '')
        destination_id=value.get('destinationId',service_id.removeprefix('youtube:') if destination=='youtube' else '')
        if not isinstance(version,str) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,200}',version):
            raise self.media.Error('Invalid receipt version.')
        if not isinstance(destination_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',destination_id):
            raise self.media.Error('A destination ID is required for the receipt.')
        with self.media.lock,self.media.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM media_share_assignments WHERE image_key=?',(image_key,)).fetchone()
            if not row or row['service_id']!=service_id or row['drop_id']!=drop_id:
                raise self.media.Error('Reserve this exact slide for the service before marking it used.',409)
            destinations=set(json.loads(row['destinations']))
            if kind=='image':destinations.add(destination)
            receipts=json.loads(row['receipts'])
            receipts[destination+':'+kind+':'+destination_id]=version
            db.execute("UPDATE media_share_assignments SET state='used',destinations=?,receipts=?,updated=? WHERE image_key=?",
                       (json.dumps(sorted(destinations)),json.dumps(receipts),time.time(),image_key))
            return self.public(db.execute('SELECT * FROM media_share_assignments WHERE image_key=?',(image_key,)).fetchone())

    def refresh(self,row):
        snapshot=json.loads(row['snapshot'])
        try:
            current=self.source(row['drop_id'])
            images=current.get('images') or []
            exact=bool(isinstance(images,list) and images and isinstance(images[0],dict) and images[0].get('key')==row['image_key'])
            if exact:snapshot=current
            elif snapshot.get('titleStatus') not in ('complete','error'):
                snapshot.update(titleStatus='superseded',autoTitle=None,titleUncertain=False)
        except self.media.Error as exc:
            if exc.status!=404:raise
            if snapshot.get('titleStatus') not in ('complete','error'):
                snapshot.update(titleStatus='unavailable',autoTitle=None,titleUncertain=False)
        with self.media.db() as db:
            db.execute('UPDATE media_share_assignments SET snapshot=? WHERE image_key=?',(json.dumps(snapshot),row['image_key']))
        value=self.public(row);value['drop']=snapshot
        return value

    def register(self):
        @self.media.app.post('/api/media-share/assignments/availability')
        def availability():
            value=request.get_json(silent=True) or {}
            if not isinstance(value,dict):raise self.media.Error('A JSON object is required.')
            service_id=value.get('serviceId','')
            keys=value.get('imageKeys')
            if not isinstance(service_id,str) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,160}',service_id):
                raise self.media.Error('Invalid serviceId.')
            if not isinstance(keys,list) or len(keys)>200 or any(not isinstance(k,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',k) for k in keys):
                raise self.media.Error('Supply up to 200 valid image keys.')
            with self.media.db() as db:
                rows=db.execute('SELECT image_key,service_id,state FROM media_share_assignments WHERE image_key IN ('+','.join('?' for _ in keys)+')',keys).fetchall() if keys else []
            claims={row['image_key']:row for row in rows}
            return jsonify(availability=[dict(imageKey=key,selectable=key not in claims or claims[key]['service_id']==service_id,
                state=claims[key]['state'] if key in claims else 'unused') for key in keys])

        @self.media.app.post('/api/media-share/assignments/reserve')
        def reserve():return jsonify(assignment=self.reserve(request.get_json(silent=True) or {}))

        @self.media.app.post('/api/media-share/assignments/used')
        def used():return jsonify(assignment=self.used(request.get_json(silent=True) or {}))

        @self.media.app.get('/api/media-share/assignments')
        def assignments():
            service_id=request.args.get('serviceId','')
            if not re.fullmatch(r'[A-Za-z0-9_:-]{1,160}',service_id):raise self.media.Error('Invalid serviceId.')
            image_key=request.args.get('imageKey')
            with self.media.db() as db:
                rows=db.execute('SELECT * FROM media_share_assignments WHERE service_id=? ORDER BY created',(service_id,)).fetchall()
            return jsonify(assignments=[self.refresh(row) for row in rows if not image_key or row['image_key']==image_key])
