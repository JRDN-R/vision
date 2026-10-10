"""Owner activity and Gemini administration. No credentials are served publicly.

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
TEXT_FIELDS = ('jobType', 'outcome', 'app', 'authProvider')
MODULE_WHERE = {
    'all': '1=1',
    'logins': "event IN ('google_sign_in','account_sign_in','app_first_seen')",
    'transcriptions': """(event IN ('audio_submitted','audio_received','transcription_finished','youtube_submitted')
        OR (event='processing_finished' AND json_extract(CASE WHEN json_valid(details) THEN details ELSE '{}' END,
        '$.jobType') IN ('youtube','transcription','whisper','whisper_sound','local','local_sound','gemini','gemini_sound','sound')))""",
    'vortex': "event LIKE 'vortex_%'",
}


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


def snapshot_payload(connect_db, person='', limit=100, version='', module='all'):
    if person and not re.fullmatch(r'[0-9a-f]{64}', person):
        raise ValueError('Choose a valid person.')
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= PAGE_LIMIT:
        raise ValueError('Choose an event limit from 1 through 2000.')
    if module not in MODULE_WHERE:
        raise ValueError('Choose a valid activity module.')
    db = connect_db()
    try:
        # A consistent, read-only snapshot, separate from the logging writer.
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        providers = dict(db.execute('SELECT uid,provider FROM audit_account_metadata')) if 'audit_account_metadata' in tables else {}
        apps = {}
        if 'audit_user_apps' in tables:
            for row in db.execute('SELECT uid,app,last_seen FROM audit_user_apps ORDER BY app'):
                if row['app'] in ('vision', 'vortex', 'venture'):
                    apps.setdefault(row['uid'], []).append({'app': row['app'], 'lastSeen': row['last_seen']})
        if 'venture_conversations' in tables:
            for row in db.execute('SELECT uid,MAX(updated_at) FROM venture_conversations GROUP BY uid'):
                apps.setdefault(row[0], []).append({'app': 'venture', 'lastSeen': row[1]})
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
            full_name = clean(row['name'])
            parts = full_name.rsplit(' ', 1)
            users.append({'id': key, 'name': full_name,
                'firstName': parts[0] if parts else '',
                'lastName': parts[1] if len(parts) > 1 else '',
                'email': clean(row['email']),
                'provider': providers.get(row['uid'], 'unknown'), 'apps': apps.get(row['uid'], []),
                'firstSeen': row['first_seen'], 'lastSeen': row['last_seen'],
                'signIns': row['sign_in_sightings'], 'eventCount': row['event_count'],
                'lastActivity': row['last_activity'], 'lastEventId': row['last_event_id'],
                'latestEvent': clean(row['latest_event'], 40), 'latestDetails': details(row['latest_details'])})
        if person and selected_uid is None:
            raise LookupError('This person is no longer in the retained activity records.')
        revision = hashlib.sha256(json.dumps([users, person, limit, module], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if version == revision:
            return {'unchanged': True, 'version': revision, 'checkedAt': time.time()}
        where, params = ('WHERE uid=? AND ', [selected_uid]) if person else ('WHERE ', [])
        scope = where
        counts = {name: db.execute('SELECT COUNT(*) FROM audit_events ' + scope + clause, params).fetchone()[0]
                  for name, clause in MODULE_WHERE.items()}
        where += MODULE_WHERE[module]
        event_rows = db.execute('SELECT id,uid,created_at,event,details FROM audit_events ' + where +
                                ' ORDER BY id DESC LIMIT ?', params + [limit]).fetchall()
        events = [{'id': row['id'], 'userId': public_id(row['uid']), 'at': row['created_at'],
                   'kind': clean(row['event'], 40), 'details': details(row['details'])} for row in event_rows]
    finally:
        db.close()
    total = counts[module]
    return {'version': revision, 'checkedAt': time.time(), 'users': users, 'events': events,
        'totalEvents': total, 'hasMore': total > len(events), 'maxEvents': PAGE_LIMIT,
        'retentionPerUser': 2000, 'pollSeconds': 3, 'module': module, 'moduleCounts': counts}


def vortex_payload(connect_db, person='', limit=100):
    """Owner-only operational metadata, including jobs created before this update.

    Explicit columns exclude input URLs, searches, media titles, engine output,
    filenames, tickets and credentials. Never reuse the user-facing snapshot.
    """
    if person and not re.fullmatch(r'[0-9a-f]{64}', person):
        raise ValueError('Choose a valid person.')
    if type(limit) is not int or not 1 <= limit <= PAGE_LIMIT:
        raise ValueError('Choose a job limit from 1 through 2000.')
    db = connect_db()
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vortex_jobs'").fetchone():
            return {'available': False, 'jobs': [], 'totals': None, 'hasMore': False}
        now = time.time()
        rows = db.execute('''SELECT id,uid,kind,quality,status,progress,output_size,options_json,
            created_at,updated_at,completed_at,expires_at FROM vortex_jobs
            WHERE delete_requested=0 ORDER BY created_at DESC,id DESC''')
        totals = dict(jobs=0, downloads=0, inspections=0, queued=0, processing=0,
                      complete=0, error=0, cancelled=0, expired=0, readyDownloads=0, retainedBytes=0)
        jobs, accounts = [], set()
        for row in rows:
            user_id = public_id(row['uid'])
            if person and person != user_id:
                continue
            state = row['status']
            if state == 'complete' and row['expires_at'] is not None and row['expires_at'] <= now:
                state = 'expired'
            totals['jobs'] += 1
            totals['downloads' if row['kind'] == 'download' else 'inspections'] += 1
            if state in ('queued', 'processing', 'complete', 'error', 'cancelled', 'expired'):
                totals[state] += 1
            accounts.add(user_id)
            size = max(0, row['output_size'] or 0) if state == 'complete' else 0
            if row['kind'] == 'download' and state == 'complete':
                totals['readyDownloads'] += 1
                totals['retainedBytes'] += size
            if len(jobs) >= limit:
                continue
            try:
                options = json.loads(row['options_json'] or '{}')
            except (ValueError, TypeError):
                options = {}
            if not isinstance(options, dict):
                options = {}
            mode = options.get('downloadMode', 'video')
            mode = mode if mode in ('video', 'audio') else 'video'
            format_value = options.get('audioFormat' if mode == 'audio' else 'videoFormat', 'm4a' if mode == 'audio' else 'mp4')
            jobs.append({'id': row['id'], 'userId': user_id, 'kind': clean(row['kind'], 16),
                'quality': clean(row['quality'], 16), 'status': clean(state, 16),
                'progress': row['progress'], 'outputBytes': size, 'mode': mode,
                'format': format_value if format_value in ('mp4','mov','m4a','mp3','wav') else None,
                'createdAt': row['created_at'], 'updatedAt': row['updated_at'],
                'completedAt': row['completed_at'], 'expiresAt': row['expires_at']})
        totals['accounts'] = len(accounts)
        return {'available': True, 'checkedAt': now, 'jobs': jobs, 'totals': totals,
                'hasMore': totals['jobs'] > len(jobs), 'retentionDays': 5}
    finally:
        db.close()


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
            value = snapshot_payload(connect_db, request.args.get('user', ''), int(raw_limit),
                                     request.args.get('version', ''), request.args.get('module', 'all'))
        except ValueError as error:
            return respond({'error': str(error)}, 400)
        except LookupError as error:
            return respond({'error': str(error)}, 404)
        return respond(value)

    app.add_url_rule('/api/admin/activity', 'vision_activity', activity, methods=['GET'])

    def owner_access():
        return allowed(app.config, getattr(g, 'uid', ''), getattr(g, 'auth_kind', ''), request.headers.get('Origin'))

    def forbidden():
        return respond({'code': 'ACTIVITY_FORBIDDEN',
                        'error': 'This Google account does not have activity access. Enable it on the Vision PC.'}, 403)

    def vortex_admin():
        if not owner_access():
            return forbidden()
        raw_limit = request.args.get('limit', '100')
        if not re.fullmatch(r'[0-9]{1,4}', raw_limit):
            return respond({'error': 'Choose a job limit from 1 through 2000.'}, 400)
        try:
            return respond(vortex_payload(connect_db, request.args.get('user', ''), int(raw_limit)))
        except ValueError as error:
            return respond({'error': str(error)}, 400)

    app.add_url_rule('/api/admin/vortex', 'vision_vortex_admin', vortex_admin, methods=['GET'])

    def gemini_admin():
        if not owner_access():
            return forbidden()
        access, usage = app.config.get('GEMINI_ACCESS'), app.config.get('GEMINI_USAGE')
        if access is None or usage is None:
            return respond({'error': 'Gemini administration is not available on this processor yet.'}, 503)
        raw_limit = request.args.get('limit', '200')
        if not re.fullmatch(r'[0-9]{1,4}', raw_limit):
            return respond({'error': 'Choose a request limit from 1 through 2000.'}, 400)
        try:
            since = float(request.args['since']) if 'since' in request.args else None
            until = float(request.args['until']) if 'until' in request.args else None
            result = access.snapshot()
            result['usage'] = usage.snapshot(request.args.get('user', ''), int(raw_limit), since, until)
        except (TypeError, ValueError) as error:
            return respond({'error': str(error)}, 400)
        return respond(result)

    def gemini_decision():
        if not owner_access():
            return forbidden()
        access = app.config.get('GEMINI_ACCESS')
        if access is None:
            return respond({'error': 'Gemini administration is not available on this processor yet.'}, 503)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return respond({'error': 'Choose an account and an access decision.'}, 400)
        try:
            result = access.decide(body.get('userId'), body.get('decision'), g.uid)
        except (TypeError, ValueError) as error:
            return respond({'error': str(error)}, 400)
        except LookupError as error:
            return respond({'error': str(error)}, 404)
        return respond(result)

    def gemini_pricing():
        if not owner_access():
            return forbidden()
        usage = app.config.get('GEMINI_USAGE')
        if usage is None:
            return respond({'error': 'Gemini administration is not available on this processor yet.'}, 503)
        if request.method == 'GET':
            return respond({'pricing': usage.pricing()})
        try:
            value = usage.save_pricing(request.get_json(silent=True))
        except (TypeError, ValueError) as error:
            return respond({'error': str(error)}, 400)
        audit = app.config.get('AUDIT_LOGS')
        if audit:
            audit.event(g.uid, 'gemini_pricing_updated', outcome='updated')
        return respond({'pricing': value})

    def gemini_own_access():
        if getattr(g, 'auth_kind', '') != 'firebase-google':
            return respond({'error': 'Sign in with Google to request Gemini access.', 'status': 'denied'}, 403)
        access = app.config.get('GEMINI_ACCESS')
        if access is None:
            return respond({'error': 'Gemini access requests are not available on this processor yet.'}, 503)
        status = access.request_access(g.uid) if request.method == 'POST' else access.status(g.uid)
        return respond({'status': status, 'approved': status == 'approved'})

    app.add_url_rule('/api/admin/gemini', 'vision_gemini_admin', gemini_admin, methods=['GET'])
    app.add_url_rule('/api/admin/gemini/access', 'vision_gemini_decision', gemini_decision, methods=['POST'])
    app.add_url_rule('/api/admin/gemini/pricing', 'vision_gemini_pricing', gemini_pricing, methods=['GET', 'PUT'])
    app.add_url_rule('/api/gemini/access', 'vision_gemini_own_access', gemini_own_access, methods=['GET', 'POST'])

    from account_administration import register as register_account_admin
    register_account_admin(app, connect_db, owner_access, forbidden, respond)
