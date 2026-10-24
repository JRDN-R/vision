"""Opt-in Luna runner for the existing Vision Windows service.

Never contacts Secret Manager or puts a billing credential in a RAG export.
Uses existing account_credentials DPAPI ciphertext under the service identity.
"""
from __future__ import annotations
import hashlib
import json
from PIL import Image
from frame_context import caption_prompt
from frame_fallback import failover
from luna_captions import (analyze_batch, estimated_request_usd, paid_amount,
                           extract_captions, LunaCaptionError, CAP_USD, BATCH_SIZE)
from sessions import unprotect_secret
from uploaded_media import atomic_json


def _pause(store, cid, message):
    # Auto mode advances to Gemini. Explicit Luna-only mode still pauses.
    return failover(store, cid, 'luna', message)


def _owner_key(store, project_id):
    with store.db() as db:
        row = db.execute("""SELECT ac.openai_key_cipher FROM projects p
                            JOIN account_credentials ac ON ac.uid=p.owner_uid
                            WHERE p.id=? AND p.owner_uid IS NOT NULL""", (project_id,)).fetchone()
    if row is None:
        raise LunaCaptionError('Save your OpenAI API key in Vision Account Settings while signed in with Google to use Luna.')
    try:
        return unprotect_secret(row[0])
    except Exception:
        raise LunaCaptionError('FUPCJ Server could not unlock the saved OpenAI key for Luna. Check Vision Account Settings.') from None


def work_luna_batch(store, row, video_title, cues):
    """Process up to four independent screenshots per paid request.

    Stops (never auto-retries) on uncertain upstream outcomes. Results and
    checkpoint progress are retained. All frames remain accessible offline.
    """
    cid, directory = row['id'], store.root / row['id']
    if row['completed'] >= row['count']:
        with store.db() as db:
            db.execute("UPDATE frame_collections SET status='complete' WHERE id=?", (cid,))
        return True
    try:
        key = _owner_key(store, row['project_id'])
    except LunaCaptionError as ex:
        _pause(store, cid, str(ex))
        return True
    remaining = min(BATCH_SIZE, row['count'] - row['completed'])
    quote = estimated_request_usd(remaining)
    if row.get('luna_cost_usd', 0) + row.get('gemini_cost_usd', 0) + quote > CAP_USD:
        _pause(store, cid, 'Luna paused near the $0.90 application budget. No new paid requests were submitted.')
        return True
    with store.db() as db:
        db.execute("UPDATE frame_collections SET status='processing',luna_inflight=1,error=NULL WHERE id=? AND status='queued'",(cid,))
    prior = None
    if row['completed']:
        old = directory / f"{row['completed']-1:04d}.analysis.json"
        if old.is_file():
            prior = json.loads(old.read_text())
    metadata_list = []
    requests = []
    for index in range(row['completed'], row['completed']+remaining):
        meta = json.loads((directory / f'{index:04d}.json').read_text())
        jpeg = (directory / f'{index:04d}.jpg').read_bytes()
        with Image.open(directory / f'{index:04d}.jpg') as picture:
            digest = hashlib.sha256(picture.convert('RGB').tobytes()).hexdigest()
        pixel_prior = prior.get('scenePixels', []) if prior else []
        px = meta.get('scenePixels', [])
        same = len(pixel_prior) == len(px) == 192 and sum(abs(a-b) for a,b in zip(pixel_prior,px))/192 < 12
        prompt, context = caption_prompt(video_title, cues, meta['timestamp'], prior, same)
        rec = {**meta,'pixelHash':digest,'sameScene':same,'context':context}
        metadata_list.append(rec)
        if prior and prior.get('pixelHash') == digest and prior.get('description'):
            rec.update(description=prior['description'],analysisStatus='reused',independentlyInspected=False,
                       reusedFrom=prior.get('reusedFrom',prior['index']))
        else:
            requests.append({'index':index,'when':meta['timestamp'],'jpeg':jpeg,'prompt':prompt})
        # Only a completed prior batch may influence subsequent frames.
    try:
        if requests:
            response = analyze_batch(key, requests)
            amount = paid_amount(response)
            with store.db() as db:
                db.execute("""UPDATE frame_collections SET luna_cost_usd=luna_cost_usd+?,
                              luna_requests=luna_requests+1 WHERE id=?""",(amount,cid))
            captions = extract_captions(response,[item['index'] for item in requests])
        else:
            captions = {}
        for rec in metadata_list:
            index = rec['index']
            if index in captions:
                rec.update(description=captions[index],analysisStatus='inspected',independentlyInspected=True)
            rec['model'] = 'gpt-6-luna' if index in captions else (prior.get('model', 'gpt-6-luna') if prior else 'gpt-6-luna')
            with store.lock:
                if store.stop.is_set():
                    raise InterruptedError('Luna results arrived after service stop. Check usage before resuming.')
                atomic_json(directory / f'{index:04d}.analysis.json',rec)
                with store.db() as db:
                    db.execute('UPDATE frame_collections SET completed=? WHERE id=?',(index+1,cid))
        with store.db() as db:
            db.execute("""UPDATE frame_collections SET status=CASE WHEN completed=count THEN 'complete' ELSE 'queued' END,
                          luna_inflight=0,error=NULL,retry_at=0 WHERE id=?""", (cid,))
    except (LunaCaptionError, InterruptedError) as err:
        _pause(store,cid,str(err) + ' Check usage before manually resuming; charges may have occurred.')
    except Exception:
        _pause(store,cid,'Luna processing stopped unexpectedly. Check provider usage before manually resuming; no automatic retry.')
    return True
