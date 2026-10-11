"""Opt-in, owner-scoped retention for acquired Vortex imports.

This is the first durability slice, not a complete evidence pipeline. Bytes live
outside Vortex/results/scratch. SQLite receipts and non-expiring source leases
survive a crashed copy. Publishing uses a same-volume staging rename, even when
the acquired source is on another volume. No age-based garbage collection exists.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import time

ID = re.compile(r'[0-9a-f]{24}\Z')
COPY_CHUNK = 1024 * 1024
MAX_RESULT_BYTES = 32 * 1024 * 1024
DEFAULT_RESERVE = 2 * 1024**3
DEFAULT_OWNER_LIMIT = 20 * 1024**3
DEFAULT_TOTAL_LIMIT = 100 * 1024**3


def owner_key(uid):
    return hashlib.sha256(uid.encode('utf-8')).hexdigest()


def reject_link(path):
    """Reject POSIX symlinks and Windows junctions/reparse points, including dirs."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('An asset path requires administrator recovery.')


def sync_directory(path):
    # Windows os.fsync flushes file data; directory fsync is not exposed by os.
    # Power-loss/NTFS validation remains a real-host deployment gate.
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@contextmanager
def exclusive_file(path):
    """OS-released lock across processes; a DB timeout never steals a live copy."""
    reject_link(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'r+b', buffering=0) as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError('The asset lock is not a regular file.')
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'\0')
        handle.seek(0)
        acquired = False
        try:
            if os.name == 'nt':
                import msvcrt
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as error:
                    # The Windows CRT reports a busy byte-range lock as EACCES,
                    # unlike flock's BlockingIOError. Keep contention distinct
                    # from genuine filesystem/disk failures in the caller.
                    if error.errno in (errno.EACCES, errno.EAGAIN):
                        raise BlockingIOError(errno.EAGAIN, 'Another worker owns the asset lock.') from None
                    raise
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class OwnedAssets:
    def __init__(self, app, connect_db, api_error):
        self.app, self.db, self.Error = app, connect_db, api_error
        self.enabled = False
        self.reserve = DEFAULT_RESERVE
        self.owner_limit = DEFAULT_OWNER_LIMIT
        self.total_limit = DEFAULT_TOTAL_LIMIT

    @property
    def root(self):
        # DATA_DIR is administrator-controlled. Descendants may never redirect it.
        return Path(self.app.config['DATA_DIR']).resolve() / 'owned-assets'

    def initialize(self, config=None):
        options = (config or {}).get('durableAssets', {})
        if options is None:
            options = {}
        if not isinstance(options, dict) or type(options.get('enabled', False)) is not bool:
            raise ValueError('durableAssets must contain a boolean enabled setting.')
        self.enabled = options.get('enabled', False)
        for key, attr, default in (('diskReserveBytes', 'reserve', DEFAULT_RESERVE),
                                   ('ownerLimitBytes', 'owner_limit', DEFAULT_OWNER_LIMIT),
                                   ('totalLimitBytes', 'total_limit', DEFAULT_TOTAL_LIMIT)):
            value = options.get(key, default)
            if type(value) is not int or value < 0 or (key != 'diskReserveBytes' and value == 0):
                raise ValueError('Choose nonnegative, integral durable asset storage budgets.')
            setattr(self, attr, value)
        self._directory()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS owned_assets (
                    id TEXT PRIMARY KEY, uid TEXT NOT NULL, acquisition_key TEXT NOT NULL,
                    project_id TEXT NOT NULL, role TEXT NOT NULL,
                    state TEXT NOT NULL, byte_count INTEGER NOT NULL, sha256 TEXT,
                    filename TEXT NOT NULL, mime TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    UNIQUE(uid,acquisition_key));
                CREATE INDEX IF NOT EXISTS owned_assets_owner ON owned_assets(uid,created_at,id);
                CREATE TABLE IF NOT EXISTS owned_import_receipts (
                    job_id TEXT PRIMARY KEY, uid TEXT NOT NULL, project_id TEXT NOT NULL,
                    source_asset_id TEXT, result_asset_id TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS owned_source_leases (
                    vortex_id TEXT NOT NULL, import_id TEXT NOT NULL, uid TEXT NOT NULL,
                    created_at REAL NOT NULL, PRIMARY KEY(vortex_id,import_id));
                CREATE INDEX IF NOT EXISTS owned_source_import ON owned_source_leases(import_id);
            ''')

    def _directory(self, uid=None):
        root = self.root
        reject_link(root)
        root.mkdir(exist_ok=True, mode=0o700)
        if uid is None:
            return root
        path = root / owner_key(uid)
        reject_link(path)
        path.mkdir(exist_ok=True, mode=0o700)
        return path

    def _path(self, row, suffix='.blob'):
        if not ID.fullmatch(row['id']):
            raise self.Error('The retained asset is unavailable.', 404)
        path = self._directory(row['uid']) / (row['id'] + suffix)
        reject_link(path)
        return path

    def _account_active(self, db, uid):
        if not isinstance(uid, str) or not uid.startswith('firebase:') or len(uid) > 200:
            raise self.Error('Sign in to read retained assets.', 403)
        if db.execute('SELECT 1 FROM account_deletions WHERE id=?', (owner_key(uid),)).fetchone():
            raise self.Error('This Vision account has been removed.', 403, 'account-removed')

    def lease_vortex(self, db, vortex_id, import_id, uid):
        """Call INSIDE enqueue's write transaction, before its durable acceptance."""
        self._account_active(db, uid)
        job = db.execute("SELECT uid,source_kind,import_options FROM jobs WHERE id=?", (import_id,)).fetchone()
        if (not job or job['uid'] != uid or job['source_kind'] != 'vortex'
                or json.loads(job['import_options']).get('assetOwnership') != 1):
            raise self.Error('This import cannot retain a Vortex source.', 409)
        source = db.execute('SELECT uid,purpose,status,delete_requested FROM vortex_jobs WHERE id=?', (vortex_id,)).fetchone()
        if (not source or source['uid'] != uid or source['purpose'] != 'vision' or source['delete_requested']
                or source['status'] not in ('queued', 'processing', 'complete')):
            raise self.Error('This Vortex source is unavailable.', 404)
        db.execute('INSERT OR IGNORE INTO owned_source_leases VALUES(?,?,?,?)',
                   (vortex_id, import_id, uid, time.time()))

    def source_leased(self, db, vortex_id):
        return db.execute('SELECT 1 FROM owned_source_leases WHERE vortex_id=?', (vortex_id,)).fetchone() is not None

    def row(self, asset_id, uid):
        if not isinstance(asset_id, str) or not ID.fullmatch(asset_id):
            raise self.Error('This retained asset is unavailable.', 404)
        with self.db() as db:
            self._account_active(db, uid)
            row = db.execute('SELECT * FROM owned_assets WHERE id=? AND uid=?', (asset_id, uid)).fetchone()
        if not row:
            raise self.Error('This retained asset is unavailable.', 404)
        return dict(row)

    def snapshot(self, row):
        return dict(id=row['id'], state=row['state'], size=row['byte_count'], sha256=row['sha256'],
                    name=row['filename'], mime=row['mime'], role=row['role'],
                    provenance=json.loads(row['metadata_json']), createdAt=row['created_at'],
                    fileURL='/api/owned-assets/' + row['id'] + '/file' if row['state'] == 'ready' else None)

    def ready_path(self, row):
        path = self._path(row)
        if row['state'] != 'ready' or not path.is_file() or path.stat().st_size != row['byte_count']:
            raise self.Error('The retained asset needs administrator recovery. No replacement was downloaded.', 410)
        return path

    def import_asset(self, uid, import_id, role):
        column = {'source': 'source_asset_id', 'result': 'result_asset_id'}[role]
        with self.db() as db:
            self._account_active(db, uid)
            receipt = db.execute('SELECT ' + column + ' FROM owned_import_receipts WHERE job_id=? AND uid=?',
                                 (import_id, uid)).fetchone()
        return self.row(receipt[0], uid) if receipt and receipt[0] else None

    def _boundary(self, stage):
        """Fault-injection seam: tests interrupt after each durable boundary."""

    def _reserve_asset(self, uid, import_id, project_id, role, size, filename, mime, metadata):
        if type(size) is not int or not 0 < size <= (2 * 1024**3 if role == 'source' else MAX_RESULT_BYTES):
            raise self.Error('The retained import exceeds the supported size.', 413)
        encoded = json.dumps(metadata, ensure_ascii=False, separators=(',', ':'))
        if len(encoded.encode('utf-8')) > 65536:
            raise self.Error('The retained source metadata is too large.', 413)
        self._directory(uid)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._account_active(db, uid)
            job = db.execute('SELECT uid,import_options FROM jobs WHERE id=?', (import_id,)).fetchone()
            if (not job or job['uid'] != uid or json.loads(job['import_options']).get('projectId') != project_id
                    or json.loads(job['import_options']).get('assetOwnership') != 1):
                raise self.Error('This retained import is unavailable.', 404)
            key = import_id + ':' + role
            old = db.execute('SELECT * FROM owned_assets WHERE uid=? AND acquisition_key=?', (uid, key)).fetchone()
            if old:
                if old['byte_count'] != size or old['project_id'] != project_id:
                    raise self.Error('The import source changed; administrator recovery is required.', 409)
                return dict(old)
            # A new source requires a still-owned destination. A result may finish
            # into its existing owner's recovery receipt after a project is removed.
            project = db.execute('SELECT owner_uid FROM projects WHERE id=?', (project_id,)).fetchone()
            prior = db.execute('SELECT uid FROM owned_import_receipts WHERE job_id=?', (import_id,)).fetchone()
            if (not project or project['owner_uid'] != uid) and not (role == 'result' and prior and prior['uid'] == uid):
                raise self.Error('The destination project is no longer available.', 404)
            rows = db.execute('SELECT uid,state,byte_count FROM owned_assets').fetchall()
            if sum(r['byte_count'] for r in rows if r['uid'] == uid) + size > self.owner_limit:
                raise self.Error('Your retained Vision storage is full. No saved source was deleted.', 507)
            if sum(r['byte_count'] for r in rows) + size > self.total_limit:
                raise self.Error('Retained Vision storage is full. No saved source was deleted.', 507)
            reserved = sum(r['byte_count'] for r in rows if r['state'] != 'ready')
            if shutil.disk_usage(self.root).free < self.reserve + reserved + size:
                raise self.Error('FUPCJ Server needs more free space to retain this source.', 507)
            now, asset_id = time.time(), secrets.token_hex(12)
            db.execute('''INSERT INTO owned_assets VALUES(?,?,?,?,?,'copying',?,NULL,?,?,?,?,?)''',
                       (asset_id, uid, key, project_id, role, size, filename, mime, encoded, now, now))
            db.execute('''INSERT OR IGNORE INTO owned_import_receipts
                          (job_id,uid,project_id,created_at,updated_at) VALUES(?,?,?,?,?)''',
                       (import_id, uid, project_id, now, now))
            row = dict(db.execute('SELECT * FROM owned_assets WHERE id=?', (asset_id,)).fetchone())
        self._boundary('reserved')
        return row

    def _digest(self, path, expected):
        reject_link(path)
        digest, count = hashlib.sha256(), 0
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise self.Error('Only regular source files may be retained.', 400)
            while chunk := handle.read(COPY_CHUNK):
                count += len(chunk)
                if count > expected:
                    raise self.Error('The retained source changed during verification.', 409)
                digest.update(chunk)
        if count != expected:
            raise self.Error('The retained source is incomplete. Its temporary copy remains protected.', 409)
        return digest.hexdigest()

    def _publish(self, row, import_id, open_source):
        # No database connection/transaction stays open during copy, hashing or fsync.
        try:
            lock = exclusive_file(self._path(row, '.lock'))
            with lock:
                row = self.row(row['id'], row['uid'])
                if row['state'] == 'ready':
                    self.ready_path(row)
                    return row
                stage, final = self._path(row, '.part'), self._path(row)
                if row['state'] == 'copying':
                    if final.exists():
                        raise self.Error('Asset publication needs administrator recovery.', 409)
                    fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                    digest, count = hashlib.sha256(), 0
                    with os.fdopen(fd, 'wb') as target, open_source() as source:
                        if not stat.S_ISREG(os.fstat(target.fileno()).st_mode):
                            raise self.Error('Only regular asset staging files are accepted.', 400)
                        while chunk := source.read(COPY_CHUNK):
                            count += len(chunk)
                            if count > row['byte_count']:
                                raise self.Error('The acquired source changed during copying.', 409)
                            if shutil.disk_usage(self.root).free < self.reserve + len(chunk):
                                raise self.Error('Copy paused for free disk space. The source remains protected.', 507)
                            target.write(chunk)
                            digest.update(chunk)
                        if count != row['byte_count']:
                            raise self.Error('The acquired source is incomplete. Its temporary copy remains protected.', 409)
                        target.flush()
                        os.fsync(target.fileno())
                    self._boundary('copied')
                    # Re-read what was written, not merely the input stream's hash.
                    sha = digest.hexdigest()
                    if self._digest(stage, row['byte_count']) != sha:
                        raise self.Error('The retained copy failed verification.', 409)
                    with self.db() as db:
                        self._account_active(db, row['uid'])
                        db.execute("UPDATE owned_assets SET state='verified',sha256=?,updated_at=? WHERE id=?",
                                   (sha, time.time(), row['id']))
                    row.update(state='verified', sha256=sha)
                    self._boundary('verified')
                candidate = final if final.exists() else stage
                if self._digest(candidate, row['byte_count']) != row['sha256']:
                    raise self.Error('The retained copy needs administrator recovery; the source lease remains active.', 409)
                if candidate == stage:
                    os.replace(stage, final)
                    sync_directory(final.parent)
                self._boundary('published')
                column = 'source_asset_id' if row['role'] == 'source' else 'result_asset_id'
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    self._account_active(db, row['uid'])
                    receipt = db.execute('SELECT uid FROM owned_import_receipts WHERE job_id=?', (import_id,)).fetchone()
                    if not receipt or receipt['uid'] != row['uid']:
                        raise self.Error('This retained import was removed.', 404)
                    db.execute("UPDATE owned_assets SET state='ready',updated_at=? WHERE id=?", (time.time(), row['id']))
                    db.execute('UPDATE owned_import_receipts SET ' + column + '=?,updated_at=? WHERE job_id=? AND uid=?',
                               (row['id'], time.time(), import_id, row['uid']))
                    if row['role'] == 'source':
                        db.execute('DELETE FROM owned_source_leases WHERE import_id=? AND uid=?', (import_id, row['uid']))
                self._boundary('committed')
                return self.row(row['id'], row['uid'])
        except BlockingIOError:
            raise self.Error('Another worker is retaining this source. Processing will retry.', 503) from None
        except OSError:
            raise self.Error('Retaining this import needs disk or filesystem attention. Its source lease remains active.', 507) from None

    def adopt_vortex(self, vortex, uid, import_id, project_id, vortex_id):
        existing = self.import_asset(uid, import_id, 'source')
        if existing:
            self.ready_path(existing)
            return existing
        value = vortex.row(vortex_id, uid)
        with self.db() as db:
            lease = db.execute('SELECT 1 FROM owned_source_leases WHERE vortex_id=? AND import_id=? AND uid=?',
                               (vortex_id, import_id, uid)).fetchone()
        if not lease or value['status'] != 'complete' or value['purpose'] != 'vision':
            raise self.Error('The source is not ready for durable adoption.', 409)
        source_path = vortex.output_path(value)
        # Check both parents as well as the file; output_path also guards Vortex paths.
        for path in (vortex.root, source_path.parent, source_path):
            reject_link(path)
        media = json.loads(value.get('media_json') or '{}')
        mime = 'audio/mp4' if source_path.suffix.lower() == '.m4a' else 'video/mp4'
        metadata = dict(acquisition='vortex', sourceURL=value['input'], vortexJobId=vortex_id,
                        representation='acquired-analysis-proxy', quality=value['quality'],
                        platformOriginal=False, media=media)
        row = self._reserve_asset(uid, import_id, project_id, 'source', value['output_size'],
                                  vortex.download_name(value), mime, metadata)
        @contextmanager
        def open_source():
            for path in (vortex.root, source_path.parent, source_path):
                reject_link(path)
            fd = os.open(source_path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size != row['byte_count']:
                    raise self.Error('The acquired source is unavailable or changed.', 409)
                yield source
                after = os.fstat(source.fileno())
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise self.Error('The acquired source changed during copying.', 409)
        return self._publish(row, import_id, open_source)

    def retain_result(self, uid, import_id, project_id, result):
        body = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        source = self.import_asset(uid, import_id, 'source')
        if not source:
            raise self.Error('Retain the source before publishing its evidence.', 409)
        metadata = dict(format='legacy-vortex-result-v1', sourceAssetId=source['id'],
                        limitations=['Sparse sampled frames, not exhaustive video inspection.',
                                     'This result is not a complete project backup.'])
        row = self._reserve_asset(uid, import_id, project_id, 'result', len(body), 'import-result.json',
                                  'application/json', metadata)
        if row['sha256'] and row['sha256'] != hashlib.sha256(body).hexdigest():
            raise self.Error('This import already has different retained evidence.', 409)
        return self._publish(row, import_id, lambda: io.BytesIO(body))

    def register_routes(self):
        from flask import g, jsonify, request, send_file

        def uid():
            if getattr(g, 'auth_kind', '') != 'firebase-google':
                raise self.Error('Sign in to read retained assets.', 401)
            return g.uid

        @self.app.get('/api/owned-assets/imports')
        def retained_imports():
            owner = uid()
            cursor = request.args.get('after', '')
            if cursor and not ID.fullmatch(cursor):
                raise self.Error('Invalid retained import cursor.', 400)
            with self.db() as db:
                self._account_active(db, owner)
                rows = db.execute('SELECT * FROM owned_import_receipts WHERE uid=? AND job_id>? ORDER BY job_id LIMIT 51',
                                  (owner, cursor)).fetchall()
            items = [dict(importId=r['job_id'], projectId=r['project_id'], sourceAssetId=r['source_asset_id'],
                          resultAssetId=r['result_asset_id'], createdAt=r['created_at']) for r in rows[:50]]
            response = jsonify(imports=items, nextCursor=rows[49]['job_id'] if len(rows) > 50 else None)
            response.headers['Cache-Control'] = 'private, no-store'
            return response

        @self.app.get('/api/owned-assets/<asset_id>')
        def retained_asset(asset_id):
            response = jsonify(self.snapshot(self.row(asset_id, uid())))
            response.headers['Cache-Control'] = 'private, no-store'
            return response

        @self.app.get('/api/owned-assets/<asset_id>/file')
        def retained_file(asset_id):
            row = self.row(asset_id, uid())
            response = send_file(self.ready_path(row), mimetype=row['mime'], as_attachment=True,
                                 download_name=row['filename'], conditional=True, max_age=0)
            response.headers.update({'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
                                     'Content-Security-Policy': "default-src 'none'; sandbox",
                                     'Referrer-Policy': 'no-referrer'})
            return response
