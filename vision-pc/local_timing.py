"""Coarse estimates from this account's completed, comparable local requests.

Never derive a completion percentage from elapsed time. Model loading, source
inspection and generation all count toward the observed processing duration.
"""
from collections import Counter
import json
import math
from pathlib import Path
from statistics import median


def workload(row):
    profile = json.loads(row.get('local_profile_json') or '{}')
    # Path changes invalidate comparisons; enabled flags do not affect speed.
    values = {key:profile.get(key, default) for key,default in (
        ('threads',8),('contextTokens',4096),('visualEnabled',False),('gpuLayers',0),
        ('runtimePath',None),('modelPath',None),('manifestPath',None),('visualManifestPath',None))}
    if values['visualEnabled']:
        values['gpuLayers'] = 0
    kinds = Counter()
    for file in json.loads(row.get('inputs_json') or '[]'):
        mime = str(file.get('mime') or '').split('/')[0]
        suffix = Path(file.get('name') or '').suffix.lower()
        kind = 'image' if mime == 'image' or suffix in ('.png','.jpg','.jpeg','.webp','.heic') else 'video' if mime == 'video' else 'file'
        kinds[kind] += 1
    # Board sources and conversational follow-ups differ from isolated uploads.
    return (json.dumps(values,sort_keys=True),tuple(sorted(kinds.items())),
            bool(row.get('board_context_json')),bool(row.get('previous_id')),int(row['max_tokens']))


def estimate(row, history, elapsed, ahead):
    pending = dict(estimatedRemainingSeconds=None,estimatedRemainingRange=None,
                   estimateStatus='learning',estimateSamples=0)
    try:
        key = workload(row)
        samples = []
        current_tokens = json.loads(row.get('local_metrics_json') or '{}').get('inputTokens')
        for item in history:
            if item['status'] != 'completed' or workload(item) != key:
                continue
            started,finished = item.get('local_started_at'),item.get('local_finished_at')
            if not started or not finished:
                continue
            seconds = finished - started
            tokens = json.loads(item.get('local_metrics_json') or '{}').get('inputTokens')
            if current_tokens and tokens and not .5 <= tokens/current_tokens <= 2:
                continue
            if math.isfinite(seconds) and 1 <= seconds <= 7200:
                samples.append(seconds)
        pending['estimateSamples'] = len(samples)
        if len(samples) < 3:
            return pending
        typical = median(samples)
        low = max(5,min(samples)*.8)*(ahead+1)
        high = max(max(samples)*1.25,typical*1.5)*(ahead+1)
        if elapsed >= high:
            return {**pending,'estimateStatus':'overrun'}
        return dict(estimatedRemainingSeconds=round(max(5,typical*(ahead+1)-elapsed)),
                    estimatedRemainingRange=[round(max(5,low-elapsed)),round(max(10,high-elapsed))],
                    estimateStatus='estimated',estimateSamples=len(samples))
    except (ValueError,TypeError,KeyError,ZeroDivisionError):
        return pending
