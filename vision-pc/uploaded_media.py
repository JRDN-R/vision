"""Project-owned uploaded-video jobs; bounded, local-only CPU processing.

Original uploads are temporary. A compact playable copy, timestamped frames and
15-minute MP3 sections survive browser disconnects and Windows restarts. There
are no provider API calls. Media artifacts remain until the owner deletes them.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
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

from media import MEDIA_LOCK, data_url, snapshot_interval, timestamp
from sessions import safe_name

UPLOAD_LIMIT = 5 * 1024 * 1024 * 1024
BODY_LIMIT = UPLOAD_LIMIT + 1024 * 1024
UPLOAD_CHUNK_BYTES = 16 * 1024 * 1024
UPLOAD_SESSION_TTL = 24 * 3600
PREVIEW_LIMIT = 128 * 1024 * 1024
RESULT_LIMIT = 20 * 1024 * 1024
AUDIO_LIMIT = 4 * 1024 * 1024
AUDIO_SECTION_SECONDS = 900
MAX_DURATION = 7200
MAX_QUEUED = 3
PROJECT_LIMIT = 16 * 1024 * 1024 * 1024
STORAGE_LIMIT = 20 * 1024 * 1024 * 1024
DISK_RESERVE = 1024 * 1024 * 1024
# Reserve the worst case while a worker is still producing its outputs.
JOB_RESERVE = UPLOAD_LIMIT + PREVIEW_LIMIT + RESULT_LIMIT + 8 * AUDIO_LIMIT
FORMATS = 'mov,matroska,webm,avi,mpeg,mpegts,ogg'
EXTENSIONS = {'.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi', '.mpeg', '.mpg', '.m2ts', '.mts', '.ogv', '.3gp'}
ACTIVE = ('queued', 'processing')
TERMINAL = ('complete', 'error', 'cancelled')


class MediaFailure(Exception):
    pass


class MediaStopped(Exception):
    pass


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as target:
        json.dump(value, target, separators=(',', ':'))
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


class WindowsJob:
    """Kill the FFmpeg child if the Windows server task is forcibly stopped."""
    def __init__(self, process):
        self.handle = None
        if os.name != 'nt':
            return
        import ctypes
        from ctypes import wintypes
        class BasicLimits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                        ('active', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                        ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
        class IOCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', BasicLimits), ('io', IOCounters), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                or not self.kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle)))):
            self.close()
            raise MediaFailure('Windows could not isolate the video worker. Restart FUPCJ Server processor and retry.')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class UploadedMedia:
    def __init__(self, app, connect_db, api_error, sessions):
        self.app, self.db, self.Error, self.sessions = app, connect_db, api_error, sessions
        self.stop, self.wake = threading.Event(), threading.Event()
        self.upload_lock, self.worker_lock = threading.Lock(), threading.Lock()
        self.register_routes()

    @property
    def root(self):
        return self.app.config['DATA_DIR'] / 'uploaded-media'

    def initialize(self):
        self.root.mkdir(exist_ok=True, mode=0o700)
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS uploaded_media (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, request_id TEXT NOT NULL,
                content_hash TEXT NOT NULL, source_name TEXT NOT NULL, source_size INTEGER NOT NULL,
                status TEXT NOT NULL, phase TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
                error TEXT, attempts INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
                output_size INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(project_id,request_id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS uploaded_media_uploads (
                project_id TEXT NOT NULL, request_id TEXT NOT NULL, source_name TEXT NOT NULL,
                source_size INTEGER NOT NULL, received_size INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY(project_id,request_id))''')

    def capability(self):
        ffmpeg = Path(self.app.config['FFMPEG'])
        ready = ffmpeg.is_file() and ffmpeg.with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe').is_file()
        return dict(ready=ready, maxUploadBytes=UPLOAD_LIMIT, maxDuration=MAX_DURATION,
                    maxPreviewBytes=PREVIEW_LIMIT, maxResultBytes=RESULT_LIMIT,
                    resumableUpload=True, uploadChunkBytes=UPLOAD_CHUNK_BYTES)

    def row(self, job_id, project_id=None):
        if not re.fullmatch(r'[0-9a-f]{24}', job_id):
            raise self.Error('This video is not part of the project.', 404)
        with self.db() as db:
            row = db.execute('SELECT * FROM uploaded_media WHERE id=?', (job_id,)).fetchone()
        if row is None or (project_id is not None and row['project_id'] != project_id):
            raise self.Error('This video is not part of the project.', 404)
        return dict(row)

    def snapshot(self, value):
        result = {key: value[key] for key in ('id', 'status', 'phase', 'progress', 'error')}
        result.update(sourceName=value['source_name'], requestId=value['request_id'], resultReady=value['status'] == 'complete',
                      cancelRequested=bool(value['cancel_requested']))
        thumbnail = self.root / value['id'] / 'thumbnail.jpg'
        try:
            if thumbnail.is_file() and thumbnail.stat().st_size <= 512 * 1024:
                result['thumbnail'] = data_url(thumbnail.read_bytes(), 'image/jpeg')
        except FileNotFoundError:
            pass
        return result

    def update(self, job_id, **fields):
        allowed = {'status', 'phase', 'progress', 'error', 'attempts', 'cancel_requested', 'output_size'}
        if set(fields) - allowed:
            raise ValueError('Invalid media update')
        fields['updated_at'] = time.time()
        with self.db() as db:
            db.execute('UPDATE uploaded_media SET ' + ','.join(key+'=?' for key in fields) + ' WHERE id=?', [*fields.values(), job_id])

    @property
    def upload_root(self):
        return self.root / '.uploads'

    def upload_directory(self, project_id, request_id):
        key = hashlib.sha256((project_id + '\0' + request_id).encode('utf-8')).hexdigest()
        return self.upload_root / key

    def sweep_uploads(self):
        """Remove stale/orphaned partial uploads and reconcile bytes after a restart."""
        cutoff = time.time() - UPLOAD_SESSION_TTL
        with self.db() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM uploaded_media_uploads')]
            for row in rows:
                if row['updated_at'] < cutoff:
                    db.execute('DELETE FROM uploaded_media_uploads WHERE project_id=? AND request_id=?',
                               (row['project_id'], row['request_id']))
        active = []
        for row in rows:
            directory = self.upload_directory(row['project_id'], row['request_id'])
            if row['updated_at'] < cutoff:
                shutil.rmtree(directory, ignore_errors=True)
                continue
            source = directory / 'source.part'
            try:
                actual = source.stat().st_size
            except FileNotFoundError:
                with self.db() as db:
                    db.execute('DELETE FROM uploaded_media_uploads WHERE project_id=? AND request_id=?',
                               (row['project_id'], row['request_id']))
                shutil.rmtree(directory, ignore_errors=True)
                continue
            if actual < 0 or actual > row['source_size']:
                with self.db() as db:
                    db.execute('DELETE FROM uploaded_media_uploads WHERE project_id=? AND request_id=?',
                               (row['project_id'], row['request_id']))
                shutil.rmtree(directory, ignore_errors=True)
                continue
            if actual != row['received_size']:
                with self.db() as db:
                    db.execute('UPDATE uploaded_media_uploads SET received_size=?,updated_at=? WHERE project_id=? AND request_id=?',
                               (actual, time.time(), row['project_id'], row['request_id']))
            active.append(directory.name)
        if self.upload_root.is_dir():
            keep = set(active)
            for directory in self.upload_root.iterdir():
                if directory.is_dir() and directory.name not in keep:
                    shutil.rmtree(directory, ignore_errors=True)
            try:
                self.upload_root.rmdir()
            except OSError:
                pass

    def finalize_upload(self, project_id, request_id, upload, source):
        digest = hashlib.sha256()
        with source.open('rb') as incoming:
            while chunk := incoming.read(8 * 1024 * 1024):
                digest.update(chunk)
        job_id, now = secrets.token_hex(12), time.time()
        directory = self.root / job_id
        directory.mkdir(mode=0o700)
        target = directory / 'source.input'
        moved = False
        try:
            os.replace(source, target)
            moved = True
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                existing = db.execute('SELECT * FROM uploaded_media WHERE project_id=? AND request_id=?',
                                      (project_id, request_id)).fetchone()
                if existing is not None:
                    if existing['content_hash'] != digest.hexdigest():
                        raise self.Error('This requestId belongs to another video. Start a new import.', 409)
                    db.execute('DELETE FROM uploaded_media_uploads WHERE project_id=? AND request_id=?',
                               (project_id, request_id))
                    shutil.rmtree(directory, ignore_errors=True)
                    shutil.rmtree(self.upload_directory(project_id, request_id), ignore_errors=True)
                    return dict(existing)
                db.execute('''INSERT INTO uploaded_media
                    (id,project_id,request_id,content_hash,source_name,source_size,status,phase,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,'queued','Waiting for FUPCJ Server video processor',?,?)''',
                    (job_id, project_id, request_id, digest.hexdigest(), upload['source_name'],
                     upload['source_size'], now, now))
                db.execute('DELETE FROM uploaded_media_uploads WHERE project_id=? AND request_id=?',
                           (project_id, request_id))
        except Exception:
            if moved and target.exists():
                partial = self.upload_directory(project_id, request_id)
                partial.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.replace(target, partial / 'source.part')
            shutil.rmtree(directory, ignore_errors=True)
            raise
        shutil.rmtree(self.upload_directory(project_id, request_id), ignore_errors=True)
        self.wake.set()
        return self.row(job_id, project_id)

    def register_routes(self):
        app = self.app
        @app.post('/api/projects/<project_id>/media/upload/<request_id>')
        def begin_video_upload(project_id, request_id):
            self.sessions.project(project_id)
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', request_id):
                raise self.Error('A valid requestId is required.')
            if not self.capability()['ready']:
                raise self.Error('Update FUPCJ Server processor to install its video tools.', 503)
            payload = request.get_json(silent=True) or {}
            name = safe_name(payload.get('name', ''))
            raw_size = payload.get('size')
            if isinstance(raw_size, bool) or not isinstance(raw_size, int) or raw_size <= 0:
                raise self.Error('A valid video size is required.')
            size = int(raw_size)
            if size > UPLOAD_LIMIT:
                raise self.Error('Video uploads exceed 5 GB. Choose a smaller video.', 413)
            if Path(name).suffix.lower() not in EXTENSIONS:
                raise self.Error('Choose a video file such as MP4, MOV, WebM or MKV.')
            with self.upload_lock:
                self.sweep_uploads()
                with self.db() as db:
                    accepted = db.execute('SELECT * FROM uploaded_media WHERE project_id=? AND request_id=?',
                                          (project_id, request_id)).fetchone()
                    if accepted is not None:
                        result = self.snapshot(dict(accepted))
                        result.update(accepted=True, receivedBytes=accepted['source_size'],
                                      totalBytes=accepted['source_size'], chunkBytes=UPLOAD_CHUNK_BYTES)
                        return jsonify(result)
                    current = db.execute('SELECT * FROM uploaded_media_uploads WHERE project_id=? AND request_id=?',
                                         (project_id, request_id)).fetchone()
                    if current is not None and (current['source_name'] != name or current['source_size'] != size):
                        raise self.Error('This upload belongs to a different video. Start a new import.', 409)
                    if current is None:
                        self.check_capacity(db, project_id)
                        now = time.time()
                        db.execute('''INSERT INTO uploaded_media_uploads
                            (project_id,request_id,source_name,source_size,received_size,created_at,updated_at)
                            VALUES(?,?,?,?,0,?,?)''', (project_id, request_id, name, size, now, now))
                        current = db.execute('SELECT * FROM uploaded_media_uploads WHERE project_id=? AND request_id=?',
                                             (project_id, request_id)).fetchone()
                directory = self.upload_directory(project_id, request_id)
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                source = directory / 'source.part'
                source.touch(exist_ok=True)
                received = source.stat().st_size
                if received > size:
                    with self.db() as db:
                        db.execute('DELETE FROM uploaded_media_uploads WHERE project_id=? AND request_id=?',
                                   (project_id, request_id))
                    shutil.rmtree(directory, ignore_errors=True)
                    raise self.Error('The saved partial upload is invalid. Start a new import.', 409)
                if received != current['received_size']:
                    with self.db() as db:
                        db.execute('UPDATE uploaded_media_uploads SET received_size=?,updated_at=? WHERE project_id=? AND request_id=?',
                                   (received, time.time(), project_id, request_id))
                remaining = size - received
                reserve = DISK_RESERVE + remaining + PREVIEW_LIMIT + RESULT_LIMIT + 8 * AUDIO_LIMIT
                if shutil.disk_usage(self.root).free < reserve:
                    needed = max(1, math.ceil(reserve / (1024 ** 3)))
                    raise self.Error(f'Free at least {needed} GB on FUPCJ Server to finish this video upload.', 507)
                return jsonify(accepted=False, requestId=request_id, sourceName=name, receivedBytes=received,
                               totalBytes=size, chunkBytes=UPLOAD_CHUNK_BYTES), 202

        @app.put('/api/projects/<project_id>/media/upload/<request_id>')
        def upload_video_chunk(project_id, request_id):
            self.sessions.project(project_id)
            request.max_content_length = UPLOAD_CHUNK_BYTES + 1024
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', request_id):
                raise self.Error('A valid requestId is required.')
            try:
                offset = int(request.args.get('offset', ''))
            except ValueError:
                raise self.Error('A valid upload offset is required.')
            if offset < 0:
                raise self.Error('A valid upload offset is required.')
            length = request.content_length
            if length is None or length <= 0 or length > UPLOAD_CHUNK_BYTES:
                raise self.Error('The video upload chunk is invalid.', 400)
            with self.upload_lock:
                with self.db() as db:
                    accepted = db.execute('SELECT * FROM uploaded_media WHERE project_id=? AND request_id=?',
                                          (project_id, request_id)).fetchone()
                    if accepted is not None:
                        result = self.snapshot(dict(accepted))
                        result.update(accepted=True, receivedBytes=accepted['source_size'],
                                      totalBytes=accepted['source_size'], chunkBytes=UPLOAD_CHUNK_BYTES)
                        return jsonify(result)
                    row = db.execute('SELECT * FROM uploaded_media_uploads WHERE project_id=? AND request_id=?',
                                     (project_id, request_id)).fetchone()
                if row is None:
                    raise self.Error('This partial video upload is unavailable. Start it again.', 404)
                upload = dict(row)
                source = self.upload_directory(project_id, request_id) / 'source.part'
                try:
                    received = source.stat().st_size
                except FileNotFoundError:
                    raise self.Error('This partial video upload is unavailable. Start it again.', 404)
                if offset != received:
                    raise self.Error(f'Upload offset changed; resume at byte {received}.', 409, 'VISION_UPLOAD_OFFSET')
                if received + length > upload['source_size']:
                    raise self.Error('The video upload chunk exceeds the declared file size.', 400)
                remaining = upload['source_size'] - received
                reserve = DISK_RESERVE + remaining + PREVIEW_LIMIT + RESULT_LIMIT + 8 * AUDIO_LIMIT
                if shutil.disk_usage(self.root).free < reserve:
                    needed = max(1, math.ceil(reserve / (1024 ** 3)))
                    raise self.Error(f'Free at least {needed} GB on FUPCJ Server to finish this video upload.', 507)
                written = 0
                try:
                    with source.open('ab') as target:
                        while written < length:
                            chunk = request.stream.read(min(1024 * 1024, length - written))
                            if not chunk:
                                break
                            target.write(chunk)
                            written += len(chunk)
                        target.flush()
                        os.fsync(target.fileno())
                    if written != length:
                        with source.open('r+b') as target:
                            target.truncate(received)
                            target.flush()
                            os.fsync(target.fileno())
                        raise self.Error('The video upload ended before the chunk was complete.', 400)
                except Exception:
                    if source.exists() and source.stat().st_size > received:
                        with source.open('r+b') as target:
                            target.truncate(received)
                    raise
                received += written
                with self.db() as db:
                    db.execute('UPDATE uploaded_media_uploads SET received_size=?,updated_at=? WHERE project_id=? AND request_id=?',
                               (received, time.time(), project_id, request_id))
                if received == upload['source_size']:
                    accepted = self.finalize_upload(project_id, request_id, upload, source)
                    result = self.snapshot(accepted)
                    result.update(accepted=True, receivedBytes=received, totalBytes=received,
                                  chunkBytes=UPLOAD_CHUNK_BYTES)
                    return jsonify(result), 202
                return jsonify(accepted=False, requestId=request_id, receivedBytes=received,
                               totalBytes=upload['source_size'], chunkBytes=UPLOAD_CHUNK_BYTES), 202

        @app.post('/api/projects/<project_id>/media')
        def upload_video(project_id):
            self.sessions.project(project_id)
            request.max_content_length = BODY_LIMIT
            if not self.capability()['ready']:
                raise self.Error('Update FUPCJ Server processor to install its video tools.', 503)
            request_id = request.form.get('requestId', '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', request_id):
                raise self.Error('A valid requestId is required.')
            uploads = request.files.getlist('file')
            if len(uploads) != 1 or sum(len(request.files.getlist(key)) for key in request.files) != 1:
                raise self.Error('Upload one video file per module.')
            upload = uploads[0]
            name = safe_name(upload.filename)
            if Path(name).suffix.lower() not in EXTENSIONS:
                raise self.Error('Choose a video file such as MP4, MOV, WebM or MKV.')
            # Do not keep unregistered source files after a failed request. Only
            # one inbound source is written at a time, with explicit disk bounds.
            with self.upload_lock, tempfile.TemporaryDirectory(prefix='incoming-', dir=self.root) as temporary:
                with self.db() as db:
                    old = db.execute('SELECT * FROM uploaded_media WHERE project_id=? AND request_id=?', (project_id, request_id)).fetchone()
                    if old is None:
                        self.check_capacity(db, project_id)
                if shutil.disk_usage(self.root).free < DISK_RESERVE + UPLOAD_LIMIT:
                    raise self.Error('Free at least 6 GB on FUPCJ Server before uploading a video.', 507)
                source = Path(temporary) / 'source.input'
                size, digest = 0, hashlib.sha256()
                with source.open('wb') as target:
                    while chunk := upload.stream.read(65536):
                        size += len(chunk)
                        if size > UPLOAD_LIMIT:
                            raise self.Error('Video uploads exceed 5 GB. Choose a smaller video.', 413)
                        digest.update(chunk)
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                if not size:
                    raise self.Error('The video file is empty.')
                if old is not None:
                    if old['content_hash'] != digest.hexdigest():
                        raise self.Error('This requestId belongs to another video. Start a new import.', 409)
                    return jsonify(self.snapshot(dict(old))), 202
                job_id, now = secrets.token_hex(12), time.time()
                directory = self.root / job_id
                directory.mkdir(mode=0o700)
                try:
                    os.replace(source, directory / 'source.input')
                    with self.db() as db:
                        db.execute('''INSERT INTO uploaded_media
                            (id,project_id,request_id,content_hash,source_name,source_size,status,phase,created_at,updated_at)
                            VALUES(?,?,?,?,?,?,'queued','Waiting for FUPCJ Server video processor',?,?)''',
                            (job_id, project_id, request_id, digest.hexdigest(), name, size, now, now))
                except Exception:
                    shutil.rmtree(directory, ignore_errors=True)
                    raise
            self.wake.set()
            return jsonify(self.snapshot(self.row(job_id, project_id))), 202

        @app.get('/api/projects/<project_id>/media/request/<request_id>')
        def video_request_receipt(project_id, request_id):
            self.sessions.project(project_id)
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', request_id):
                raise self.Error('This video request is unavailable.', 404)
            with self.db() as db:
                row = db.execute('SELECT * FROM uploaded_media WHERE project_id=? AND request_id=?', (project_id, request_id)).fetchone()
            if row is None:
                raise self.Error('FUPCJ Server has not accepted this video request.', 404)
            return jsonify(self.snapshot(dict(row)))

        @app.get('/api/projects/<project_id>/media')
        def video_inventory(project_id):
            self.sessions.project(project_id)
            cursor = request.args.get('cursor')
            clause, arguments = '', [project_id]
            if cursor:
                prior = self.row(cursor, project_id)
                clause = ' AND (created_at < ? OR (created_at = ? AND id < ?))'
                arguments += [prior['created_at'], prior['created_at'], prior['id']]
            with self.db() as db:
                rows = db.execute('SELECT * FROM uploaded_media WHERE project_id=?'+clause+' ORDER BY created_at DESC,id DESC LIMIT 51', arguments).fetchall()
            items = [dict(id=row['id'], requestId=row['request_id'], sourceName=row['source_name'],
                          status=row['status'], phase=row['phase'], progress=row['progress'], error=row['error'],
                          createdAt=row['created_at'], cancelRequested=bool(row['cancel_requested']),
                          bytes=row['source_size'] if row['status'] in ACTIVE else row['output_size']) for row in rows[:50]]
            return jsonify(items=items, nextCursor=rows[49]['id'] if len(rows)>50 else None)

        @app.route('/api/projects/<project_id>/media/<job_id>', methods=['GET', 'DELETE'])
        def video_status(project_id, job_id):
            self.sessions.project(project_id)
            value = self.row(job_id, project_id)
            if request.method == 'DELETE':
                # Synchronize deletion with the worker's completion checkpoint.
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    current = db.execute('SELECT status FROM uploaded_media WHERE id=?', (job_id,)).fetchone()
                    processing = current['status'] == 'processing'
                    db.execute('UPDATE uploaded_media SET cancel_requested=1,updated_at=? WHERE id=?', (time.time(), job_id))
                    if not processing:
                        db.execute("UPDATE uploaded_media SET status='cancelled',phase='Removed',output_size=0 WHERE id=?", (job_id,))
                if not processing:
                    shutil.rmtree(self.root / job_id, ignore_errors=True)
                self.wake.set()
                value = self.row(job_id, project_id)
            return jsonify(self.snapshot(value))

        @app.get('/api/projects/<project_id>/media/<job_id>/result')
        def video_result(project_id, job_id):
            value = self.authorized_result(project_id, job_id)
            return send_file(self.root / value['id'] / 'result.json', mimetype='application/json', conditional=True)

        @app.get('/api/projects/<project_id>/media/<job_id>/preview')
        def video_preview(project_id, job_id):
            value = self.authorized_result(project_id, job_id)
            return send_file(self.root / value['id'] / 'preview.mp4', mimetype='video/mp4', conditional=True)

        @app.get('/api/projects/<project_id>/media/<job_id>/thumbnail')
        def video_thumbnail(project_id, job_id):
            self.sessions.project(project_id)
            value = self.row(job_id, project_id)
            path = self.root / value['id'] / 'thumbnail.jpg'
            if not path.is_file():
                raise self.Error('The first frame is not ready yet.', 409)
            return send_file(path, mimetype='image/jpeg', conditional=True)

        @app.get('/api/projects/<project_id>/media/<job_id>/audio/<int:index>')
        def video_audio(project_id, job_id, index):
            value = self.authorized_result(project_id, job_id)
            if not 0 <= index < 8:
                raise self.Error('This audio section is unavailable.', 404)
            path = self.root / value['id'] / f'audio-{index:03d}.mp3'
            if not path.is_file():
                raise self.Error('This audio section is unavailable.', 404)
            return send_file(path, mimetype='audio/mpeg', conditional=True)

    def authorized_result(self, project_id, job_id):
        self.sessions.project(project_id)
        value = self.row(job_id, project_id)
        if value['status'] != 'complete' or value['cancel_requested']:
            raise self.Error('This video result is not available.', 410 if value['status'] in TERMINAL else 409)
        return value

    def check_capacity(self, db, project_id):
        rows = db.execute('SELECT project_id,status,output_size FROM uploaded_media').fetchall()
        uploads = db.execute('SELECT project_id FROM uploaded_media_uploads').fetchall()
        if sum(row['status'] in ACTIVE for row in rows) + len(uploads) >= MAX_QUEUED:
            raise self.Error('FUPCJ Server video queue is full. Wait for a video to finish.', 429)
        def weight(row):
            return JOB_RESERVE if row['status'] in ACTIVE else row['output_size']
        reserved = len(uploads) * JOB_RESERVE
        if sum(weight(row) for row in rows) + reserved + JOB_RESERVE > STORAGE_LIMIT:
            raise self.Error('FUPCJ Server video storage is full. Remove saved video previews before adding more.', 507)
        project_reserved = sum(row['project_id'] == project_id for row in uploads) * JOB_RESERVE
        if sum(weight(row) for row in rows if row['project_id'] == project_id) + project_reserved + JOB_RESERVE > PROJECT_LIMIT:
            raise self.Error('This project has reached its video storage limit. Remove a saved preview before adding more.', 507)

    def recover(self):
        """Startup only: resume accepted jobs and preserve recent resumable uploads."""
        with self.upload_lock:
            self.sweep_uploads()
        with self.db() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM uploaded_media')]
        known = {row['id'] for row in rows}
        for directory in self.root.iterdir():
            if directory.is_dir() and directory.name != '.uploads' and directory.name not in known:
                shutil.rmtree(directory, ignore_errors=True)
        for row in rows:
            directory = self.root / row['id']
            if row['cancel_requested']:
                shutil.rmtree(directory, ignore_errors=True)
                self.update(row['id'], status='cancelled', phase='Removed', output_size=0)
            elif row['status'] in ACTIVE:
                if row['attempts'] >= 3 or not (directory / 'source.input').is_file():
                    self.update(row['id'], status='error', phase='Stopped', error='Video processing was interrupted. Add the video again to retry.')
                    shutil.rmtree(directory, ignore_errors=True)
                else:
                    for path in directory.iterdir():
                        if path.name != 'source.input':
                            path.unlink(missing_ok=True)
                    self.update(row['id'], status='queued', phase='Resuming video after FUPCJ Server restart', progress=0)
            elif row['status'] == 'complete':
                (directory / 'source.input').unlink(missing_ok=True)
            else:
                shutil.rmtree(directory, ignore_errors=True)

    def work_once(self):
        if not self.worker_lock.acquire(blocking=False):
            return False
        locked = False
        try:
            # Uploaded videos and YouTube extraction share the same CPU slot.
            locked = MEDIA_LOCK.acquire(blocking=False)
            if not locked:
                return False
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT * FROM uploaded_media WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
                if row is None:
                    return False
                value = dict(row)
                trials = self.app.config.get('TRIALS')
                if trials and trials.expired(value['project_id']):
                    db.execute("UPDATE uploaded_media SET status='cancelled',cancel_requested=1,phase='Trial ended' WHERE id=?", (value['id'],))
                    return True
                db.execute("UPDATE uploaded_media SET status='processing',phase='Reading video',attempts=attempts+1,updated_at=? WHERE id=?", (time.time(), value['id']))
            directory = self.root / value['id']
            processing_started = time.monotonic()
            try:
                self.process(value)
                size = sum(path.stat().st_size for path in directory.iterdir() if path.is_file() and path.name != 'source.input')
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    completed = db.execute("UPDATE uploaded_media SET status='complete',phase='Video ready',progress=100,output_size=?,updated_at=? WHERE id=? AND cancel_requested=0",
                                           (size, time.time(), value['id'])).rowcount
                if not completed:
                    raise MediaStopped()
                (directory / 'source.input').unlink(missing_ok=True)
            except MediaStopped:
                if self.row(value['id'])['cancel_requested']:
                    shutil.rmtree(directory, ignore_errors=True)
                    self.update(value['id'], status='cancelled', phase='Removed', output_size=0)
                else:
                    self.update(value['id'], status='queued', phase='Waiting for FUPCJ Server restart')
            except Exception as error:
                self.app.logger.error('Uploaded video %s failed (%s).', value['id'], type(error).__name__)
                message = str(error) if isinstance(error, MediaFailure) else 'FUPCJ Server could not process this video. Try a smaller MP4 or MOV file.'
                shutil.rmtree(directory, ignore_errors=True)
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    current = db.execute('SELECT cancel_requested FROM uploaded_media WHERE id=?', (value['id'],)).fetchone()
                    cancelled = bool(current['cancel_requested'])
                    db.execute('UPDATE uploaded_media SET status=?,phase=?,error=?,output_size=0,updated_at=? WHERE id=?',
                               ('cancelled' if cancelled else 'error', 'Removed' if cancelled else 'Stopped',
                               None if cancelled else message, time.time(), value['id']))
            finally:
                current = self.row(value['id'])
                self.app.config['AUDIT_LOGS'].project_event(value['project_id'], 'processing_finished', jobType='video',
                    outcome=current['status'], processingWallSeconds=time.monotonic()-processing_started,
                    uploadedBytes=value['source_size'], outputBytes=current['output_size'])
            return True
        finally:
            if locked:
                MEDIA_LOCK.release()
            self.worker_lock.release()

    def start(self):
        self.stop.clear()
        self.recover()
        def loop():
            while not self.stop.is_set():
                try:
                    worked = self.work_once()
                except Exception:
                    self.app.logger.error('Uploaded video worker paused after a database error.')
                    worked = False
                if not worked:
                    self.wake.wait(2)
                    self.wake.clear()
        threading.Thread(target=loop, name='vision-uploaded-video', daemon=True).start()

    def process(self, value):
        from PIL import Image, ImageDraw, ImageFont
        directory, job_id = self.root / value['id'], value['id']
        source = directory / 'source.input'
        ffmpeg = self.app.config['FFMPEG']
        ffprobe = str(Path(ffmpeg).with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe'))
        started = time.monotonic()
        deadline = started + 4 * 3600
        def alive():
            if self.stop.is_set() or self.row(job_id)['cancel_requested']:
                raise MediaStopped()
            if time.monotonic() > deadline:
                raise MediaFailure('Video processing took too long. Try a shorter video.')
            if shutil.disk_usage(directory).free < DISK_RESERVE:
                raise MediaFailure('Free at least 1 GB on FUPCJ Server, then add the video again.')

        def command(args, timeout=120, limits=(), phase=None, start_progress=0, span=0, duration=0):
            """No shell/network protocols; bounded files and cancellable children."""
            stdout_path, stderr_path = directory / 'command.out', directory / 'command.err'
            progress_path = directory / 'ffmpeg-progress.txt'
            progress_path.unlink(missing_ok=True)
            if phase:
                self.update(job_id, phase=phase, progress=start_progress)
            flags = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS} if os.name == 'nt' else {}
            process, guard = None, None
            try:
                with stdout_path.open('wb') as stdout, stderr_path.open('wb') as stderr:
                    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, **flags)
                    guard = WindowsJob(process)
                    until = time.monotonic() + timeout
                    while process.poll() is None:
                        alive()
                        if time.monotonic() > until:
                            raise MediaFailure('A video processing step took too long. Try a shorter video.')
                        for path, maximum in [(stdout_path, 1024*1024), (stderr_path, 65536), *limits]:
                            if path.is_file() and path.stat().st_size > maximum:
                                raise MediaFailure('This video exceeds the workspace preview limits. Try a smaller video.')
                        if duration and progress_path.is_file():
                            raw = progress_path.read_bytes()[-4096:].decode('utf-8', errors='ignore')
                            matches = re.findall(r'out_time_us=(\d+)', raw)
                            if matches:
                                fraction = min(.99, int(matches[-1]) / 1000000 / duration)
                                self.update(job_id, progress=round(start_progress + span * fraction, 1))
                        self.stop.wait(.25)
                    if process.returncode:
                        raise MediaFailure('FUPCJ Server could not decode this video. Try a standard MP4 or MOV file.')
                    alive()
                for path, maximum in [(stdout_path, 1024*1024), (stderr_path, 65536), *limits]:
                    if path.is_file() and path.stat().st_size > maximum:
                        raise MediaFailure('This video exceeds the workspace preview limits. Try a smaller video.')
                return stdout_path.read_bytes()
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                if guard is not None:
                    guard.close()
                for path in (stdout_path, stderr_path, progress_path):
                    path.unlink(missing_ok=True)

        read_flags = ['-protocol_whitelist', 'file,pipe', '-format_whitelist', FORMATS]
        def probe(path):
            raw = command([ffprobe, '-v', 'error', *read_flags, '-show_entries',
                           'format=duration:stream=codec_type,width,height,duration', '-of', 'json', str(path)], timeout=30)
            try:
                return json.loads(raw)
            except ValueError:
                raise MediaFailure('FUPCJ Server could not read the video details.')
        details = probe(source)
        streams = details.get('streams', [])
        video = next((s for s in streams if s.get('codec_type') == 'video'), None)
        audio = any(s.get('codec_type') == 'audio' for s in streams)
        try:
            duration = float(details.get('format', {}).get('duration') or (video or {}).get('duration'))
            width, height = int(video['width']), int(video['height'])
            if not math.isfinite(duration) or not 0 < duration <= MAX_DURATION:
                raise ValueError()
            if not 0 < width <= 8192 or not 0 < height <= 8192 or width * height > 33554432:
                raise ValueError()
        except (TypeError, ValueError, KeyError):
            raise MediaFailure('Choose a video up to two hours long with a standard frame size.')
        deadline = started + min(4 * 3600, max(900, duration * 2 + 600))
        common = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                  '-threads', '2', '-filter_threads', '1', '-filter_complex_threads', '1']
        def frame(when, output):
            command([*common, '-ss', str(when), *read_flags, '-i', str(source), '-map', '0:v:0',
                     '-frames:v', '1', '-vf', "scale='min(640,iw)':'min(640,ih)':force_original_aspect_ratio=decrease",
                     '-threads', '2', '-q:v', '6', '-f', 'image2', str(output)], limits=[(output, 512*1024)], timeout=60)
        first = directory / 'first.jpg'
        frame(0, first)
        os.replace(first, directory / 'thumbnail.jpg')
        self.update(job_id, phase='Creating a small playable preview', progress=4)
        preview = directory / 'preview.mp4'
        target_kbps = min(192, max(48, int((PREVIEW_LIMIT - 2*1024*1024)*8 / duration / 1000) - 32))
        command([*common, *read_flags, '-i', str(source), '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn',
                 '-vf', "scale='min(480,iw)':'min(480,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=15",
                 '-c:v', 'libx264', '-preset', 'veryfast', '-threads', '2', '-b:v', f'{target_kbps}k',
                 '-maxrate', f'{target_kbps*2}k', '-bufsize', f'{target_kbps*4}k', '-pix_fmt', 'yuv420p',
                 '-c:a', 'aac', '-ac', '1', '-ar', '22050', '-b:a', '24k', '-t', str(duration),
                 '-movflags', '+faststart', '-fs', str(PREVIEW_LIMIT), '-progress', str(directory/'ffmpeg-progress.txt'),
                 str(preview)], timeout=max(600, duration*2), limits=[(preview, PREVIEW_LIMIT)],
                phase='Creating a small playable preview', start_progress=4, span=62, duration=duration)
        preview_details = probe(preview)
        if float(preview_details.get('format', {}).get('duration') or 0) < duration - max(.3, min(2, duration * .005)):
            raise MediaFailure('The video is too large for a complete workspace preview. Try a shorter video.')
        preview_video = next(s for s in preview_details['streams'] if s.get('codec_type') == 'video')
        interval, frames = snapshot_interval(duration), []
        count = math.ceil(duration / interval)
        font = ImageFont.load_default(size=16)
        for index in range(count):
            alive()
            when = index * interval
            self.update(job_id, phase=f'Snapshot {index+1} of {count}', progress=66 + 22*index/count)
            image_path = directory / 'snapshot.jpg'
            if index == 0:
                image_path.write_bytes((directory / 'thumbnail.jpg').read_bytes())
            else:
                frame(when, image_path)
            with Image.open(image_path) as picture:
                picture = picture.convert('RGB')
                stamped = Image.new('RGB', (picture.width, picture.height+28), 'black')
                stamped.paste(picture, (0, 0))
                ImageDraw.Draw(stamped).text((picture.width/2, picture.height+14), timestamp(when),
                                           font=font, fill='white', anchor='mm')
                output = io.BytesIO()
                stamped.save(output, format='JPEG', quality=68, optimize=True)
                frames.append(dict(name=f'frame-{index+1:04d}-{timestamp(when).replace(":", "-")}.jpg',
                                   timestamp=when, mime='image/jpeg', data=data_url(output.getvalue(), 'image/jpeg')))
            image_path.unlink(missing_ok=True)
            if sum(len(item['data']) for item in frames) > RESULT_LIMIT - 1024*1024:
                raise MediaFailure('The video snapshots exceed the project limit. Try a shorter video.')
        base = f'/api/projects/{value["project_id"]}/media/{job_id}'
        audio_sections = []
        if audio:
            for index in range(math.ceil(duration / AUDIO_SECTION_SECONDS)):
                when, end = index*AUDIO_SECTION_SECONDS, min(duration, (index+1)*AUDIO_SECTION_SECONDS)
                path = directory / f'audio-{index:03d}.mp3'
                # Decode the audio timeline from its beginning before slicing.
                # Input-side seeking can discard a delayed audio stream's offset;
                # padding also keeps later video sections valid after audio ends.
                command([*common, *read_flags, '-i', str(source), '-map', '0:a:0',
                         '-af', 'aresample=async=1:first_pts=0,apad', '-ss', str(when),
                         '-t', str(end-when), '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame',
                         '-threads', '1', '-b:a', '32k', '-f', 'mp3', str(path)], timeout=600,
                        limits=[(path, AUDIO_LIMIT)], phase=f'Preparing audio section {index+1}', start_progress=88+10*when/duration)
                audio_sections.append(dict(start=when, end=end, name=path.name, mime='audio/mpeg', url=base+f'/audio/{index}'))
        result = dict(title=Path(value['source_name']).stem, duration=duration, snapshotInterval=interval,
                      thumbnail=data_url((directory/'thumbnail.jpg').read_bytes(), 'image/jpeg'), frames=frames,
                      audioSections=audio_sections,
                      preview=dict(url=base+'/preview', mime='video/mp4', size=preview.stat().st_size,
                                   width=preview_video['width'], height=preview_video['height']))
        encoded_size = len(json.dumps(result, separators=(',', ':')).encode('utf-8'))
        if encoded_size > RESULT_LIMIT:
            raise MediaFailure('The video snapshots exceed the project limit. Try a shorter video.')
        atomic_json(directory / 'result.json', result)
        alive()
