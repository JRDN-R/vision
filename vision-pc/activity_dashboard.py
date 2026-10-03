"""Read-only owner activity API. No log files or credentials are served publicly.

Enable-Vision-Activity.ps1 creates DATA_DIR/activity-admins.json locally. Only
explicit Firebase UIDs in that protected file can read the dashboard. A Google
login alone, an email/name match, and the installation token never grant access.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import time

PAGE_LIMIT = 2000
NUMBER_FIELDS = ('requestBytes', 'uploadedBytes', 'outputBytes', 'requestWallSeconds',
                 'processingWallSeconds', 'runElapsedSeconds', 'httpStatus')
TEXT_FIELDS = ('jobType', 'outcome')


def public_id(uid):
    return hashlib.sha256(uid.encode('utf-8')).hexdigest()


def clean(value, limit=200):
    return re.sub(r'[\x00-\x1f\x7f-\x9f\u2028-\u202e\u2066-\u2069]', ' ', str(value or ''))[:limit].strip()


def details(raw):
    """Re-apply the audit allowlist; never return arbitrary DB fields."""
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in NUMBER_FIELDS:
        item = value.get(key)
        if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item) and item >= 0:
            result[key] = round(item, 3)
    for key in TEXT_FIELDS:
        item = value.get(key)
        if isinstance(item, str) and re.fullmatch(r'[a-z_]{1,32}', item):
            result[key] = item
    return result


def allowed(config, uid, auth_kind, origin=None):
    if auth_kind != 'firebase-google' or not str(uid).startswith('firebase:'):
        return False
    origins = {'https://jrdn-r.github.io', config.get('BACKEND_URL', '')}
    if origin and origin not in origins:
        return False
    try:
        path = Path(config['DATA_DIR']) / 'activity-admins.json'
        if path.is_symlink() or path.stat().st_size > 65536:
            return False
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        uids = value.get('uids') if isinstance(value, dict) else None
        if not isinstance(uids, list) or not 1 <= len(uids) <= 100:
            return False
        if any(not isinstance(uid, str) or not uid.startswith('firebase:') or not 9 < len(uid) <= 137 for uid in uids):
            return False
        return uid in uids
    except (OSError, ValueError, KeyError, TypeError):
        return False


def snapshot_payload(connect_db, person='', limit=100, version=''):
    if person and not re.fullmatch(r'[0-9a-f]{64}', person):
        raise ValueError('Choose a valid person.')
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= PAGE_LIMIT:
        raise ValueError('Choose an event limit from 1 through 2000.')
    db = connect_db()
    try:
        # A consistent, read-only snapshot, separate from the logging writer.
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        rows = db.execute('''SELECT u.*, COALESCE(a.n,0) AS event_count,
            COALESCE(a.last_id,0) AS last_event_id, a.last_at AS last_activity,
            e.event AS latest_event, e.details AS latest_details
            FROM audit_users u LEFT JOIN
            (SELECT uid,COUNT(*) AS n,MAX(id) AS last_id,MAX(created_at) AS last_at
             FROM audit_events GROUP BY uid) a ON a.uid=u.uid
            LEFT JOIN audit_events e ON e.id=a.last_id
            ORDER BY MAX(u.last_seen,COALESCE(a.last_at,0)) DESC,u.name,u.email,u.uid''').fetchall()
        users, selected_uid = [], None
        for row in rows:
            key = public_id(row['uid'])
            if key == person:
                selected_uid = row['uid']
            users.append({'id': key, 'name': clean(row['name']), 'email': clean(row['email']),
                'firstSeen': row['first_seen'], 'lastSeen': row['last_seen'],
                'signIns': row['sign_in_sightings'], 'eventCount': row['event_count'],
                'lastActivity': row['last_activity'], 'lastEventId': row['last_event_id'],
                'latestEvent': clean(row['latest_event'], 40), 'latestDetails': details(row['latest_details'])})
        if person and selected_uid is None:
            raise LookupError('This person is no longer in the retained activity records.')
        revision = hashlib.sha256(json.dumps([users, person, limit], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if version == revision:
            return {'unchanged': True, 'version': revision, 'checkedAt': time.time()}
        where, params = ('WHERE uid=?', [selected_uid]) if person else ('', [])
        event_rows = db.execute('SELECT id,uid,created_at,event,details FROM audit_events ' + where +
                                ' ORDER BY id DESC LIMIT ?', params + [limit]).fetchall()
        events = [{'id': row['id'], 'userId': public_id(row['uid']), 'at': row['created_at'],
                   'kind': clean(row['event'], 40), 'details': details(row['details'])} for row in event_rows]
    finally:
        db.close()
    total = sum(u['eventCount'] for u in users if not person or u['id'] == person)
    return {'version': revision, 'checkedAt': time.time(), 'users': users, 'events': events,
        'totalEvents': total, 'hasMore': total > len(events), 'maxEvents': PAGE_LIMIT,
        'retentionPerUser': 2000, 'pollSeconds': 3}


def register(app, connect_db):
    """Register after the existing global Firebase authorization hook."""
    from flask import g, jsonify, request
    if 'vision_activity' in app.view_functions:
        return

    def respond(value, status=200):
        response = jsonify(value)
        response.status_code = status
        response.headers['Cache-Control'] = 'private, no-store, max-age=0'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def activity():
        if not allowed(app.config, getattr(g, 'uid', ''), getattr(g, 'auth_kind', ''), request.headers.get('Origin')):
            return respond({'code': 'ACTIVITY_FORBIDDEN', 'error': 'This Google account does not have activity access. Enable it on the Vision PC.'}, 403)
        raw_limit = request.args.get('limit', '100')
        if not re.fullmatch(r'[0-9]{1,4}', raw_limit):
            return respond({'error': 'Choose an event limit from 1 through 2000.'}, 400)
        try:
            value = snapshot_payload(connect_db, request.args.get('user', ''), int(raw_limit), request.args.get('version', ''))
        except ValueError as error:
            return respond({'error': str(error)}, 400)
        except LookupError as error:
            return respond({'error': str(error)}, 404)
        return respond(value)

    app.add_url_rule('/api/admin/activity', 'vision_activity', activity, methods=['GET'])
