"""Opt-in real public-source probe; never print media URLs, cookies or content.

Unit tests are not evidence of working providers. This separate probe uses the
production worker and normal 2 GiB/2 hour limits, then deletes all media.
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

# A separate bounded metadata-only check distinguishes a provider failure from
# a policy rejection. It does not weaken or replace the actual worker test.
METADATA_CHECK = r'''
import contextlib, json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from vortex_network import install_network_guard
install_network_guard()
import yt_dlp
class Quiet:
    def debug(self,*a,**k): pass
    def info(self,*a,**k): pass
    def warning(self,*a,**k): pass
    def error(self,*a,**k): pass
report = {'status': 'unavailable'}
try:
    with open(os.devnull,'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        with yt_dlp.YoutubeDL({'quiet':True,'no_warnings':True,'logger':Quiet(),'socket_timeout':15,
             'retries':0,'skip_download':True,'noplaylist':True,'playlistend':1,'cachedir':False,
             'proxy':'','cookiefile':None,'usenetrc':False}) as ydl:
            info = ydl.extract_info(os.environ['VORTEX_PROBE_URL'],download=False)
    if isinstance(info,dict):
        report = {'status':'metadata_returned','id':str(info.get('id',''))[:32],
                  'duration':info.get('duration'),'isLive':bool(info.get('is_live')),
                  'hasDRM':bool(info.get('has_drm')),'formats':len(info.get('formats') or []),
                  'filesize':info.get('filesize'),'filesizeApprox':info.get('filesize_approx'),
                  'type':info.get('_type','video')}
except Exception as error:
    text = str(error).lower()
    report['category'] = ('rate_limited' if any(x in text for x in ('429','rate limit','rate-limit')) else
                          'authentication' if any(x in text for x in ('login','sign in','private')) else
                          'unavailable')
print(json.dumps(report))
'''


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
    environment = {k: v for k, v in os.environ.items()
                   if not any(s in k.upper() for s in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY', 'CREDENTIAL'))}
    with tempfile.TemporaryDirectory(prefix='vortex-public-probe-') as temporary:
        root = Path(temporary)
        directory = root / 'job'
        services = {}
        if args.allow_public_fxembed and provider == 'twitter':
            services['fxembed'] = {'enabled': True, 'url': 'https://api.fxtwitter.com/', 'allowExternal': True}
        specification = dict(input=value, kind='download', quality='balanced', directory=str(directory),
            maxBytes=2 * 1024**3, maxDuration=7200, maxItems=50, ffmpeg=ffmpeg,
            services=services, downloadMode='video', videoFormat='mp4', audioFormat='m4a')
        path = root / 'request.json'
        path.write_text(json.dumps(specification), encoding='utf-8')
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
                digest = hashlib.sha256()
                with artifact.open('rb') as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        digest.update(chunk)
                report.update(status='downloaded_and_verified', verified=True, bytes=artifact.stat().st_size,
                              sha256=digest.hexdigest())
                report['media'] = {k: final.get('media', {}).get(k) for k in ('duration', 'width', 'height', 'mediaType', 'ext')}
        if report['status'] == 'not_run':
            report['status'] = 'no_verified_download'
        if not report['verified']:
            try:
                diagnostic = subprocess.run([sys.executable, '-c', METADATA_CHECK, str(ROOT / 'vision-pc')],
                    cwd=root, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=60, text=True, check=True)
                report['metadataCheck'] = json.loads(diagnostic.stdout)
            except (subprocess.SubprocessError, ValueError):
                report['metadataCheck'] = {'status': 'unavailable'}
    print('VORTEX_LIVE_REPORT=' + json.dumps(report, sort_keys=True))
    return 0 if report['verified'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
