"""Small extraction adapter registry. Every invocation runs in its own process.

The orchestrator owns deadlines/cancellation, including descendants. Adapters
own compatibility, health, provider identity and best-source selection. Existing
native adapters and new stream adapters all use the same local FFmpeg export.
"""
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import re
from urllib.parse import urlsplit

from vortex_services import ServiceClient, service_config
from vortex_urls import identity, platform, instagram_signed_source


@dataclass(frozen=True)
class Adapter:
    name: str
    module: str = ''
    platforms: tuple = ()
    service: bool = False

    def compatible(self, value):
        name = platform(value)
        if self.name == 'instagram-direct':
            return instagram_signed_source(value) is not None
        if self.name == 'yt-dlp':
            return name != 'spotify'
        if self.name == 'gallery-dl':
            from vortex_worker import is_gallery
            return name in self.platforms or is_gallery(value)
        return name in self.platforms

    def configured(self, request):
        if self.name == 'instagram-direct':
            return True  # Uses the existing guarded requests/FFmpeg stack.
        if self.module:
            try:
                return importlib.util.find_spec(self.module) is not None
            except (ImportError, ValueError):
                return False
        try:
            service_config((request.get('services') or {}).get(self.name))
            return True
        except (ValueError, TypeError):
            return False

    def run(self, request):
        import vortex_worker as worker
        if self.name == 'instagram-direct':
            return run_instagram_direct(request)
        if self.name == 'yt-dlp':
            return worker.run_ytdlp(request)
        if self.name == 'gallery-dl':
            return worker.run_gallery(request)
        if self.name == 'spotdl':
            return worker.run_spotify(request)
        if self.name == 'fxembed':
            return run_fxembed(request)
        if self.name == 'cobalt':
            return run_cobalt(request)
        raise ValueError('adapter_unavailable')


ADAPTERS = (
    Adapter('yt-dlp', 'yt_dlp'),
    Adapter('gallery-dl', 'gallery_dl', ('twitter', 'instagram', 'reddit')),
    Adapter('instagram-direct'),
    Adapter('spotdl', 'spotdl', ('spotify',)),
    Adapter('cobalt', platforms=('youtube', 'twitter', 'instagram', 'reddit', 'facebook', 'tiktok'), service=True),
    Adapter('fxembed', platforms=('twitter',), service=True),
)
# The upstream 2023 twitter-video-dl implementation cannot establish identity
# reliably (regex over entire thread; recursively follows first repost) and has
# no timeouts. Do not install it or claim a working adapter. See VORTEX.md.


def select_stream(streams, quality):
    from vortex_worker import number
    streams = [s for s in streams if isinstance(s, dict) and isinstance(s.get('url'), str)]
    if not streams:
        raise ValueError('no_stream')
    key = lambda s: (number(s.get('height')) or 0, number(s.get('bitrate')) or 0)
    cap = {'small': 480, 'balanced': 1080}.get(quality)
    below = [s for s in streams if s.get('height') and s['height'] <= cap] if cap else []
    # If no smaller stream exists, start at the best and downscale locally.
    return max(below or streams, key=key)


def fx_streams(payload, value):
    """Current v2, plus the documented v1 compatibility envelope.

    Never use quote/reply/external embeds or thumbnails as video candidates.
    """
    status = payload.get('status') or payload.get('tweet') or {}
    if payload.get('code') != 200 or str(status.get('id')) != identity(value)[1]:
        raise ValueError('identity_mismatch')
    videos = (status.get('media') or {}).get('videos') or []
    if len(videos) != 1:
        raise ValueError('ambiguous_media')
    video = videos[0]
    streams = []
    for item in video.get('formats') or []:
        if item.get('container') not in ('mp4', None):
            continue
        url = item.get('url') or ''
        if urlsplit(url).hostname != 'video.twimg.com' or not urlsplit(url).path.endswith('.mp4'):
            continue
        # FxEmbed v2 formats may omit dimensions; X embeds them in the path.
        dims = re.search(r'/(\d+)x(\d+)/', urlsplit(url).path)
        streams.append(dict(item, width=item.get('width') or (int(dims[1]) if dims else video.get('width')),
                            height=item.get('height') or (int(dims[2]) if dims else video.get('height'))))
    if not streams:
        url = video.get('url') or ''
        if urlsplit(url).hostname == 'video.twimg.com' and urlsplit(url).path.endswith('.mp4'):
            streams = [dict(video, url=url)]
    if not streams:
        raise ValueError('no_stream')
    return status, video, streams


def download_stream(url, request, client=None):
    import vortex_worker as worker
    from vortex_network import validate_input
    import requests
    directory = Path(request['directory'])
    artifact = directory / 'source.mp4'
    limit = int(request['maxBytes'])
    connection = None
    if client is not None:
        connection, response = client.tunnel(url)
        expected = worker.number(response.getheader('Content-Length'))
        chunks = iter(lambda: response.read(256 * 1024), b'')
    else:
        validate_input(url, 'download')
        session = requests.Session()
        session.trust_env = False
        response = session.get(url, stream=True, timeout=(10, 25))
        response.raise_for_status()
        expected = worker.number(response.headers.get('Content-Length')) if not response.headers.get('Content-Encoding') else None
        chunks = response.iter_content(256 * 1024)
    try:
        if expected and expected > limit:
            raise ValueError('size_limit')
        total = 0
        with artifact.open('xb') as output:
            for chunk in chunks:
                total += len(chunk)
                if total > limit:
                    raise ValueError('size_limit')
                output.write(chunk)
                # No signed URLs, headers or synthetic percentages in events.
                worker.emit(phase='Downloading media', progress=min(100, total / expected * 100) if expected else None)
        if not total or (expected and total != expected):
            raise ValueError('incomplete_download')
        return artifact
    finally:
        response.close()
        if connection:
            connection.close()


