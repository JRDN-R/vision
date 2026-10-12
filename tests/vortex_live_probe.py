"""Opt-in real public-source probe; never print media URLs, cookies or content.

Unit tests are not evidence of working providers. This separate probe uses the
production worker and normal 2 GiB/four-hour limits, then deletes all media.
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
import time

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vision-pc'))
from vortex_adapters import instagram_shortcode
from vortex_urls import identity, platform

# A separate bounded metadata-only check distinguishes a provider failure from
# a policy rejection. It does not weaken or replace the actual worker test.
METADATA_CHECK = r'''
import contextlib, importlib.metadata, json, os, re, sys
from pathlib import Path
from urllib.parse import urlparse
sys.path.insert(0, sys.argv[1])
from vortex_worker import error_category
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
            'proxy':'','cookiefile':None,'usenetrc':False,
             'js_runtimes': {'deno': {'path': os.environ['VORTEX_PROBE_DENO']}} if os.environ.get('VORTEX_PROBE_DENO') else {}}) as ydl:
            value = os.environ['VORTEX_PROBE_URL']
            try:
                info = ydl.extract_info(value,download=False)
            except yt_dlp.utils.DownloadError:
                parsed = urlparse(value)
                if parsed.hostname not in ('vimeo.com', 'www.vimeo.com') or not re.fullmatch(r'/\d+/?', parsed.path):
                    raise
                video_id = parsed.path.strip('/')
                info = ydl.extract_info('https://player.vimeo.com/video/' + video_id, download=False)
                if str(info.get('id')) != video_id:
                    raise ValueError('identity_mismatch')
    if isinstance(info,dict):
        report = {'status':'metadata_returned','mediaId':str(info.get('id',''))[:32],
                  'postId':str(info.get('display_id',''))[:32],
                  'duration':info.get('duration'),'isLive':bool(info.get('is_live')),
                  'hasDRM':bool(info.get('has_drm')),'formats':len(info.get('formats') or []),
                  'filesize':info.get('filesize'),'filesizeApprox':info.get('filesize_approx'),
                  'type':info.get('_type','video'),
                  'streamSummary': [{k:f.get(k) for k in ('format_id','height','tbr','filesize','filesize_approx','protocol')}
                                    for f in (info.get('formats') or [])[:40]]}
except Exception as error:
    report['category'] = error_category(error)
print(json.dumps(report))
'''

# A failed native transfer can be a programming defect rather than a provider
# restriction. Reproduce failed transfers with the actual guarded
# adapter, recording code locations and fixed exception names, never messages.
NATIVE_DIAGNOSTIC = r'''
import re, sys
from pathlib import Path
sys.path.insert(0, sys.argv.pop(1))
import vortex_worker as worker
original_category = worker.error_category
def categorize(error):
    category = original_category(error)
    if category in ('unavailable', 'authentication', 'access_denied', 'drm', 'network', 'unsupported'):
        names = {'WorkerError', 'SourceError', 'DownloadError', 'ExtractorError',
                 'KeyError', 'TypeError', 'AttributeError', 'ValueError',
                 'AssertionError', 'NameError', 'OSError', 'HTTPError'}
        files = {'vortex_worker.py', 'vortex_network.py', 'YoutubeDL.py', 'common.py', 'vimeo.py',
                 'hls.py', 'dash.py', 'http.py', 'fragment.py', 'external.py'}
        frames, trace = [], error.__traceback__
        while trace:
            filename = Path(trace.tb_frame.f_code.co_filename).name
            if filename in files:
                frames.append(filename + ':' + str(trace.tb_lineno))
            trace = trace.tb_next
        name = type(error).__name__
        chain, current = [], error
        while current is not None and len(chain) < 5:
            chain.append(str(current).lower())
            current = current.__cause__
        detail = ' '.join(chain)
        reason = next((code for pattern, code in (
            ('confirm you’re not a bot', 'bot_challenge'), ('confirm you are not a bot', 'bot_challenge'),
            ("confirm you're not a bot", 'bot_challenge'),
            ('http error 403', 'http_403'), ('403 forbidden', 'http_403'),
            ('http error 401', 'http_401'), ('http error 429', 'http_429'),
            ('initialization fragment found after media fragments', 'hls_initialization_layout'),
            ('drm protected', 'drm'), ('downloaded file is empty', 'empty_transfer'),
            ('did not get any data blocks', 'empty_transfer'),
            ('fragment', 'fragment_failure'), ('requested format', 'format_unavailable'))
            if pattern in detail), 'unclassified')
        worker.emit(probeFailure={'exception': name if name in names else 'Exception',
                                  'frames': frames[-6:], 'reason': reason})
    return category
worker.error_category = categorize
# Record only fixed DRM signals, never playlist/key URLs or response bodies.
from yt_dlp.downloader.hls import HlsFD
original_drm = HlsFD._has_drm
def observe_drm(manifest):
    result = original_drm(manifest)
    if result:
        signals = [code for marker, code in (
            ('skd://', 'fairplay'), ('com.apple.streamingkeydelivery', 'fairplay'),
            ('com.microsoft.playready', 'playready'), ('#EXT-X-FAXS-CM:', 'adobe'))
            if marker in manifest]
        worker.emit(probeFailure={'reason': 'hls_drm_signaling', 'signals': sorted(set(signals))})
    return result
HlsFD._has_drm = staticmethod(observe_drm)
raise SystemExit(worker.main())
'''


def native_diagnostic(specification, root):
    import contextlib
    import vortex_race
    from vortex_adapters import ADAPTERS
    wrapper = root / 'native-check.py'
    # Attempt normally invokes WORKER directly; this test-only wrapper inserts
    # the repository path and then runs the exact worker entry point.
    wrapper.write_text(NATIVE_DIAGNOSTIC.replace('sys.argv.pop(1)', repr(str(ROOT / 'vision-pc'))), encoding='utf-8')
    original_worker = vortex_race.WORKER
    attempt = None
    try:
        vortex_race.WORKER = wrapper
        adapter = next(a for a in ADAPTERS if a.name == 'yt-dlp')
        attempt = vortex_race.Attempt(adapter, dict(specification, downloadTimeout=180),
                                      root / 'native-diagnostic', 'download')
    finally:
        vortex_race.WORKER = original_worker
    try:
        with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink):
            while not attempt.poll():
                size = 0
                for path in attempt.directory.rglob('*'):
                    try:
                        if path.is_file() and not path.is_symlink():
                            size += path.stat().st_size
                    except FileNotFoundError:
                        pass
                if size > 3 * specification['maxBytes']:
                    attempt.category = 'disk_limit'
                    attempt.cancel()
                time.sleep(.1)
        errors = []
        for line in attempt.events.read_text(encoding='utf-8').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and isinstance(event.get('probeFailure'), dict):
                errors.append(event['probeFailure'])
        return {'category': attempt.category, 'errors': errors[:5]}
    finally:
        attempt.cancel()


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
    import importlib.metadata
    report['versions'] = {name: importlib.metadata.version(name) for name in ('yt-dlp', 'gallery-dl', 'instaloader', 'spotdl')}
    if provider == 'twitter':
        report['requestedId'] = identity(value)[1]
    elif provider == 'instagram':
        report['requestedId'] = instagram_shortcode(value)
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise SystemExit('FFmpeg is required for the live probe.')
    environment = {k: v for k, v in os.environ.items()
                   if not any(s in k.upper() for s in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY', 'CREDENTIAL'))}
    deno = shutil.which('deno')
    if deno:
        environment['VORTEX_PROBE_DENO'] = deno
    report['javascriptRuntime'] = 'deno' if deno else 'missing'
    with tempfile.TemporaryDirectory(prefix='vortex-public-probe-') as temporary:
        root = Path(temporary)
        directory = root / 'job'
        services = {}
        if args.allow_public_fxembed and provider == 'twitter':
            services['fxembed'] = {'enabled': True, 'url': 'https://api.fxtwitter.com/', 'allowExternal': True}
        specification = dict(input=value, kind='download', quality='balanced', directory=str(directory),
            maxBytes=2 * 1024**3, maxDuration=14400, maxItems=50, ffmpeg=ffmpeg, deno=deno,
            services=services, downloadMode='video', videoFormat='mp4', audioFormat='m4a')
        path = root / 'request.json'
        path.write_text(json.dumps(specification), encoding='utf-8')
        started = time.monotonic()
        process = subprocess.Popen([sys.executable, str(ROOT / 'vision-pc/vortex_worker.py'), '--request', str(path)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=environment, text=True)
        final = None
        try:
            # Include full-file decoding of long media. The former four-minute
            # probe timeout contradicted the production 45-minute job deadline.
            output, _ = process.communicate(timeout=45 * 60)
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
            if event.get('phase') in ('Finding media…', 'Preparing download…', 'Downloading media',
                                      'Converting to MP4', 'Verifying complete media'):
                report['lastPhase'] = event['phase']
            if 'error' in event:
                report['terminalCategory'] = event.get('category')
            if isinstance(event.get('diagnostic'), dict):
                data = event['diagnostic']
                report['attempts'].append({k: data[k] for k in ('engine', 'category', 'elapsedMs') if k in data})
            if event.get('complete') is True:
                final = event
        report['elapsedSeconds'] = round(time.monotonic() - started, 2)
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
        if not report['verified'] and any(a.get('engine') == 'yt-dlp' and a.get('category') in ('unavailable', 'authentication', 'access_denied', 'drm', 'network', 'unsupported')
                                          for a in report['attempts']):
            report['nativeDiagnostic'] = native_diagnostic(specification, root)
        if provider in ('twitter', 'instagram', 'youtube', 'generic'):
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
