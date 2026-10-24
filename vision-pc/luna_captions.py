"""Opt-in paid frame captions. Runs only on FUPCJ Server with a DPAPI-held key.

No browser-supplied API key is used. No automatic provider retries: when a
provider's response is uncertain, the collection pauses to avoid repeat charges.
"""
from __future__ import annotations
import base64
import json
import requests

MODEL = 'gpt-6-luna'
INPUT_RATE = 0.10 / 1_000_000
OUTPUT_RATE = 0.50 / 1_000_000
CAP_USD = 0.90                  # Application guard; provider billing can lag.
BATCH_SIZE = 4
MAX_OUTPUT_TOKENS = 2400

SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['frames'],
    'properties': {'frames': {
        'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['index', 'description'],
            'properties': {'index': {'type': 'integer'}, 'description': {'type': 'string'}}
        }
    }}
}

class LunaCaptionError(RuntimeError):
    """Safe, user-facing failure with no secrets or provider payloads."""


def estimated_request_usd(count):
    """Conservative planning quote; actual usage (including reasoning) is metered."""
    return (count * 3500 + 1100) * INPUT_RATE + MAX_OUTPUT_TOKENS * OUTPUT_RATE


def _output_text(response):
    if isinstance(response.get('output_text'), str):
        return response['output_text']
    return ''.join(part.get('text', '') for item in response.get('output', [])
                   if item.get('type') == 'message'
                   for part in item.get('content', []) if part.get('type') == 'output_text')


def paid_amount(response):
    usage = response.get('usage') or {}
    a, b = usage.get('input_tokens'), usage.get('output_tokens')
    if not isinstance(a, int) or not isinstance(b, int) or a < 0 or b < 0:
        raise LunaCaptionError('Luna did not provide usable token billing information. Processing paused to avoid repeated charges.')
    return a * INPUT_RATE + b * OUTPUT_RATE


def extract_captions(response, expected):
    """Require every requested frame, with no index swaps or silent omissions."""
    try:
        data = json.loads(_output_text(response))
        frames = data['frames']
        if not isinstance(frames, list) or len(frames) != len(expected):
            raise ValueError('wrong frame count')
        out = {}
        for frame in frames:
            i, description = frame['index'], frame['description']
            if (not isinstance(i, int) or i not in expected or i in out or
                not isinstance(description, str) or not 12 <= len(description.strip()) <= 3000):
                raise ValueError('invalid frame')
            out[i] = description.strip()
        if set(out) != set(expected):
            raise ValueError('missing frame')
        return out
    except (ValueError, KeyError, TypeError):
        raise LunaCaptionError('Luna returned incomplete frame descriptions. Processing paused to prevent duplicate charges.') from None


def analyze_batch(key, entries, post=None):
    """Returns raw response with usage; caller MUST persist usage before parsing captions.

    entries: [{'index':int,'when':float,'jpeg':bytes,'prompt':str}].
    """
    if not isinstance(key, str) or not key.strip() or any(c.isspace() for c in key) or len(key) > 512:
        raise LunaCaptionError('Save a valid OpenAI API key in your Vision account before using Luna.')
    if not 1 <= len(entries) <= BATCH_SIZE:
        raise LunaCaptionError('Luna frame batch must contain 1–4 images.')
    content = [{'type': 'input_text', 'text': (
        'Return only JSON in the requested schema. Describe EACH image independently, '
        'with concrete visible parts, objects, positions, and changes. '
        'Use surrounding narration to identify terms, never to invent unseen actions. '
        'Write informative descriptions up to roughly 90 words per frame. '
        'If visually uncertain, explicitly say what is uncertain. '
        'Each image has a distinct numeric frame index. Do not combine frame descriptions.') }]
    for entry in entries:
        if not isinstance(entry['jpeg'], bytes) or len(entry['jpeg']) > 524288:
            raise LunaCaptionError('A screenshot was too large for Luna.')
        content += [
            {'type': 'input_text', 'text': (
                f"Frame {entry['index']} timestamp {entry['when']:.2f} seconds. " + entry['prompt'][:1400])},
            {'type': 'input_image', 'image_url': 'data:image/jpeg;base64,' + base64.b64encode(entry['jpeg']).decode('ascii'), 'detail': 'high'}
        ]
    payload = {'model': MODEL, 'store': False, 'reasoning': {'effort': 'low'},
               'max_output_tokens': MAX_OUTPUT_TOKENS,
               'text': {'format': {'type': 'json_schema', 'name': 'vision_frame_captions',
                                    'schema': SCHEMA, 'strict': True}},
               'input': [{'role': 'user', 'content': content}]}
    if post is None:
        post = requests.post
    try:
        resp = post('https://api.openai.com/v1/responses',
                    headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
                    json=payload, timeout=(15, 180))
    except requests.RequestException:
        raise LunaCaptionError('Connection to Luna was interrupted. Processing paused; verify usage before resuming.') from None
    if resp.status_code != 200:
        # Never print provider response bodies: some upstreams echo request data.
        raise LunaCaptionError('Luna request was not accepted. Check your Vision account API key, access, and quota.')
    try:
        result = resp.json()
    except (ValueError, TypeError):
        raise LunaCaptionError('Luna returned an unreadable response. Check usage before resuming.') from None
    if not isinstance(result, dict) or result.get('status') != 'completed':
        raise LunaCaptionError('Luna did not complete the batch. Check usage before resuming.')
    return result
