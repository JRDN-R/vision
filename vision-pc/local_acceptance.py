"""Read-only eligibility gate. A smoke test can never authorize default migration.

An operator supplies the acceptance receipt only after the documented real-host
checks. Reading this module never enables answering or changes a preference.
"""
import hashlib
import json
import math
from pathlib import Path

from local_visual_runtime import PIPELINE, MODEL_SHA, PROJECTOR_SHA, manifest_path

CHECKS = ('realPhoto', 'screenshot', 'readableText', 'videoTimestamps',
          'questionReinspection', 'restartRecovery', 'accountIsolation',
          'cancellation', 'resourceLimits', 'livestreamCoexistence')
PROFILE_KEYS = ('contextTokens', 'maxOutputTokens', 'threads', 'reserveRamGiB', 'maxRunSeconds', 'maxResidentGiB')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(settings):
    root = Path(__file__).parent
    return dict(pipeline=PIPELINE, modelId='qwen3.8-local', visualSha256=MODEL_SHA,
                projectorSha256=PROJECTOR_SHA, mainManifestSha256=digest(settings['manifestPath']),
                visualManifestSha256=digest(manifest_path(settings)),
                code={name: digest(root / name) for name in ('local_inference.py', 'local_runtime.py',
                     'local_visual.py', 'local_visual_runtime.py', 'local_visual_media.py', 'local_acceptance.py',
                     'context_engine.py', 'context_integration.py', 'sessions.py', 'venture_files.py',
                     'owned_assets.py', 'local_preferences.py', 'venture.py')},
                profile={**{k: settings.get(k) for k in PROFILE_KEYS}, 'gpuLayers': 0, 'visualGpuLayers': 0})


def eligible(settings):
    try:
        path = Path(settings.get('acceptancePath') or '__missing__')
        if not path.is_absolute() or path.is_symlink() or path.stat().st_size > 128000:
            return False
        result = json.loads(path.read_text(encoding='utf-8-sig'))
        if (result.get('schema') != 'qwen-local-acceptance-v1' or result.get('approvedForDefault') is not True or
                result.get('identity') != identity(settings) or not result.get('reviewer') or not result.get('reviewedAt')):
            return False
        if any((result.get('checks', {}).get(key) or {}).get('passed') is not True or
               not result['checks'][key].get('evidence') for key in CHECKS):
            return False
        metrics = result.get('measurements') or {}
        for name in ('peakResidentBytes', 'minimumAvailableRamGiB', 'elapsedSeconds'):
            if type(metrics.get(name)) not in (int, float) or not math.isfinite(metrics[name]) or metrics[name] <= 0:
                return False
        return metrics['minimumAvailableRamGiB'] >= max(8, float(settings.get('reserveRamGiB', 10)))
    except (OSError, ValueError, TypeError, KeyError):
        return False


def main():
    """Generate an explicitly incomplete review template without loading models."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    settings = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))['localAI']
    report = dict(schema='qwen-local-acceptance-v1', approvedForDefault=False,
                  identity=identity(settings), reviewer='', reviewedAt='',
                  checks={key:dict(passed=False, evidence='') for key in CHECKS},
                  measurements=dict(peakResidentBytes=None,minimumAvailableRamGiB=None,elapsedSeconds=None),
                  note='Incomplete review template. Red/blue smoke tests and mocked CI do not satisfy these gates.')
    with Path(args.output).open('x', encoding='utf-8') as target:
        json.dump(report, target, indent=2)
        target.write('\n')
    print('Created an incomplete acceptance template. No model, service, config or account was changed.')


if __name__ == '__main__':
    main()
