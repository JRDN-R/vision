"""Account-scoped startup preferences and a paged conversation Sources index.

No new file storage: downloads use existing authorized artifact/upload routes.
"""
import json
import math
from pathlib import Path
import time
from flask import jsonify, request


class Workspace:
    def __init__(self, venture):
        self.v = venture
        self.register_routes()

    def initialize(self):
        with self.v.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS venture_workspace_preferences (
                uid TEXT PRIMARY KEY, launch_view TEXT NOT NULL DEFAULT 'vision',
                swipe_notice_version INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL)''')

    def register_routes(self):
        v = self.v

        @v.app.route('/api/venture/workspace-preferences', methods=['GET', 'PATCH'])
        def workspace_preferences():
            uid = v.account()
            if request.method == 'PATCH':
                request.max_content_length = 2048
                body = request.get_json(silent=True)
                if not isinstance(body, dict) or set(body) - {'launchView', 'swipeNoticeVersion'}:
                    raise v.Error('Invalid workspace preferences.')
                if 'launchView' in body and body['launchView'] not in ('vision', 'venture'):
                    raise v.Error('Choose Vision or Venture as the startup workspace.')
                if 'swipeNoticeVersion' in body and (type(body['swipeNoticeVersion']) is not int or body['swipeNoticeVersion'] != 1):
                    raise v.Error('Invalid navigation notice version.')
                with v.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute('INSERT OR IGNORE INTO venture_workspace_preferences(uid,updated_at) VALUES(?,?)', (uid, time.time()))
                    if 'launchView' in body:
                        db.execute('UPDATE venture_workspace_preferences SET launch_view=?,updated_at=? WHERE uid=?', (body['launchView'], time.time(), uid))
                    if 'swipeNoticeVersion' in body:
                        db.execute('UPDATE venture_workspace_preferences SET swipe_notice_version=1,updated_at=? WHERE uid=?', (time.time(), uid))
            with v.db() as db:
                row = db.execute('SELECT * FROM venture_workspace_preferences WHERE uid=?', (uid,)).fetchone()
            return jsonify(launchView=row['launch_view'] if row else 'vision',
                           swipeNoticeVersion=row['swipe_notice_version'] if row else 0,
                           sourcesV1=True)

        @v.app.get('/api/venture/conversations/<cid>/sources')
        def conversation_sources(cid):
            uid = v.account()
            cursor = request.args.get('before')
            params = [cid, cid]
            where = ''
            if cursor:
                try:
                    when, key = json.loads(cursor)
                    when = float(when)
                    if not math.isfinite(when) or not isinstance(key, str) or len(key) > 300:
                        raise ValueError()
                except (ValueError, TypeError, OverflowError):
                    raise v.Error('Invalid Sources cursor.') from None
                where = 'WHERE (created_at < ? OR (created_at = ? AND source_id < ?))'
                params += [when, when, key]
            # Union before limiting: includes older runs, generated versions and
            # original inputs, independent of the chat's currently loaded turns.
            query = '''WITH sources AS (
                SELECT 'a-' || a.id AS source_id, 'artifact' AS kind, a.id AS artifact_id,
                    r.id AS run_id, NULL AS input_index, a.name, a.mime, a.size, a.path,
                    a.error, r.created_at
                FROM run_artifacts a JOIN project_runs r ON r.id=a.run_id WHERE r.project_id=?
                UNION ALL
                SELECT 'u-' || r.id || '-' || j.key, 'upload', NULL, r.id, j.key,
                    json_extract(j.value,'$.name'), json_extract(j.value,'$.mime'),
                    json_extract(j.value,'$.size'), json_extract(j.value,'$.path'), NULL, r.created_at
                FROM project_runs r, json_each(r.inputs_json) j WHERE r.project_id=?
            ) SELECT * FROM sources ''' + where + ' ORDER BY created_at DESC, source_id DESC LIMIT 101'
            with v.db() as db:
                db.execute('BEGIN')
                v.lookup(cid, uid, db)  # Rechecked in the same read transaction.
                rows = db.execute(query, params).fetchall()
            more = len(rows) > 100
            rows = rows[:100]
            files = []
            for row in rows:
                value = dict(row)
                files.append(dict(id=value['source_id'], kind=value['kind'], runId=value['run_id'],
                    artifactId=value['artifact_id'], inputIndex=value['input_index'],
                    name=value['name'], mime=value['mime'], size=value['size'],
                    ready=bool(value['path'] and Path(value['path']).is_file()),
                    error=value['error'], createdAt=value['created_at']))
            return jsonify(conversationId=cid, files=files,
                           nextCursor=json.dumps([rows[-1]['created_at'], rows[-1]['source_id']]) if more else None)
