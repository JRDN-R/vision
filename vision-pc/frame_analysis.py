"""Project-owned frame sets and a resumable, serialized local caption queue.

No provider calls or model downloads. Images are fetched only through project
credentials. Exact pixel duplicates reuse an explicitly identified observation.
"""
from __future__ import annotations
import hashlib
import io
import json
import math
import shutil
import threading
import time
from pathlib import Path
from flask import jsonify, request, send_file
from media import data_url
from owned_assets import exclusive_file
from local_visual_runtime import VisualRuntime, MODEL_SHA, IncompleteVisualInspection
from uploaded_media import atomic_json
from project_naming import ProjectNaming
from frame_context import caption_prompt, timed_cues
from luna_caption_worker import work_luna_batch
from gemini_caption_worker import work_gemini_batch
from frame_fallback import recover_inflight, DEFAULT_LABEL

PRESETS = {'maximum': (.25, 1200), 'medium': (1, 600), 'balanced': (3, 300), 'minimal': (10, 120)}
MAX_BYTES = 256 * 1024 * 1024
TOTAL_BYTES = 4 * 1024**3


def processing_options(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {'screenshotDetail': raw.get('screenshotDetail') if raw.get('screenshotDetail') in PRESETS else 'balanced',
            'describeFrames': raw.get('describeFrames') is True,
            'frameDescriptionEngine': raw.get('frameDescriptionEngine') if raw.get('frameDescriptionEngine') in ('auto','luna','local') else 'auto'}


def frame_interval(duration, detail):
    seconds, limit = PRESETS.get(detail, PRESETS['balanced'])
    return max(seconds, float(duration) / limit)


def image_metadata(picture):
    from PIL import Image
    width, height = picture.size
    divisor = math.gcd(width, height)
    colors = picture.resize((64, 64)).quantize(colors=5, method=Image.Quantize.MEDIANCUT).convert('RGB').getcolors(4096) or []
    return dict(width=width, height=height, aspectRatio=f'{width//divisor}:{height//divisor}',
                prominentColors=['#%02x%02x%02x' % rgb for _, rgb in sorted(colors, reverse=True)[:5]],
                scenePixels=list(picture.resize((8, 8)).convert('RGB').tobytes()))


class FrameAnalysis:
    def __init__(self, app, db, error, sessions):
        self.app, self.db, self.Error, self.sessions = app, db, error, sessions
        self.stop, self.lock = threading.Event(), threading.RLock()
        self.runtime_factory = VisualRuntime
        self.register_routes()
        self.naming = ProjectNaming(self)

    @property
    def root(self):
        return self.app.config['DATA_DIR'] / 'frame-collections'

    def initialize(self):
        self.stop.clear()
        self.root.mkdir(exist_ok=True)
        self.naming.initialize()
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS frame_collections (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
                bytes INTEGER NOT NULL DEFAULT 0, error TEXT, created_at REAL NOT NULL)''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(frame_collections)')}
            for name, definition in [('analysis_step', 'INTEGER NOT NULL DEFAULT 1'), ('failures', 'INTEGER NOT NULL DEFAULT 0'), ('retry_at', 'REAL NOT NULL DEFAULT 0'),
                                     ('caption_engine', "TEXT NOT NULL DEFAULT 'local'"), ('luna_cost_usd', 'REAL NOT NULL DEFAULT 0'),
                                     ('luna_requests', 'INTEGER NOT NULL DEFAULT 0'), ('luna_inflight', 'INTEGER NOT NULL DEFAULT 0'),
                                     ('gemini_cost_usd', 'REAL NOT NULL DEFAULT 0'), ('gemini_requests', 'INTEGER NOT NULL DEFAULT 0'),
                                     ('fallback_enabled', 'INTEGER NOT NULL DEFAULT 0'),
                                     ('fallback_history_json', "TEXT NOT NULL DEFAULT '[]'")]:
                if name not in columns:
                    db.execute(f'ALTER TABLE frame_collections ADD COLUMN {name} {definition}')
            recover_inflight(db)
            db.execute("UPDATE frame_collections SET status='queued' WHERE status='processing'")
            if 'failures' not in columns:
                # One-time recovery for the old incomplete-generation failure.
                db.execute("UPDATE frame_collections SET status='queued',error=NULL WHERE status='paused' AND error=?",
                           ('Visual inspection did not finish. No incomplete finding was cached.',))

    def create(self, project_id, collection_id):
        # Called only with a server-generated job ID and an authorized project.
        with self.lock, self.db() as db:
            old = db.execute('SELECT * FROM frame_collections WHERE id=?', (collection_id,)).fetchone()
            if old:
                if old['project_id'] != project_id:
                    raise ValueError('Frame collection ownership changed.')
                return collection_id
            used = db.execute('SELECT COALESCE(SUM(bytes),0) FROM frame_collections').fetchone()[0]
            if used + MAX_BYTES > TOTAL_BYTES or shutil.disk_usage(self.root).free < MAX_BYTES + 1024**3:
                raise ValueError('Frame storage is full. Remove an older frame collection in Project Settings.')
            (self.root / collection_id).mkdir(exist_ok=True)
            db.execute("INSERT INTO frame_collections(id,project_id,status,created_at) VALUES(?,?,'collecting',?)", (collection_id, project_id, time.time()))
        return collection_id

    def add(self, collection_id, index, when, jpeg, metadata):
        if not 0 <= index < 1200 or len(jpeg) > 512*1024:
            raise ValueError('A screenshot exceeded its storage limit.')
        with self.lock, self.db() as db:
            row = db.execute('SELECT * FROM frame_collections WHERE id=?', (collection_id,)).fetchone()
            if not row or row['status'] != 'collecting':
                raise ValueError('This frame collection is no longer accepting screenshots.')
            directory = self.root / collection_id
            path = directory / f'{index:04d}.jpg'
            previous = path.stat().st_size if path.exists() else 0
            size = row['bytes'] - previous + len(jpeg)
            total = db.execute('SELECT COALESCE(SUM(bytes),0) FROM frame_collections').fetchone()[0]
            if size > MAX_BYTES or total - previous + len(jpeg) > TOTAL_BYTES:
                raise ValueError('The screenshot collection exceeded its storage limit.')
            temporary = path.with_suffix('.tmp'); temporary.write_bytes(jpeg); temporary.replace(path)
            record = dict(index=index, timestamp=when, **metadata)
            atomic_json(directory / f'{index:04d}.json', record)
            db.execute('UPDATE frame_collections SET count=MAX(count,?),bytes=? WHERE id=?', (index+1, size, collection_id))
        return dict(name=f'frame-{index+1:04d}.jpg', timestamp=when, mime='image/jpeg', size=len(jpeg),
                    frameRef=dict(collectionId=collection_id, index=index, projectId=row['project_id']))

    def seal(self, collection_id, describe, detail=None, engine='local'):
        engine = 'luna' if engine == 'luna' else 'local'
        with self.db() as db:
            db.execute("UPDATE frame_collections SET status=?,analysis_step=?,caption_engine=? WHERE id=? AND status='collecting'", ('queued' if describe else 'disabled', 1 if engine == 'luna' else (2 if detail in ('maximum', 'medium') else 1), engine, collection_id))

    def row(self, collection_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM frame_collections WHERE id=?', (collection_id,)).fetchone()
        return dict(row) if row else None

    def authorize(self, project_id, collection_id):
        self.sessions.project(project_id)
        row = self.row(collection_id)
        if not row or row['project_id'] != project_id:
            raise self.Error('This frame collection is unavailable.', 404)
        return row

    def report(self, row):
        records = []
        directory = self.root / row['id']
        for index in range(row['completed']):
            path = directory / f'{index:04d}.analysis.json'
            if path.exists():
                records.append(json.loads(path.read_text()))
        groups = []
        for record in records:
            if record.get('analysisStatus') == 'skipped':
                continue
            if groups and record.get('sameScene') is True:
                groups[-1]['endFrame'] = record['index']; groups[-1]['end'] = record['timestamp']
                if record['description'] != groups[-1]['observations'][-1]['description']:
                    groups[-1]['observations'].append(dict(timestamp=record['timestamp'],description=record['description']))
            else:
                groups.append(dict(startFrame=record['index'], endFrame=record['index'], start=record['timestamp'],
                                   end=record['timestamp'], description=record['description'],
                                   observations=[dict(timestamp=record['timestamp'],description=record['description'])]))
        return dict(id=row['id'], status=row['status'], completed=row['completed'], count=row['count'], error=row['error'],
                    model='gpt-6-luna' if row.get('caption_engine') == 'luna' else 'Qwen3.5-0.8B',
                    modelSha256=None if row.get('caption_engine') == 'luna' else MODEL_SHA,
                    estimatedProviderCostUSD=round(row.get('luna_cost_usd', 0), 6),
                    providerRequests=row.get('luna_requests', 0), frames=records, timeline=groups,
                    analysisStep=row.get('analysis_step', 1), analyzed=sum(r.get('analysisStatus') != 'skipped' for r in records),
                    planned=(row['count']+row.get('analysis_step',1)-1)//row.get('analysis_step',1),
                    grouping='Nearby frames are grouped by coarse visual similarity, not confirmed identity. Each changed observation is retained. Sampled timestamps do not prove continuous action.')

    def register_routes(self):
        @self.app.get('/api/projects/<project_id>/frames')
        def list_frames(project_id):
            self.sessions.project(project_id)
            with self.db() as db:
                rows = [dict(row) for row in db.execute('SELECT * FROM frame_collections WHERE project_id=? ORDER BY created_at DESC', (project_id,))]
            return jsonify(items=rows)

        @self.app.get('/api/projects/<project_id>/frames/<collection_id>/<int:index>')
        def frame_image(project_id, collection_id, index):
            row = self.authorize(project_id, collection_id)
            if not 0 <= index < row['count']:
                raise self.Error('This frame is unavailable.', 404)
            return send_file(self.root / row['id'] / f'{index:04d}.jpg', mimetype='image/jpeg', conditional=True)

        @self.app.route('/api/projects/<project_id>/frames/<collection_id>', methods=['GET', 'POST', 'DELETE'])
        def frame_report(project_id, collection_id):
            row = self.authorize(project_id, collection_id)
            with self.lock:
                if request.method == 'DELETE':
                    with self.db() as db:
                        db.execute('DELETE FROM frame_collections WHERE id=?', (collection_id,))
                    shutil.rmtree(self.root / row['id'], ignore_errors=True)
                    return jsonify(deleted=True)
                if request.method == 'POST':
                    action = (request.get_json(silent=True) or {}).get('action')
                    if action not in ('pause', 'resume') or row['status'] in ('collecting', 'complete'):
                        raise self.Error('Choose pause or resume for an unfinished analysis.')
                    with self.db() as db:
                        db.execute('UPDATE frame_collections SET status=?,error=NULL,failures=0,retry_at=0 WHERE id=?', ('paused' if action == 'pause' else 'queued', collection_id))
                    row = self.row(collection_id)
                return jsonify({key:row[key] for key in ('id','status','completed','count','error','analysis_step','caption_engine','luna_cost_usd')} if request.args.get('summary')=='1' else self.report(row))

    def caption_material(self, row):
        """Match captions to this video's own title and asynchronous speech receipt.

        An opted-in transcription gets a bounded chance to finish before captions
        begin. Other videos never wait. No external provider is contacted.
        """
        with self.db() as db:
            source = db.execute('SELECT source_name,updated_at FROM uploaded_media WHERE id=? AND project_id=?',
                                (row['id'], row['project_id'])).fetchone()
            speech = db.execute('''SELECT status,result_json FROM local_transcriptions
                WHERE project_id=? AND client_id=?''', (row['project_id'], 'media-' + row['id'])).fetchone()
        requested = (self.app.config['DATA_DIR'] / 'uploaded-media' / row['id'] / 'transcription.json').is_file()
        # Do not stall on an unapproved/failed transcript indefinitely.
        if requested and (speech is None or speech['status'] in ('queued', 'processing', 'approval_waiting')):
            if time.time() - (source['updated_at'] if source else row['created_at']) < 20 * 60:
                return None
        cues = timed_cues(speech['result_json']) if speech and speech['status'] == 'complete' else []
        return (source['source_name'] if source else ''), cues

    def work_once(self):
        # Foreground Qwen work has priority. A batch releases the shared model
        # lock after at most 16 images; all completed observations are durable.
        with self.db() as db:
            if db.execute("SELECT 1 FROM project_runs WHERE provider='local_llama_cpp' AND status IN ('queued','preparing','submitting','in_progress','saving') LIMIT 1").fetchone():
                return False
            row = db.execute("SELECT * FROM frame_collections WHERE status='queued' AND retry_at<=? ORDER BY created_at LIMIT 1", (time.time(),)).fetchone()
        if not row:
            return False
        row = dict(row)
        trials = self.app.config.get('TRIALS')
        if trials and trials.expired(row['project_id']):
            return False
        row = dict(row); cid = row['id']; directory = self.root / cid
        if row.get('caption_engine') == 'luna':
            material = self.caption_material(row)
            if material is None:
                with self.db() as db:
                    db.execute("UPDATE frame_collections SET retry_at=? WHERE id=? AND status='queued'", (time.time()+30, cid))
                return False
            return work_luna_batch(self, row, *material)
        settings = self.sessions.local.settings
        if not settings.get('enabled') or not settings.get('visualEnabled'):
            with self.db() as db:
                db.execute("UPDATE frame_collections SET status='paused',error=? WHERE id=?", ('Enable the verified local visual worker on FUPCJ Server, then resume.', cid))
            return True
        material = self.caption_material(row)
        if material is None:
            with self.db() as db:
                db.execute("UPDATE frame_collections SET retry_at=? WHERE id=? AND status='queued'", (time.time()+30, cid))
            return False
        video_title, transcript_cues = material
        try:
            # Defer while OBS is running or system memory is constrained.
            import psutil
            if psutil.virtual_memory().available < 10*1024**3 or any((p.info.get('name') or '').lower() in ('obs','obs.exe','obs64','obs64.exe','obs32.exe') for p in psutil.process_iter(['name'])):
                return False
            with exclusive_file(self.app.config['DATA_DIR'] / 'local-inference.lock'):
                with self.db() as db:
                    db.execute("UPDATE frame_collections SET status='processing' WHERE id=? AND status='queued'", (cid,))
                def cancelled():
                    current = self.row(cid)
                    return self.stop.is_set() or not current or current['status'] != 'processing'
                from PIL import Image
                prior = None
                for previous in range(row['completed']-1, -1, -1):
                    prior = json.loads((directory / f'{previous:04d}.analysis.json').read_text())
                    if prior.get('analysisStatus') != 'skipped':
                        break
                    prior = None
                with self.runtime_factory(settings, cancelled, lambda _: None) as runtime:
                    for index in range(row['completed'], min(row['count'], row['completed']+16)):
                        if cancelled():
                            break
                        metadata = json.loads((directory / f'{index:04d}.json').read_text())
                        if index % row.get('analysis_step', 1):
                            record = dict(**metadata, analysisStatus='skipped', description=None, independentlyInspected=False)
                            with self.lock:
                                if cancelled():break
                                atomic_json(directory / f'{index:04d}.analysis.json', record)
                                with self.db() as db:
                                    db.execute('UPDATE frame_collections SET completed=? WHERE id=?', (index+1,cid))
                            continue
                        with Image.open(directory / f'{index:04d}.jpg') as picture:
                            picture = picture.convert('RGB').crop((0, 0, metadata['width'], metadata['height']))
                            digest = hashlib.sha256(picture.tobytes()).hexdigest()
                            output = io.BytesIO(); picture.save(output, 'JPEG', quality=90)
                        reused = prior and prior['pixelHash'] == digest
                        before = prior.get('scenePixels', []) if prior else []
                        pixels = metadata.get('scenePixels', [])
                        same_scene = len(before) == len(pixels) == 192 and sum(abs(a-b) for a,b in zip(before,pixels))/192 < 12
                        # Previous descriptions are labeled unverified and only
                        # provided for pixel-similar scenes. Title/transcript are
                        # candidate vocabulary, not claims about visible pixels.
                        prompt, context = caption_prompt(video_title, transcript_cues,
                                                          metadata['timestamp'], prior, same_scene)
                        description = prior['description'] if reused else runtime.inspect(data_url(output.getvalue(), 'image/jpeg'), prompt)
                        record = dict(**metadata, pixelHash=digest, description=description,
                                      context=context, analysisStatus='reused' if reused else 'inspected',
                                      independentlyInspected=not bool(reused), sameScene=same_scene)
                        if reused:
                            record['reusedFrom'] = prior.get('reusedFrom', prior['index'])
                        with self.lock:
                            if cancelled():
                                break
                            atomic_json(directory / f'{index:04d}.analysis.json', record)
                            with self.db() as db:
                                db.execute('UPDATE frame_collections SET completed=?,failures=0,retry_at=0,error=NULL WHERE id=?', (index+1, cid))
                        prior = record
                with self.db() as db:
                    db.execute("UPDATE frame_collections SET status=CASE WHEN completed=count THEN 'complete' ELSE 'queued' END WHERE id=? AND status='processing'", (cid,))
        except InterruptedError:
            with self.db() as db:
                db.execute("UPDATE frame_collections SET status='queued' WHERE id=? AND status='processing'", (cid,))
        except IncompleteVisualInspection as error:
            with self.db() as db:
                # Checkpoints survive retries; a bad frame cannot loop forever.
                db.execute("""UPDATE frame_collections SET failures=failures+1,
                    status=CASE WHEN failures<2 THEN 'queued' ELSE 'paused' END,
                    retry_at=?,error=? WHERE id=? AND status='processing'""",
                    (time.time()+30, 'Retrying incomplete visual inspection automatically on FUPCJ Server.', cid))
                db.execute("UPDATE frame_collections SET error=? WHERE id=? AND status='paused' AND failures>=3",
                           ('Visual inspection did not finish after 3 attempts. Completed frames are saved. Resume to retry.', cid))
        except BlockingIOError:
            return False
        except Exception as error:
            with self.db() as db:
                db.execute("UPDATE frame_collections SET status=?,error=? WHERE id=? AND status IN ('queued','processing')", ('queued' if self.stop.is_set() else 'paused', None if self.stop.is_set() else str(error)[:400], cid))
        return True

    def start(self):
        def run():
            while not self.stop.is_set():
                try:
                    worked = self.naming.work_once()
                    if not worked:
                        worked = self.work_once()
                except Exception:
                    worked = False
                    self.app.logger.exception('Frame analysis queue failed')
                self.stop.wait(1.5 if worked else 5)
        threading.Thread(target=run, name='vision-frame-analysis', daemon=True).start()
