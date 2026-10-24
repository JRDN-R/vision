"""Gemini second-choice frame caption worker, with a final local-Qwen fallback."""
from __future__ import annotations
import hashlib
import json
from PIL import Image

from frame_context import caption_prompt
from frame_fallback import failover, MAX_USD
from gemini_captions import (analyze_batch, captions, usage, MODEL,
                              BATCH_SIZE, MAX_OUTPUT_TOKENS, GeminiCaptionError)
from uploaded_media import atomic_json


def _owner_gemini(store, project_id):
    with store.db() as db:
        row = db.execute('SELECT owner_uid FROM projects WHERE id=?', (project_id,)).fetchone()
    uid = row['owner_uid'] if row else None
    if not uid:
        raise GeminiCaptionError('Google sign-in is required for the Gemini service.')
    access = store.app.config.get('GEMINI_ACCESS')
    if access is None or access.status(uid) != 'approved':
        raise GeminiCaptionError('Gemini is not approved for this account.')
    credentials = store.app.config.get('GEMINI_CREDENTIALS')
    if credentials is None or not credentials.available():
        raise GeminiCaptionError('No protected Gemini credential is installed.')
    return uid, credentials.get()


def _estimated_cost(store, count):
    # Derive rates from the server's versioned Gemini price table.
    value = store.app.config['GEMINI_USAGE'].estimate(
        MODEL, {'promptTokenCount': count * 3500 + 1100,
                'candidatesTokenCount': MAX_OUTPUT_TOKENS, 'thoughtsTokenCount': 0})
    return value['cost'] if value.get('available') else None


def work_gemini_batch(store, row, video_title, cues):
    cid, folder = row['id'], store.root / row['id']
    remaining = min(BATCH_SIZE, row['count'] - row['completed'])
    if remaining <= 0:
        with store.db() as db:
            db.execute("UPDATE frame_collections SET status='complete' WHERE id=?", (cid,))
        return True
    try:
        quote = _estimated_cost(store, remaining)
    except Exception:
        quote = None
    if quote is None or row.get('luna_cost_usd', 0) + row.get('gemini_cost_usd', 0) + quote > MAX_USD:
        failover(store, cid, 'gemini',
                 'Gemini pricing is unavailable or the combined cloud budget is near $0.90.')
        return True
    try:
        uid, key = _owner_gemini(store, row['project_id'])
    except Exception:
        failover(store, cid, 'gemini',
                 'Gemini access or protected credentials are unavailable.')
        return True
    with store.db() as db:
        db.execute("UPDATE frame_collections SET status='processing',luna_inflight=1 WHERE id=? AND status='queued'", (cid,))
    prior = None
    if row['completed']:
        path = folder / f"{row['completed']-1:04d}.analysis.json"
        if path.is_file():
            prior = json.loads(path.read_text())
    prepared, entries = [], []
    for i in range(row['completed'], row['completed'] + remaining):
        metadata = json.loads((folder / f'{i:04d}.json').read_text())
        jpeg = (folder / f'{i:04d}.jpg').read_bytes()
        with Image.open(folder / f'{i:04d}.jpg') as picture:
            digest = hashlib.sha256(picture.convert('RGB').tobytes()).hexdigest()
        old_pixels = prior.get('scenePixels', []) if prior else []
        pixels = metadata.get('scenePixels', [])
        same = len(old_pixels) == len(pixels) == 192 and sum(abs(a-b) for a,b in zip(old_pixels,pixels))/192 < 12
        prompt, context = caption_prompt(video_title, cues, metadata['timestamp'], prior, same)
        record = {**metadata, 'context':context, 'pixelHash':digest,'sameScene':same}
        prepared.append(record)
        if prior and prior.get('pixelHash') == digest and prior.get('description'):
            record.update(description=prior['description'],analysisStatus='reused',
                          independentlyInspected=False,
                          reusedFrom=prior.get('reusedFrom', prior['index']))
        else:
            entries.append({'index':i,'when':metadata['timestamp'],'jpeg':jpeg,'prompt':prompt})

    ledger, actual_usage, result_status = None, {}, 'failed'
    try:
        if entries:
            usage_api = store.app.config['GEMINI_USAGE']
            # Same request key cannot be silently reissued after uncertain failure.
            ledger = usage_api.begin_request(uid, row['project_id'], cid, MODEL, kind='frame',
                                             request_key=f"frame-{row['completed']}")
            result = analyze_batch(key, entries)
            actual_usage = usage(result)
            pricing = usage_api.estimate(MODEL, actual_usage)
            result_status = 'succeeded'
            amount = pricing.get('cost') if pricing.get('available') else None
            if amount is not None:
                with store.db() as db:
                    db.execute("UPDATE frame_collections SET gemini_cost_usd=gemini_cost_usd+?,gemini_requests=gemini_requests+1 WHERE id=?",
                               (amount, cid))
            else:
                with store.db() as db:
                    db.execute("UPDATE frame_collections SET gemini_requests=gemini_requests+1 WHERE id=?", (cid,))
            descriptions = captions(result, [e['index'] for e in entries])
        else:
            descriptions = {}
        for record in prepared:
            idx = record['index']
            if idx in descriptions:
                record.update(description=descriptions[idx],analysisStatus='inspected',independentlyInspected=True)
            record['model'] = MODEL if idx in descriptions else (prior.get('model', 'gpt-6-luna') if prior else MODEL)
            with store.lock:
                if store.stop.is_set():
                    raise RuntimeError('Gemini response received during server shutdown')
                atomic_json(folder / f'{idx:04d}.analysis.json', record)
                with store.db() as db:
                    db.execute("UPDATE frame_collections SET completed=? WHERE id=?", (idx+1,cid))
        with store.db() as db:
            db.execute("""UPDATE frame_collections SET
                          status=CASE WHEN completed=count THEN 'complete' ELSE 'queued' END,
                          luna_inflight=0,error=NULL,retry_at=0 WHERE id=?""", (cid,))
    except Exception:
        failover(store, cid, 'gemini', 'Gemini could not finish safely; proceeding to local Qwen without retrying.')
    finally:
        if ledger is not None:
            # The owner's existing Gemini usage ledger owns the billing receipt.
            store.app.config['GEMINI_USAGE'].finish_request(
                ledger, status=result_status, usage=actual_usage)
    return True
