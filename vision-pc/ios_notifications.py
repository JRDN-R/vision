"""Account approval APNs alerts; optional, encrypted, durable and account scoped.

The existing audited decision journal is the outbox. Local download completion
is emitted on the phone after verification, never from a server-ready job.
"""
import hashlib
import re
import threading
import time
from flask import jsonify, request
from ios_live_activities import HEX_TOKEN

class IOSNotifications:
    def __init__(self, app, db, Error, transport):
        self.app, self.db, self.Error, self.transport = app, db, Error, transport
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        app.add_url_rule('/api/ios/notification-device', 'ios_notification_device', self.device, methods=['PUT','DELETE'])

    def initialize(self):
        self.close()
        self.stop.clear()
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS ios_notification_devices (
                token_hash TEXT PRIMARY KEY, uid TEXT NOT NULL, installation TEXT NOT NULL,
                token_cipher BLOB NOT NULL, last_decision INTEGER NOT NULL,
                expires REAL NOT NULL, next_attempt REAL NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0)''')

    def device(self):
        uid = self.transport._authorize()
        self.transport._limit(uid)
        request.max_content_length = 4096
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {'token','installation'}:
            raise self.Error('Invalid notification registration.', 400)
        token, installation = body['token'], body['installation']
        if not isinstance(token,str) or not HEX_TOKEN.fullmatch(token) or not isinstance(installation,str) or not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',installation):
            raise self.Error('Invalid notification registration.', 400)
        token = token.lower()
        digest = hashlib.sha256(token.encode()).hexdigest()
        if request.method == 'DELETE':
            with self.db() as db:
                db.execute('DELETE FROM ios_notification_devices WHERE token_hash=? AND installation=? AND uid=?',(digest,installation,uid))
            return jsonify(removed=True)
        if not self.transport.ready:
            raise self.Error('Apple push is not configured. In-app approval checks still work.',503)
        cipher = self.transport.encrypt(token)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM ios_notification_devices WHERE token_hash=?',(digest,)).fetchone()
            if old and old['installation'] != installation and old['uid'] != uid:
                raise self.Error('Device registration does not match this installation.',409)
            if old is None:
                old = db.execute('SELECT * FROM ios_notification_devices WHERE installation=?',(installation,)).fetchone()
            count = db.execute('SELECT COUNT(*) FROM ios_notification_devices WHERE uid=? AND installation!=? AND expires>?',(uid,installation,time.time())).fetchone()[0]
            if count >= 20:
                raise self.Error('This account has too many notification devices.',429)
            last = old['last_decision'] if old and old['uid']==uid else db.execute('SELECT COALESCE(MAX(id),0) FROM gemini_access_decisions WHERE uid=?',(uid,)).fetchone()[0]
            db.execute('DELETE FROM ios_notification_devices WHERE installation=? AND token_hash!=?',(installation,digest))
            db.execute('INSERT OR REPLACE INTO ios_notification_devices(token_hash,uid,installation,token_cipher,last_decision,expires) VALUES(?,?,?,?,?,?)',(digest,uid,installation,cipher,last,time.time()+90*86400))
        return jsonify(registered=True)

    def process_once(self):
        t = self.transport
        if not t.ready or not self.lock.acquire(blocking=False): return 0
        sent = 0
        try:
            now = time.time()
            with self.db() as db:
                db.execute('DELETE FROM ios_notification_devices WHERE expires<=?',(now,))
                # Only devices with new decisions enter the batch; idle devices
                # cannot starve later registrations indefinitely.
                rows = [dict(r) for r in db.execute('''SELECT d.* FROM ios_notification_devices d
                    WHERE next_attempt<=? AND EXISTS (SELECT 1 FROM gemini_access_decisions a
                    WHERE a.uid=d.uid AND a.id>d.last_decision)
                    ORDER BY next_attempt,token_hash LIMIT 100''',(now,))]
            for row in rows:
                if self.stop.is_set(): break
                uid, digest = row['uid'], row['token_hash']
                if t.accounts.blocked(uid):
                    with self.db() as db: db.execute('DELETE FROM ios_notification_devices WHERE token_hash=? AND uid=?',(digest,uid))
                    continue
                with self.db() as db:
                    decision = db.execute('SELECT id,status FROM gemini_access_decisions WHERE uid=? AND id>? ORDER BY id DESC LIMIT 1',(uid,row['last_decision'])).fetchone()
                if not decision: continue
                delivered = decision['status'] != 'approved'
                try:
                    if not delivered:
                        token = t.decrypt(row['token_cipher'])
                        if not HEX_TOKEN.fullmatch(token): raise ValueError('Invalid protected token')
                        base = 'https://api.sandbox.push.apple.com' if t.settings['environment']=='sandbox' else 'https://api.push.apple.com'
                        payload = {'aps':{'alert':{'title':'Account upgrade approved','body':'Your account now has access to Gemini and Luna.'},'sound':'default'},'kind':'upgrade','uid':uid.removeprefix('firebase:'),'decisionID':decision['id']}
                        headers = {'authorization':'bearer '+t._provider(now),'apns-topic':t.settings['bundleId'],'apns-push-type':'alert','apns-priority':'10','apns-expiration':str(int(now)+300),'apns-collapse-id':'account-upgrade','content-type':'application/json'}
                        response = t.client.post(base+'/3/device/'+token,json=payload,headers=headers)
                        code=response.status_code;response.close()
                        if code in (400,404,410):
                            with self.db() as db: db.execute('DELETE FROM ios_notification_devices WHERE token_hash=? AND uid=?',(digest,uid))
                            continue
                        if code!=200: raise ValueError('Push deferred')
                        delivered=True;sent+=1
                    if delivered:
                        with self.db() as db: db.execute('UPDATE ios_notification_devices SET last_decision=?,failures=0,next_attempt=0 WHERE token_hash=? AND uid=?',(decision['id'],digest,uid))
                except Exception:
                    # Never expose the device token, key JWT, or transport exception.
                    failures=row['failures']+1
                    with self.db() as db: db.execute('UPDATE ios_notification_devices SET failures=?,next_attempt=? WHERE token_hash=? AND uid=?',(failures,now+min(3600,15*2**min(failures,8)),digest,uid))
            return sent
        finally: self.lock.release()

    def start(self):
        if self.thread and self.thread.is_alive(): return
        self.stop.clear()
        def run():
            while not self.stop.is_set():
                try: self.process_once()
                except Exception: self.app.logger.warning('An account notification was deferred.')
                self.stop.wait(10)
        self.thread=threading.Thread(target=run,name='vision-account-notifications',daemon=True);self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=15)
        self.thread=None
