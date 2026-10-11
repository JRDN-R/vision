"""Opt-in real public-source probe; never print media URLs, cookies or content.

A passing unit suite is not evidence of a working provider. This separate probe
records the live outcome and deletes all downloaded media on exit.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vision-pc'))
from vortex_adapters import instagram_shortcode
from vortex_urls import identity, platform


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-public-fxembed', action='store_true',
                        help='Explicitly send this public X post ID to api.fxtwitter.com for this test only.')
    args = parser.parse_args()
    value = os.environ.get('VORTEX_PROBE_URL', '')
    if not value:
        raise SystemExit('Set VORTEX_PROBE_URL to a public post URL.')
    provider = platform(value)
    report = {'provider': provider, 'status': 'not_run', 'verified': False,
              'environment': 'test runner, not FUPCJ', 'attempts': []}
    if provider == 'twitter':
        report['requestedId'] = identity(value)[1]
    elif provider == 'instagram':
        report['requestedId'] = instagram_shortcode(value)
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise SystemExit('FFmpeg is required for the live probe.')
    with tempfile.TemporaryDirectory(prefix='vortex-public-probe-') as temporary:
        root = Path(temporary)
        directory = root / 'job'
        services = {}
        if args.allow_public_fxembed and provider == 'twitter':
            services['fxembed'] = {'enabled': True, 'url': 'https://api.fxtwitter.com/', 'allowExternal': True}
        specification = dict(input=value, kind='download', quality='balanced', directory=str(directory),
            maxBytes=64 * 1024 * 1024, maxDuration=7200, maxItems=50, ffmpeg=ffmpeg,
            services=services, downloadMode='video', videoFormat='mp4', audioFormat='m4a')
        path = root / 'request.json'
        path.write_text(json.dumps(specification), encoding='utf-8')
        environment = {k: v for k, v in os.environ.items()
                       if not any(s in k.upper() for s in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY', 'CREDENTIAL'))}
        process = subprocess.Popen([sys.executable, str(ROOT / 'vision-pc/vortex_worker.py'), '--request', str(path)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=environment, text=True)
        final = None
        try:
            output, _ = process.communicate(timeout=240)
        except subprocess.TimeoutExpired:
            report['status'] = 'timeout'
            try:
                parent = psutil.Process(process.pid)
                for child in reversed(parent.children(recursive=True)):
                    try: child.kill()
                    except psutil.NoSuchProcess: pass
                parent.kill()
            except psutil.NoSuchProcess:
                pass
            output, _ = process.communicate(timeout=10)
        for line in output.splitlines():
            try: event = json.loads(line)
            except ValueError: continue
            if not isinstance(event, dict): continue
            if isinstance(event.get('diagnostic'), dict):
                data = event['diagnostic']
                report['attempts'].append({k: data[k] for k in ('engine', 'category', 'elapsedMs') if k in data})
            if event.get('complete') is True:
                final = event
        if process.returncode == 0 and final:
            name = final.get('filename')
            artifact = directory / name if isinstance(name, str) and Path(name).name == name else None
            if artifact and artifact.is_file() and not artifact.is_symlink():
                report.update(status='downloaded_and_verified', verified=True, bytes=artifact.stat().st_size,
                              sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
                report['media'] = {k: final.get('media', {}).get(k) for k in ('duration', 'width', 'height', 'mediaType', 'ext')}
        if report['status'] == 'not_run':
            report['status'] = 'no_verified_download'
    # Deliberately report a failed provider attempt as failed, not a green unit
    # test. The workflow runs this optional availability probe separately.
    print('VORTEX_LIVE_REPORT=' + json.dumps(report, sort_keys=True))
    return 0 if report['verified'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
