"""Verified exact-model overrides, separate from Vision's fallback safety policy.

The model-list API returns IDs, not parameter schemas. Never label an unknown
snapshot's inherited app cap as a verified provider maximum.
"""
import json
from pathlib import Path
import re

REGISTRY = json.loads(Path(__file__).with_name('model-parameters.json').read_text(encoding='utf-8'))


def profile(model):
    return REGISTRY['models'].get(model)


def output_limit(model):
    known = profile(model)
    if known:
        return known['maxOutput']
    return 128000 if re.match(r'^(?:gpt-6(?:[.-]|$)|gpt-5\.6(?:[.-]|$))', str(model or '')) else 64000


def validate_parameters(model, options, limit):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 512 <= limit <= output_limit(model):
        raise ValueError(f'Choose an output limit from 512 to {output_limit(model):,} for this model. No output budget was silently reduced.')
    known = profile(model)
    if not known:
        return  # Existing unreviewed model IDs remain explicit, not falsely verified.
    if options.get('effort', 'auto') not in known['efforts']:
        raise ValueError('This model does not support the selected thinking effort.')
    if options.get('mode', 'auto') != 'auto' and not known['pro']:
        raise ValueError('This model does not accept the standard/pro reasoning-mode control. Choose Model default.')
    if options.get('verbosity', 'auto') != 'auto' and not known['verbosity']:
        raise ValueError('This model does not accept the verbosity control. Choose Model default.')