def finish_stream(url, media, request, client=None):
    import vortex_worker as worker
    artifact = download_stream(url, request, client)
    probe = worker.probe_file(artifact, request.get('ffmpeg'), Path(request['directory']))
    audio_only = request.get('downloadMode') == 'audio' or media.get('mediaType') == 'audio'
    if not any(s.get('codec_type') == ('audio' if audio_only else 'video') and not (s.get('disposition') or {}).get('attached_pic') for s in probe.get('streams', [])):
        raise ValueError('not_video')
    worker.apply_probe(media, probe)
    artifact = worker.export_media(artifact, media, request)
    return dict(complete=True, media=media, results=[media], filename=artifact.name)


def run_fxembed(request):
    import vortex_worker as worker
    client = ServiceClient(request['services']['fxembed'])
    post_id = identity(request['input'])[1]
    if not post_id or '/video/' in urlsplit(request['input']).path or '/photo/' in urlsplit(request['input']).path:
        raise ValueError('ambiguous_media')
    # OpenAPI is a URL-free health check; an incompatible instance is excluded.
    spec = client.json('/2/openapi.json')
    if not isinstance(spec.get('paths'), dict) or '/2/status/{id}' not in spec['paths']:
        raise ValueError('service_schema')
    status, video, streams = fx_streams(client.json('/2/status/' + post_id), request['input'])
    stream = select_stream(streams, request.get('quality', 'balanced'))
    media = dict(title=worker.text(status.get('text') or 'X video'), url=request['input'], source='X',
                 thumbnail=worker.safe_url(video.get('thumbnail_url')), duration=worker.number(video.get('duration')),
                 mediaType='video', width=stream.get('width'), height=stream.get('height'), quality=request.get('quality'),
                 engine='fxembed', ext='mp4')
    if request['kind'] == 'inspect':
        # Verify availability, using guarded requests rather than a service proxy.
        import requests
        from vortex_network import validate_input
        validate_input(stream['url'], 'download')
        with requests.get(stream['url'], headers={'Range': 'bytes=0-65535'}, stream=True, timeout=(5, 10)) as response:
            response.raise_for_status()
            first = next(response.iter_content(65536), b'')
            if b'ftyp' not in first[:64]:
                raise ValueError('not_video')
        return dict(complete=True, media=media, results=[media])
    return finish_stream(stream['url'], media, request)


def run_cobalt(request):
    import vortex_worker as worker
    client = ServiceClient(request['services']['cobalt'])
    service = platform(request['input'])
    health = client.json()
    if service not in (health.get('cobalt') or {}).get('services', []):
        raise ValueError('service_unsupported')
    body = dict(url=request['input'], videoQuality={'small': '480', 'balanced': '1080'}.get(request.get('quality'), 'max'),
                audioFormat='best', downloadMode='auto', convertGif=False, localProcessing='disabled',
                youtubeVideoCodec='av1' if request.get('quality') == 'max' else 'h264', youtubeVideoContainer='auto')
    result = client.json(body=body)
    if result.get('status') not in ('redirect', 'tunnel') or not isinstance(result.get('url'), str):
        # A picker is not permission to silently choose the first of many items.
        raise ValueError('service_no_single_stream')
    media = dict(title=worker.text(Path(result.get('filename') or 'Media').stem), url=request['input'],
                 source=service, mediaType='audio' if urlsplit(request['input']).hostname == 'music.youtube.com' else 'video',
                 quality=request.get('quality'), engine='cobalt')
    # Cobalt has no inspection metadata API. A real bounded download + probe is
    # required for inspection too; its file is reusable by the download race.
    return finish_stream(result['url'], media, request, client if result['status'] == 'tunnel' else None)



def run_instagram_direct(request):
    """Download an explicitly supplied signed Instagram MP4, with verified output.

    Direct CDN retrieval is attempted first. A VideoDropper download URL is an
    optional second route only if the user specifically pasted one. No Instagram
    post is ever submitted to a third-party service automatically.
    """
    import vortex_worker as worker
    import requests
    direct = instagram_signed_source(request['input'])
    if not direct:
        raise ValueError('unsupported_instagram_media')
    media = dict(title='Instagram video', url=request['input'], source='Instagram',
                 mediaType='video', quality=request.get('quality', 'balanced'),
                 engine='instagram-direct', ext='mp4')
    targets = [direct]
    if request['input'] != direct:
        targets.append(request['input'])
    worker.emit(phase='Reading media details', progress=None)
    for index, target in enumerate(targets):
        try:
            # finish_stream bounds bytes, probes codecs/duration and exports
            # locally. verify_result will also full-decode the final artifact.
            return finish_stream(target, dict(media), request)
        except (requests.RequestException, ValueError, worker.WorkerError) as error:
            # Do not retry a size/quota restriction through another host.
            if str(error) == 'size_limit' or (
                    isinstance(error, worker.WorkerError) and 'limit' in str(error).lower()):
                raise
            (Path(request['directory']) / 'source.mp4').unlink(missing_ok=True)
            if index == len(targets) - 1:
                raise

