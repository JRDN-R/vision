from pathlib import Path

def edit(name, replacements):
    p = Path(name)
    text = p.read_text()
    for old, new in replacements:
        assert old in text, (name, old[:120])
        text = text.replace(old, new, 1)
    p.write_text(text)

edit('vision-pc/server.py', [
 ('from audit_logs import AuditLogs', 'from audit_logs import AuditLogs\nfrom trials import Trials'),
 ('STOP = threading.Event()', 'STOP = threading.Event()\nYOUTUBE_WORKER_LOCK = threading.Lock()'),
 ('    uploaded_media.initialize()\n    return config', "    uploaded_media.initialize()\n    app.config['TRIAL_ENABLED'] = config.get('anonymousTrialEnabled', True) is True\n    app.config['TRIALS'] = trials\n    trials.initialize()\n    return config"),
 ("    expected = app.config.get('CONNECTION_TOKEN', '')", "    if request.path == '/api/trial/start' and request.method == 'POST':\n        return\n    if token.startswith('trial_'):\n        trials.authorize(token)\n        return\n    expected = app.config.get('CONNECTION_TOKEN', '')"),
 ('def cors(response):', '''def cors(response):
    if getattr(g, 'auth_kind', None) == 'trial':
        ident, deadline = g.trial['id'], g.trial['expires_at']
        released = [False]
        def release_trial():
            if not released[0]:
                released[0] = True
                with trials.lock:
                    count = trials.inflight.get(ident, 1) - 1
                    if count > 0:
                        trials.inflight[ident] = count
                    else:
                        trials.inflight.pop(ident, None)
        if time.time() >= deadline:
            response.close()
            response = jsonify(error='Your five-minute trial has ended. Sign in with Google.', code='trial-expired')
            response.status_code = 403
        elif response.is_streamed:
            original = response.response
            def trial_chunks():
                try:
                    for chunk in original:
                        if time.time() >= deadline or trials.expired('trial-' + ident):
                            break
                        yield chunk
                finally:
                    if hasattr(original, 'close'):
                        original.close()
                    release_trial()
            response.response = trial_chunks()
        response.call_on_close(release_trial)'''),
 ("googleSignInRequired=bool(app.config.get('FIREBASE_IDENTITY')))", "googleSignInRequired=bool(app.config.get('FIREBASE_IDENTITY')), anonymousTrial={'enabled': trials.enabled(), 'durationSeconds': 300})"),
 ("'uploadedMedia': video['ready']}", "'uploadedMedia': video['ready'], 'temporarySessions': trials.enabled()}"),
 ("if client_id and g.auth_kind == 'firebase-google':", "if client_id and g.auth_kind in ('firebase-google', 'trial'):"),
 ('def process_next_job():\n', 'def process_next_job():\n    with YOUTUBE_WORKER_LOCK:\n        return _process_next_job()\n\n\ndef _process_next_job():\n'),
 ('    last_write = [0.0]\n', "    trial_id = 'trial-' + value['uid'][6:] if value['uid'].startswith('trial:') else ''\n    deadline = None\n    if trial_id:\n        with connect_db() as db:\n            lease = db.execute('SELECT expires_at FROM trial_visits WHERE id=?', (value['uid'][6:],)).fetchone()\n        deadline = lease['expires_at'] if lease else 0\n    last_write = [0.0]\n"),
 ("    def update(job_id, **fields):\n        result", "    def update(job_id, **fields):\n        if trial_id and trials.expired(trial_id):\n            raise RuntimeError('trial-expired')\n        result"),
 ("        with MEDIA_LOCK:\n            process_job", "        with MEDIA_LOCK:\n            if trial_id and trials.expired(trial_id):\n                with connect_db() as db:\n                    db.execute(\"UPDATE jobs SET status='error',phase='Trial ended' WHERE id=?\", (value['id'],))\n                return True\n            process_job"),
 ("include_sound_events=bool(value['include_sound_events']))", "include_sound_events=bool(value['include_sound_events']),\n                        **({'deadline': deadline} if deadline is not None else {}))"),
 ("    except Exception:\n        update(value['id'], status='error'", "    except Exception:\n        if trial_id and trials.expired(trial_id):\n            with connect_db() as db:\n                db.execute(\"UPDATE jobs SET status='error',phase='Trial ended' WHERE id=?\", (value['id'],))\n        else:\n            update(value['id'], status='error'"),
 ('uploaded_media = UploadedMedia(app, connect_db, APIError, sessions)', 'uploaded_media = UploadedMedia(app, connect_db, APIError, sessions)\ntrials = Trials(app, connect_db, APIError, sessions, transcriptions, uploaded_media, YOUTUBE_WORKER_LOCK)'),
 ('        recover_jobs()\n', '        trials.sweep()\n        recover_jobs()\n        trials.start()\n'),
 ('                  expose_tracebacks=False)', "                  expose_tracebacks=False, trusted_proxy='127.0.0.1',\n                  trusted_proxy_count=1, trusted_proxy_headers={'x-forwarded-for', 'x-forwarded-proto'},\n                  clear_untrusted_proxy_headers=True)"),
 ('        finally:\n            STOP.set()', '        finally:\n            trials.stop.set()\n            STOP.set()')
])
edit('vision-pc/sessions.py', [
 ('        self.wake, self.stop = threading.Event(), threading.Event()', '        self.wake, self.stop = threading.Event(), threading.Event()\n        self.worker_lock = threading.Lock()'),
 ('    def project(self, project_id, create=False, db=None):\n', "    def project(self, project_id, create=False, db=None):\n        if getattr(g, 'auth_kind', '') == 'trial':\n            return self.app.config['TRIALS'].project(project_id)\n        if project_id.startswith('trial-'):\n            raise self.Error('Temporary projects cannot be opened or saved as account projects.', 403)\n"),
 ('    def work_once(self):\n', '    def work_once(self):\n        with self.worker_lock:\n            return self._work_once()\n\n    def _work_once(self):\n'),
 ("        run_id, key = row['id'], ''\n        try:", "        run_id, key = row['id'], ''\n        trial = self.app.config.get('TRIALS')\n        if trial and trial.expired(row['project_id']):\n            row['cancel_requested'] = 1\n            self.update(run_id, cancel_requested=1)\n            if not row['response_id'] or row['status'] == 'saving':\n                self.update(run_id, status='cancelled', phase='Trial ended', key_cipher=None)\n                return True\n        try:"),
 ('                    self.absorb_response(run_id, result)\n                else:', "                    self.absorb_response(run_id, result)\n                    if trial and trial.expired(row['project_id']):\n                        self.update(run_id, status='cancelled', phase='Trial ended', key_cipher=None)\n                        return True\n                else:"),
 ("            if row['response_id'] and (error.status >= 500 or error.status == 429):", "            if trial and trial.expired(row['project_id']):\n                self.update(run_id, status='cancelled', phase='Trial ended; upstream cancellation could not be confirmed', key_cipher=None)\n            elif row['response_id'] and (error.status >= 500 or error.status == 429):")
])
edit('vision-pc/media.py', [
 ('import base64, html, io, json, math, os, re, subprocess, tempfile, threading', 'import base64, html, io, json, math, os, re, subprocess, tempfile, threading, time'),
 ('def process_job(job_id, url, ffmpeg, update, deno=None, temp_root=None, include_sound_events=False):', 'def process_job(job_id, url, ffmpeg, update, deno=None, temp_root=None, include_sound_events=False, deadline=None):\n    def budget(normal):\n        if deadline is None:\n            return normal\n        remaining = deadline - time.time()\n        if remaining <= 0:\n            raise TimeoutError("The trial has ended")\n        return min(normal, remaining)'),
 ('timeout=90, **SUBPROCESS_FLAGS', 'timeout=budget(90), **SUBPROCESS_FLAGS'),
 ('timeout=600, **SUBPROCESS_FLAGS', 'timeout=budget(600), **SUBPROCESS_FLAGS')
])
for module, table in [('uploaded_media', 'uploaded_media'), ('transcription', 'local_transcriptions')]:
    edit('vision-pc/' + module + '.py', [('                value = dict(row)\n', '''                value = dict(row)
                trials = self.app.config.get('TRIALS')
                if trials and trials.expired(value['project_id']):
                    db.execute("UPDATE ''' + table + ''' SET status='cancelled',cancel_requested=1,phase='Trial ended' WHERE id=?", (value['id'],))
                    return True
''')])
edit('vision-pc/Setup-Vision-PC.ps1', [("'server.py','media.py'", "'server.py','trials.py','media.py'")])
