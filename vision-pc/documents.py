"""Durable, project-owned local document jobs. No remote model/API fallback."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time

from flask import jsonify, request, send_file
from media import MEDIA_LOCK
from sessions import safe_name
from uploaded_media import WindowsJob

UPLOAD_LIMIT = 50 * 1024 * 1024
RESULT_LIMIT = 16 * 1024 * 1024
STORAGE_LIMIT = 4 * 1024 * 1024 * 1024
PROJECT_LIMIT = 512 * 1024 * 1024
RESERVE = 1024 * 1024 * 1024
ACTIVE = ('queued', 'processing')
EXTENSIONS = set('.pdf .docx .pptx .xlsx .xlsm .xls .doc .ppt .rtf .odt .ods .odp .epub .eml .msg .txt .md .csv .tsv .json .xml .html .htm .log .yaml .yml .ipynb .zip .png .jpg .jpeg .webp .bmp .tif .tiff .gif'.split())


class DocumentJobs:
    def __init__(self, app, db, error, sessions):
        self.app, self.db, self.Error, self.sessions = app, db, error, sessions
        self.stop, self.wake = threading.Event(), threading.Event()
        self.upload_lock, self.worker_lock = threading.Lock(), threading.Lock()
        self.settings = {}
        self.register_routes()

    @property
    def root(self):
        return self.app.config['DATA_DIR'] / 'documents'

    def initialize(self, config):
        self.settings = config.get('documentProcessing') or {}
        self.root.mkdir(exist_ok=True, mode=0o700)
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS document_jobs (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, request_id TEXT NOT NULL,
                content_hash TEXT NOT NULL, source_name TEXT NOT NULL, source_size INTEGER NOT NULL,
                status TEXT NOT NULL, phase TEXT NOT NULL, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                output_size INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE(project_id,request_id))''')

    def capability(self):
        python = Path(self.settings.get('pythonPath') or '__not_installed__')
        ready = self.settings.get('enabled') is True and python.is_file()
        return dict(ready=ready, maxUploadBytes=UPLOAD_LIMIT, maxResultBytes=RESULT_LIMIT,
                    extensions=sorted(EXTENSIONS), maxPages=100, retentionDays=30,
                    profile=self.settings.get('profile', 'none'),
                    tools=self.settings.get('tools', []), storageLimitBytes=STORAGE_LIMIT)

    def row(self, job_id, project_id=None):
        if not re.fullmatch(r'[0-9a-f]{24}', job_id):
            raise self.Error('This document job is unavailable.', 404)
        with self.db() as db:
            row = db.execute('SELECT * FROM document_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None or (project_id is not None and row['project_id'] != project_id):
            raise self.Error('This document job is unavailable.', 404)
        return dict(row)

    @staticmethod
    def snapshot(row):
        return dict(id=row['id'], status=row['status'], phase=row['phase'], error=row['error'],
                    requestId=row['request_id'], sourceName=row['source_name'],
                    bytes=row['output_size'], sourceSha256=row['content_hash'])

    def update(self, job_id, **fields):
        if set(fields) - {'status', 'phase', 'error', 'output_size', 'cancel_requested'}:
            raise ValueError('Invalid document update')
        fields['updated_at'] = time.time()
        with self.db() as db:
            db.execute('UPDATE document_jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?', [*fields.values(), job_id])

    def capacity(self, db, project_id):
        rows = db.execute('SELECT project_id,status,output_size FROM document_jobs').fetchall()
        if sum(row['status'] in ACTIVE for row in rows) >= 8:
            raise self.Error('Document queue is full. Vision will retry shortly.', 429)
        weight = lambda row: UPLOAD_LIMIT + RESULT_LIMIT if row['status'] in ACTIVE else row['output_size']
        if sum(weight(r) for r in rows) + UPLOAD_LIMIT + RESULT_LIMIT > STORAGE_LIMIT:
            raise self.Error('Document cache is full. Remove prepared files from Projects → Document processing.', 507)
        if sum(weight(r) for r in rows if r['project_id'] == project_id) + UPLOAD_LIMIT + RESULT_LIMIT > PROJECT_LIMIT:
            raise self.Error('This project’s document cache is full. Remove prepared files from Projects → Document processing.', 507)

    def register_routes(self):
        app = self.app

        @app.post('/api/projects/<project_id>/documents')
        def upload_document(project_id):
            self.sessions.project(project_id)
            request.max_content_length = UPLOAD_LIMIT + 1024 * 1024
            if not self.capability()['ready']:
                raise self.Error('Run Enable-Vision-Documents.ps1 on FUPCJ Server to enable document processing.', 503)
            request_id = request.form.get('requestId', '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', request_id):
                raise self.Error('A valid requestId is required.')
            uploads = request.files.getlist('file')
            if len(uploads) != 1 or sum(len(request.files.getlist(k)) for k in request.files) != 1:
                raise self.Error('Upload one document at a time.')
            upload = uploads[0]
            name = safe_name(upload.filename)
            if Path(name).suffix.lower() not in EXTENSIONS:
                raise self.Error('This file type is retained as an original attachment; automatic extraction is not supported.', 415)
            with self.upload_lock, tempfile.TemporaryDirectory(prefix='incoming-', dir=self.root) as temporary:
                self.cleanup()
                with self.db() as db:
                    old = db.execute('SELECT * FROM document_jobs WHERE project_id=? AND request_id=?', (project_id, request_id)).fetchone()
                    if old is None:
                        self.capacity(db, project_id)
                if shutil.disk_usage(self.root).free < RESERVE + UPLOAD_LIMIT:
                    raise self.Error('FUPCJ Server needs at least 1 GB of free working space.', 507)
                source = Path(temporary) / 'source'
                size, digest = 0, hashlib.sha256()
                with source.open('wb') as target:
                    while chunk := upload.stream.read(65536):
                        size += len(chunk)
                        if size > UPLOAD_LIMIT:
                            raise self.Error('Automatic document processing supports files up to 50 MB.', 413)
                        digest.update(chunk)
                        target.write(chunk)
                if not size:
                    raise self.Error('The document is empty.')
                if old is not None:
                    if old['content_hash'] != digest.hexdigest():
                        raise self.Error('This request belongs to a different file.', 409)
                    return jsonify(self.snapshot(dict(old))), 202
                # Reuse completed identical content only within this same project.
                with self.db() as db:
                    cached = db.execute("SELECT * FROM document_jobs WHERE project_id=? AND content_hash=? AND source_name=? AND status='complete' AND cancel_requested=0 ORDER BY created_at DESC LIMIT 1", (project_id, digest.hexdigest(), name)).fetchone()
                if cached and (self.root / cached['id'] / 'result.json').is_file():
                    self.update(cached['id'], phase='Prepared content reused')
                    return jsonify(self.snapshot(dict(cached))), 202
                job_id, now = secrets.token_hex(12), time.time()
                directory = self.root / job_id
                directory.mkdir(mode=0o700)
                try:
                    os.replace(source, directory / 'source')
                    with self.db() as db:
                        db.execute('''INSERT INTO document_jobs
                            (id,project_id,request_id,content_hash,source_name,source_size,status,phase,created_at,updated_at)
                            VALUES(?,?,?,?,?,?,'queued','Waiting for document processor',?,?)''',
                            (job_id, project_id, request_id, digest.hexdigest(), name, size, now, now))
                except Exception:
                    shutil.rmtree(directory, ignore_errors=True)
                    raise
            self.wake.set()
            return jsonify(self.snapshot(self.row(job_id))), 202

        @app.get('/api/projects/<project_id>/documents/request/<request_id>')
        def document_receipt(project_id, request_id):
            self.sessions.project(project_id)
            with self.db() as db:
                row = db.execute('SELECT * FROM document_jobs WHERE project_id=? AND request_id=?', (project_id, request_id)).fetchone()
            if row is None:
                raise self.Error('This upload has not been accepted.', 404)
            return jsonify(self.snapshot(dict(row)))

        @app.get('/api/projects/<project_id>/documents')
        def document_inventory(project_id):
            self.sessions.project(project_id)
            with self.db() as db:
                rows = db.execute('SELECT * FROM document_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 500', (project_id,)).fetchall()
            return jsonify(items=[self.snapshot(dict(r)) for r in rows])

        @app.route('/api/projects/<project_id>/documents/<job_id>', methods=['GET', 'DELETE'])
        def document_status(project_id, job_id):
            self.sessions.project(project_id)
            value = self.row(job_id, project_id)
            if request.method == 'DELETE':
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    current = db.execute('SELECT status FROM document_jobs WHERE id=?', (job_id,)).fetchone()
                    db.execute('UPDATE document_jobs SET cancel_requested=1 WHERE id=?', (job_id,))
                    if current['status'] != 'processing':
                        db.execute("UPDATE document_jobs SET status='cancelled',phase='Removed',output_size=0 WHERE id=?", (job_id,))
                if current['status'] != 'processing':
                    shutil.rmtree(self.root / job_id, ignore_errors=True)
                value = self.row(job_id)
            return jsonify(self.snapshot(value))

        @app.get('/api/projects/<project_id>/documents/<job_id>/result')
        def document_result(project_id, job_id):
            self.sessions.project(project_id)
            value = self.row(job_id, project_id)
            if value['cancel_requested'] or value['status'] != 'complete':
                raise self.Error('Prepared content is not available. Retry extraction from the original file.', 409)
            path = self.root / job_id / 'result.json'
            if not path.is_file():
                raise self.Error('Prepared content expired. Retry extraction from the original file.', 410)
            return send_file(path, mimetype='application/json', conditional=True)

    def cleanup(self):
        with self.db() as db:
            rows = db.execute("SELECT id FROM document_jobs WHERE status NOT IN ('queued','processing') AND updated_at<?", (time.time()-30*86400,)).fetchall()
            for row in rows:
                shutil.rmtree(self.root / row['id'], ignore_errors=True)
                db.execute('DELETE FROM document_jobs WHERE id=?', (row['id'],))

    def recover(self):
        with self.db() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM document_jobs')]
        known = {r['id'] for r in rows}
        for directory in self.root.iterdir():
            if directory.is_dir() and directory.name not in known:
                shutil.rmtree(directory, ignore_errors=True)
        for row in rows:
            directory = self.root / row['id']
            if row['cancel_requested']:
                shutil.rmtree(directory, ignore_errors=True)
                self.update(row['id'], status='cancelled', phase='Removed', output_size=0)
            elif row['status'] in ACTIVE:
                if row['attempts'] >= 3 or not (directory / 'source').is_file():
                    self.update(row['id'], status='error', phase='Stopped', error='Extraction was interrupted repeatedly. Retry this file.')
                    shutil.rmtree(directory, ignore_errors=True)
                else:
                    self.update(row['id'], status='queued', phase='Resuming after restart')
            elif row['status'] == 'complete':
                (directory / 'source').unlink(missing_ok=True)
        self.cleanup()

    def process(self, value):
        directory = self.root / value['id']
        options = {k: self.settings.get(k) for k in ('tesseractPath', 'javaPath', 'tikaPath', 'artifactsPath', 'profile')}
        (directory / 'options.json').write_text(json.dumps(options), encoding='utf-8')
        command = [self.settings['pythonPath'], str(Path(__file__).with_name('document_worker.py')),
                   '--source', str(directory/'source'), '--name', value['source_name'],
                   '--output', str(directory/'result.json'), '--options', str(directory/'options.json')]
        env = dict(os.environ, OMP_NUM_THREADS='2', MKL_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2',
                   HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS} if os.name == 'nt' else {}
        self.update(value['id'], phase='Extracting text, tables and images on FUPCJ Server')
        process, guard = None, None
        try:
            with (directory/'worker.log').open('wb') as log:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, **flags)
                guard = WindowsJob(process)
                deadline = time.monotonic() + 1800
                while process.poll() is None:
                    if self.stop.is_set() or self.row(value['id'])['cancel_requested']:
                        raise InterruptedError()
                    if time.monotonic() > deadline:
                        raise RuntimeError('Extraction reached its 30-minute limit. Try a smaller file.')
                    if shutil.disk_usage(directory).free < RESERVE or sum(p.stat().st_size for p in directory.rglob('*') if p.is_file()) > 256*1024*1024:
                        raise RuntimeError('Extraction reached its working-storage limit. Try a smaller file.')
                    self.stop.wait(.5)
                if process.returncode:
                    raise RuntimeError('The file could not be extracted. It may be encrypted, damaged, or unsupported. The original is kept in your project.')
            path = directory/'result.json'
            if not path.is_file() or path.stat().st_size > RESULT_LIMIT:
                raise RuntimeError('Prepared content exceeds its 16 MB limit. Split the source into smaller files.')
            result = json.loads(path.read_text(encoding='utf-8'))
            if result.get('schema') != 'vision-document-v1' or not isinstance(result.get('artifacts'), list):
                raise RuntimeError('The document worker returned an invalid result.')
            result['sourceSha256'] = value['content_hash']
            path.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        finally:
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            if guard:
                guard.close()

    def work_once(self):
        if not self.capability()['ready'] or not self.worker_lock.acquire(False):
            return False
        locked = False
        try:
            locked = MEDIA_LOCK.acquire(False)
            if not locked:
                return False
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT * FROM document_jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
                if row is None:
                    return False
                value = dict(row)
                db.execute("UPDATE document_jobs SET status='processing',attempts=attempts+1 WHERE id=?", (value['id'],))
            directory, started = self.root / value['id'], time.monotonic()
            try:
                self.process(value)
                with self.db() as db:
                    done = db.execute("UPDATE document_jobs SET status='complete',phase='Prepared for AI',output_size=?,updated_at=? WHERE id=? AND cancel_requested=0", ((directory/'result.json').stat().st_size, time.time(), value['id'])).rowcount
                if not done:
                    raise InterruptedError()
                for p in directory.iterdir():
                    if p.name != 'result.json':
                        if p.is_dir(): shutil.rmtree(p, ignore_errors=True)
                        else: p.unlink(missing_ok=True)
            except InterruptedError:
                if self.row(value['id'])['cancel_requested']:
                    shutil.rmtree(directory, ignore_errors=True)
                    self.update(value['id'], status='cancelled', phase='Removed', output_size=0)
                else:
                    self.update(value['id'], status='queued', phase='Waiting for restart')
            except Exception as error:
                message = str(error) if isinstance(error, RuntimeError) else 'Extraction failed. Retry this file.'
                shutil.rmtree(directory, ignore_errors=True)
                self.update(value['id'], status='error', phase='Stopped', error=message, output_size=0)
            self.app.config['AUDIT_LOGS'].project_event(value['project_id'], 'processing_finished', jobType='document',
                outcome=self.row(value['id'])['status'], processingWallSeconds=time.monotonic()-started,
                uploadedBytes=value['source_size'], outputBytes=self.row(value['id'])['output_size'])
            return True
        finally:
            if locked: MEDIA_LOCK.release()
            self.worker_lock.release()

    def start(self):
        self.stop.clear()
        self.recover()
        def loop():
            while not self.stop.is_set():
                try: worked = self.work_once()
                except Exception:
                    self.app.logger.error('Document worker paused after a processing error.')
                    worked = False
                if not worked:
                    self.wake.wait(3)
                    self.wake.clear()
        threading.Thread(target=loop, name='vision-documents', daemon=True).start()
