"""Small Gemini 3.8 Flash image-description client using the installed HTTP stack.

Only the protected FUPCJ service sends provider requests. No automatic retries
or ambiguous response replays. Structured responses map captions by frame ID.
"""
from __future__ import annotations
import base64
import json
import requests

from luna_captions import extract_captions, LunaCaptionError

MODEL = 'gemini-3.8-flash'
BATCH_SIZE = 4
MAX_OUTPUT_TOKENS = 2400
URL = 'https://generativelanguage.googleapis.com/v1beta/interactions'
SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['frames'],
    'properties': {'frames': {'type': 'array',
                             'items': {'type': 'object', 'additionalProperties': False,
                                       'required': ['index','description'],
                                       'properties': {'index': {'type': 'integer'},
                                                      'description': {'type': 'string'}}}}}
}


class GeminiCaptionError(RuntimeError):
    """Safe error without exposing API keys, prompts or provider response."""


def _output_text(result):
    if isinstance(result.get('output_text'), str):
        return result['output_text']
    output = []
    for step in result.get('steps', result.get('outputs', [])):
        if not isinstance(step, dict):
            continue
        content = step.get('content', [step])
        if isinstance(content, dict):
            content = [content]
        if isinstance(content, list):
            output += [x.get('text', '') for x in content
                       if isinstance(x, dict) and x.get('type', 'text') == 'text']
    return ''.join(output)


def captions(result, indices):
    try:
        return extract_captions({'output_text': _output_text(result)}, indices)
    except LunaCaptionError:
        raise GeminiCaptionError(
            'Gemini returned incomplete captions. The processed batch will use Qwen; no automatic Gemini retry.') from None


def analyze_batch(key, entries, post=None):
    if not isinstance(key, str) or not 20 <= len(key) <= 256 or any(c.isspace() for c in key):
        raise GeminiCaptionError('Saved Gemini connection is unavailable.')
    if not 1 <= len(entries) <= BATCH_SIZE:
        raise GeminiCaptionError('Gemini caption batch must contain one to four images.')
    content = [{'type':'text', 'text':(
        'Produce one detailed, factual description for EACH screenshot in JSON. '
        'Include visible objects, tools, part locations, and observable changes. '
        'Do not infer that a step occurred merely because the transcript mentions it. '
        'Identify uncertain objects as uncertain and preserve frame indices. '
        'Return up to roughly 90 words per frame.')}]
    for f in entries:
        jpeg = f.get('jpeg')
        if not isinstance(jpeg, bytes) or len(jpeg) > 524288:
            raise GeminiCaptionError('A screenshot exceeded the supported size.')
        content += [
            {'type':'text', 'text':f"Frame {f['index']} at {f['when']:.2f} s. {f['prompt'][:1400]}"},
            {'type':'image', 'data':base64.b64encode(jpeg).decode('ascii'), 'mime_type':'image/jpeg'}
        ]
    body = {
        'model': MODEL, 'input': content,
        'generation_config': {'thinking_level': 'low', 'max_output_tokens': MAX_OUTPUT_TOKENS},
        'response_format': {'type':'text', 'mime_type':'application/json', 'schema':SCHEMA},
        'store': False
    }
    if post is None:
        post = requests.post
    try:
        response = post(URL, headers={'x-goog-api-key':key,'Content-Type':'application/json'},
                        json=body, timeout=(15,180), allow_redirects=False)
    except requests.RequestException:
        raise GeminiCaptionError(
            'Gemini connection was interrupted. Charges may have occurred; falling back without replaying Gemini.') from None
    if response.status_code != 200:
        raise GeminiCaptionError('Gemini could not accept the request; falling back to local Qwen.')
    try:
        value = response.json()
    except (TypeError, ValueError):
        raise GeminiCaptionError('Gemini returned an unreadable response; falling back to local Qwen.') from None
    if not isinstance(value, dict) or value.get('status') not in (None, 'completed'):
        raise GeminiCaptionError('Gemini did not complete the request; falling back to local Qwen.')
    return value


def usage(result):
    return result.get('usage') or result.get('usageMetadata') or {}
