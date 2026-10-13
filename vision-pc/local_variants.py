"""Side-by-side Qwen selection. Browser input is a boolean, never a model path.

The original model/default is unchanged. Heretic needs an explicit operator
installation and a receipt for the exact CPU profile. Every inference still
performs the full pinned model/runtime verification in LlamaRuntime.verify.
"""
import json
from pathlib import Path

from local_runtime import HERETIC_SPEC, LocalFailure, digest_file
from owned_assets import reject_link

PROFILE_FIELDS = ('runtimePath', 'modelPath', 'manifestPath', 'port', 'contextTokens',
                  'maxOutputTokens', 'threads', 'gpuLayers', 'reserveRamGiB',
                  'maxRunSeconds', 'maxResidentGiB', 'visualEnabled', 'visualManifestPath')
RECEIPT_SCHEMA = 'vision-heretic-ready-v1'


def checked_path(value):
    path = Path(value)
    if not path.is_absolute():
        raise LocalFailure('A local variant installation path must be absolute.')
    for part in (path, *path.parents):
        reject_link(part)
    return path


def variant_root(settings):
    return checked_path(settings['modelPath']).parent / 'variants' / 'heretic' / HERETIC_SPEC['modelRevision']


def read_json(path):
    path = checked_path(path)
    if path.stat().st_size > 131072:
        raise LocalFailure('Local variant metadata exceeds its bound.')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise LocalFailure('Invalid local variant metadata.')
    return value


def profile_key(profile):
    defaults = dict(gpuLayers=0, contextTokens=4096, threads=8, maxOutputTokens=2048,
                    port=8101, reserveRamGiB=10, maxRunSeconds=1800, maxResidentGiB=16)
    return {key: profile.get(key, default) for key, default in defaults.items()}


def stamp(path):
    info = checked_path(path).stat()
    return [info.st_size, info.st_mtime_ns]


def receipt_path(profile):
    return checked_path(profile['manifestPath']).parent / 'ready.json'


def check_ready(profile):
    variant = profile.get('modelVariant', 'standard')
    if variant == 'standard':
        return
    if variant != 'heretic':
        raise LocalFailure('Unknown local variant. No fallback was used.')
    manifest = read_json(profile['manifestPath'])
    if any(manifest.get(k) != v for k, v in HERETIC_SPEC.items()):
        raise LocalFailure('Heretic provenance does not match the pinned variant.')
    receipt = read_json(receipt_path(profile))
    if (receipt.get('schema') != RECEIPT_SCHEMA or receipt.get('passed') is not True or
            receipt.get('profile') != profile_key(profile) or
            receipt.get('manifestSha256') != digest_file(profile['manifestPath']) or
            receipt.get('modelStamp') != stamp(profile['modelPath']) or
            receipt.get('runtimeStamp') != stamp(profile['runtimePath']) or
            stamp(profile['modelPath'])[0] != HERETIC_SPEC['modelBytes']):
        raise LocalFailure('Heretic needs an explicit Enable test for this exact installed profile.')


def select_profile(settings, uncensored=False, require_ready=True):
    if not isinstance(uncensored, bool):
        raise LocalFailure('Uncensored must be on or off.')
    profile = {key: value for key, value in settings.items() if key in PROFILE_FIELDS}
    profile['modelVariant'] = 'heretic' if uncensored else 'standard'
    if uncensored:
        root = checked_path(variant_root(settings))
        # Anchor the existing visual worker BEFORE changing the answering path.
        profile['visualManifestPath'] = str(checked_path(settings.get('visualManifestPath') or
            checked_path(settings['modelPath']).parent / 'visual' / 'visual-worker-manifest.json'))
        profile.update(modelPath=str(root / HERETIC_SPEC['modelFile']),
                       manifestPath=str(root / 'installed-manifest.json'), gpuLayers=0)
        if require_ready:
            check_ready(profile)
    return profile


def variant_status(settings):
    result = dict(available=False, status='not-installed', vision=False,
                  name='Qwen3.8-9B-heretic-uncensored', quantization='Q4_K_M',
                  modelBytes=HERETIC_SPEC['modelBytes'])
    try:
        profile = select_profile(settings, True, require_ready=False)
        if not Path(profile['modelPath']).is_file():
            return result
        result['status'] = 'needs-enable'
        check_ready(profile)
        result.update(available=settings.get('enabled') is True,
                      status='ready' if settings.get('enabled') is True else 'disabled')
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return result
