"""Durable, deterministic caption-provider failover for one Vision frame collection.

Only the next provider is selected, never a second attempt at an uncertain
paid request. Each transition is persisted before a new request is scheduled.
"""
from __future__ import annotations
import json
import time

CHAIN = {'luna': 'gemini', 'gemini': 'local'}
LABELS = {'luna': 'GPT-6 Luna', 'gemini': 'Gemini 3.8 Flash',
          'local': 'Qwen3.5-0.8B'}
DEFAULT_LABEL = 'Automatic · Luna → Gemini 3.8 Flash → Qwen3.5-0.8B'
MAX_USD = 0.90


def _history(value):
    try:
        parsed = json.loads(value or '[]')
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def advance(db, cid, source, reason):
    """Move failed provider -> next one if auto is enabled, else pause.

    Returns the next provider or None. The caller must not replay a failed
    provider request automatically, even when its billing outcome is unknown.
    """
    row = db.execute('SELECT caption_engine,fallback_enabled,fallback_history_json FROM frame_collections WHERE id=?',
                     (cid,)).fetchone()
    if row is None or row['caption_engine'] != source:
        return None
    reason = str(reason).replace('\n', ' ')[:200]
    next_engine = CHAIN.get(source) if row['fallback_enabled'] else None
    if not next_engine:
        db.execute("UPDATE frame_collections SET status='paused',error=?,luna_inflight=0 WHERE id=?",
                   (reason or 'Caption provider unavailable.', cid))
        return None
    history = _history(row['fallback_history_json'])
    history.append({'from': source, 'to': next_engine, 'reason': reason, 'timestamp': round(time.time())})
    db.execute("""UPDATE frame_collections
                  SET caption_engine=?,analysis_step=1,status='queued',
                      fallback_history_json=?,retry_at=0,error=NULL,luna_inflight=0
                  WHERE id=?""", (next_engine, json.dumps(history[-20:]), cid))
    return next_engine


def failover(store, cid, source, reason):
    with store.db() as db:
        return advance(db, cid, source, reason)


def recover_inflight(db):
    """Never restart an unfinished paid call on the same provider."""
    rows = [dict(row) for row in db.execute(
        "SELECT id,caption_engine FROM frame_collections WHERE status='processing' "
        "AND caption_engine IN ('luna','gemini')")]
    for row in rows:
        advance(db, row['id'], row['caption_engine'],
                'A paid request was interrupted by a server restart. Its cost is uncertain.')
