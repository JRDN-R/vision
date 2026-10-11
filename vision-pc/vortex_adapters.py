"""Isolated extraction adapters, including independent public-media fallbacks.

No adapter collects browser cookies or enables a third-party API implicitly.
All downloads retain the worker's public-network guard and local verification.
"""
from dataclasses import dataclass
from html.parser import HTMLParser
import importlib.util
from itertools import islice
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import parse_qsl, urljoin, urlsplit

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
        if self.name in ('yt-dlp', 'cobalt', 'page-media'):
            return name != 'spotify' and (self.name == 'yt-dlp' or value.startswith(('https://', 'http://')))
        if self.name == 'gallery-dl':
            from vortex_worker import is_gallery
            return name in self.platforms or is_gallery(value)
        if self.name == 'instaloader':
            return instagram_shortcode(value) is not None
        return name in self.platforms

    def configured(self, request):
        if self.name in ('instagram-direct', 'page-media'):
            return True
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
        import requests
        import vortex_worker as worker
        try:
            if self.name == 'instagram-direct':
                return run_instagram_direct(request)
            if self.name == 'yt-dlp':
                return worker.run_ytdlp(request)
            if self.name.startswith('yt-dlp-twitter-'):
                return run_twitter_api(request, self.name.removeprefix('yt-dlp-twitter-'))
            if self.name == 'gallery-dl':
                return worker.run_gallery(request)
            if self.name == 'spotdl':
                return worker.run_spotify(request)
            if self.name == 'fxembed':
                return run_fxembed(request)
            if self.name == 'cobalt':
                return run_cobalt(request)
            if self.name == 'instaloader':
                return run_instaloader(request)
            if self.name == 'page-media':
                return run_page_media(request)
            raise ValueError('adapter_unavailable')
        except worker.WorkerError:
            raise
        except requests.HTTPError as error:
            status = getattr(error.response, 'status_code', None)
            safe = ('The provider is limiting requests.' if status == 429 else
                    'The provider rejected the request; a signed-in session may be needed.' if status in (401, 403) else
                    'The provider connection failed.' if status and status >= 500 else
                    'The provider did not return this media.')
            raise worker.SourceError(safe) from None
        except (requests.RequestException, TimeoutError, ConnectionError):
            raise worker.SourceError('The provider connection failed.') from None
        except ValueError as error:
            # Only authored categories cross the process boundary, never URLs,
            # service error bodies, headers, or upstream exception strings.
            code = str(error)
            safe = ('The provider is limiting requests.' if code == 'service_rate_limited' else
                    'The provider rejected the request; a signed-in session may be needed.' if code == 'service_authentication' else
                    'The provider connection failed.' if code == 'service_network' else
                    'The source returned a different media item.' if code == 'identity_mismatch' else
                    'This operation exceeded its media limit.' if code in ('size_limit', 'duration_limit') else
                    'The media could not be verified.' if code in ('not_video', 'invalid_media', 'incomplete_download') else
                    'No unambiguous playable media was returned.')
            raise worker.SourceError(safe) from None


# Native recipes are independent attempts, not aliases for a single default
# extraction. Services still require explicit administrator-owned configuration.
ADAPTERS = (
    Adapter('yt-dlp', 'yt_dlp'),
    Adapter('gallery-dl', 'gallery_dl', ('twitter', 'instagram', 'reddit')),
    Adapter('instagram-direct'),
    Adapter('cobalt', service=True),
    Adapter('yt-dlp-twitter-syndication', 'yt_dlp', ('twitter',)),
    Adapter('yt-dlp-twitter-legacy', 'yt_dlp', ('twitter',)),
    Adapter('instaloader', 'instaloader', ('instagram',)),
    Adapter('page-media'),
    Adapter('fxembed', platforms=('twitter',), service=True),
    Adapter('spotdl', 'spotdl', ('spotify',)),
)


def selection(value):
    """Return the requested attachment number without conflating photos/videos."""
    match = re.search(r'/(video|photo)/(\d+)/?$', urlsplit(value).path)
    if platform(value) == 'twitter' and match:
        return match[1], int(match[2])
    if platform(value) == 'instagram':
        values = [v for k, v in parse_qsl(urlsplit(value).query) if k == 'img_index']
        if values:
            if len(values) != 1 or not re.fullmatch(r'[1-9]\d{0,2}', values[0]):
                raise ValueError('ambiguous_media')
            return 'video', int(values[0])
    return None, None


def instagram_shortcode(value):
    if platform(value) != 'instagram':
        return None
    match = re.fullmatch(r'/(?:[A-Za-z0-9_.]+/)?(?:p|reels?|tv)/([A-Za-z0-9_-]{1,64})/?', urlsplit(value).path)
    return match[1] if match else None


def run_twitter_api(request, api):
    """Select a documented yt-dlp recipe inside this disposable process only.

    Reuse the complete existing worker rather than duplicate its format rules,
    URL identity checks, size limits, native transfer and FFmpeg protections.
    The original class is restored even when extraction fails.
    """
    import yt_dlp
    import vortex_worker as worker
    if api not in ('syndication', 'legacy') or platform(request['input']) != 'twitter':
        raise ValueError('unsupported')
    original = yt_dlp.YoutubeDL

    class TwitterProfile(original):
        def __init__(self, params=None, *args, **kwargs):
            params = dict(params or {})
            params['extractor_args'] = {'twitter': {'api': [api]}}
            super().__init__(params, *args, **kwargs)

    yt_dlp.YoutubeDL = TwitterProfile
    try:
        return worker.run_ytdlp(request, engine='yt-dlp-twitter-' + api)
    finally:
        yt_dlp.YoutubeDL = original


def select_stream(streams, quality):
    from vortex_worker import number
    streams = [s for s in streams if isinstance(s, dict) and isinstance(s.get('url'), str)]
    if not streams:
        raise ValueError('no_stream')
    key = lambda s: (number(s.get('height')) or 0, number(s.get('bitrate')) or 0)
    cap = {'small': 480, 'balanced': 1080}.get(quality)
    below = [s for s in streams if number(s.get('height')) and number(s.get('height')) <= cap] if cap else []
    return max(below or streams, key=key)


def choose_item(items, value, *, kind_key='type'):
    """Only select an explicit attachment, or one unambiguous video."""
    kind, index = selection(value)
    if kind == 'photo':
        raise ValueError('not_video')
    if index is not None:
        if not 1 <= index <= len(items):
            raise ValueError('ambiguous_media')
        chosen = items[index - 1]
        if chosen.get(kind_key) not in ('video', 'gif', 'animated_gif'):
            raise ValueError('not_video')
        return chosen
    videos = [x for x in items if x.get(kind_key) in ('video', 'gif', 'animated_gif')]
    if len(videos) != 1:
        raise ValueError('ambiguous_media')
    return videos[0]


def fx_streams(payload, value):
    status = payload.get('status') or payload.get('tweet') or {}
    if payload.get('code') != 200 or str(status.get('id')) != identity(value)[1]:
        raise ValueError('identity_mismatch')
    media = status.get('media') or {}
    # Prefer the full attachment order when available (it can contain photos).
    items = media.get('all') or [dict(x, type=x.get('type', 'video')) for x in media.get('videos') or []]
    video = choose_item(items, value)
    streams = []
    for item in video.get('formats') or []:
        if item.get('container') not in ('mp4', None):
            continue
        url = item.get('url') or ''
        if urlsplit(url).hostname != 'video.twimg.com' or not urlsplit(url).path.endswith('.mp4'):
            continue
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


def guarded_get(session, value):
    """Validate every redirect before making a request; the socket guard pins DNS."""
    from vortex_network import validate_input
    for _ in range(6):
        value = validate_input(value, 'download')
        response = session.get(value, allow_redirects=False, stream=True, timeout=(10, 25))
        if response.status_code not in (301, 302, 303, 307, 308):
            try:
                response.raise_for_status()
            except Exception:
                response.close()
                raise
            return response, value
        location = response.headers.get('Location')
        response.close()
        if not location:
            raise ValueError('invalid_media')
        value = urljoin(value, location)
    raise ValueError('redirect_limit')


def copy_stream(response, artifact, limit, *, tunnel=False):
    import vortex_worker as worker
    header = response.getheader if tunnel else response.headers.get
    mime = (header('Content-Type') or '').split(';', 1)[0].lower()
    if mime in ('text/html', 'application/xhtml+xml', 'application/json'):
        raise ValueError('not_video')
    expected = worker.number(header('Content-Length')) if not header('Content-Encoding') else None
    if expected and expected > limit:
        raise ValueError('size_limit')
    chunks = iter(lambda: response.read(256 * 1024), b'') if tunnel else response.iter_content(256 * 1024)
    total = 0
    try:
        with artifact.open('xb') as output:
            for chunk in chunks:
                total += len(chunk)
                if total > limit:
                    raise ValueError('size_limit')
                output.write(chunk)
                worker.emit(phase='Downloading media', progress=min(100, total / expected * 100) if expected else None)
        if not total or (expected and total != expected):
            raise ValueError('incomplete_download')
        return artifact
    except Exception:
        artifact.unlink(missing_ok=True)
        raise


def download_stream(url, request, client=None, *, stem='source'):
    import requests
    if not re.fullmatch(r'[a-z0-9-]{1,32}', stem):
        raise ValueError('invalid_media')
    artifact = Path(request['directory']) / (stem + '.mp4')
    session, connection, response = None, None, None
    try:
        if client is not None:
            connection, response = client.tunnel(url)
        else:
            session = requests.Session()
            session.trust_env = False
            response, _ = guarded_get(session, url)
        return copy_stream(response, artifact, int(request['maxBytes']), tunnel=client is not None)
    finally:
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()
        if session is not None:
            session.close()


def finish_artifact(artifact, media, request):
    import vortex_worker as worker
    probe = worker.probe_file(artifact, request.get('ffmpeg'), Path(request['directory']))
    streams = probe.get('streams') or []
    video = any(s.get('codec_type') == 'video' and not (s.get('disposition') or {}).get('attached_pic') for s in streams)
    audio = any(s.get('codec_type') == 'audio' for s in streams)
    if media.get('mediaType') == 'unknown':
        media['mediaType'] = 'video' if video else 'audio' if audio else 'unknown'
    audio_only = request.get('downloadMode') == 'audio' or media.get('mediaType') == 'audio'
    if not (audio if audio_only else video):
        raise ValueError('not_video')
    duration = worker.number((probe.get('format') or {}).get('duration'))
    if not duration or duration > int(request.get('maxDuration', 7200)):
        raise ValueError('duration_limit')
    worker.apply_probe(media, probe)
    artifact = worker.export_media(artifact, media, request)
    return dict(complete=True, media=media, results=[media], filename=artifact.name)


def finish_stream(url, media, request, client=None):
    return finish_artifact(download_stream(url, request, client), media, request)


def run_fxembed(request):
    import vortex_worker as worker
    client = ServiceClient(request['services']['fxembed'])
    post_id = identity(request['input'])[1]
    if not post_id:
        raise ValueError('ambiguous_media')
    # Do not require the OpenAPI endpoint to work before trying the media API.
    status, video, streams = fx_streams(client.json('/2/status/' + post_id), request['input'])
    stream = select_stream(streams, request.get('quality', 'balanced'))
    media = dict(title=worker.text(status.get('text') or 'X video'), url=request['input'], source='X',
                 thumbnail=worker.safe_url(video.get('thumbnail_url')), duration=worker.number(video.get('duration')),
                 mediaType='video', width=stream.get('width'), height=stream.get('height'), quality=request.get('quality'),
                 engine='fxembed', ext='mp4')
    # Probe a real bounded file for inspection too, rather than trust metadata.
    return finish_stream(stream['url'], media, request)


def cobalt_client_for(url, client):
    parsed = urlsplit(url)
    return client if (parsed.scheme, parsed.netloc) == (client.parsed.scheme, client.parsed.netloc) else None


def run_cobalt(request):
    import vortex_worker as worker
    client = ServiceClient(request['services']['cobalt'])
    health = client.json()
    if not isinstance((health.get('cobalt') or {}).get('services'), list):
        raise ValueError('service_schema')
    # Cobalt owns its current provider registry. A stale Python list must not
    # prevent it trying another supported platform such as Vimeo or Bluesky.
    body = dict(url=request['input'], videoQuality={'small': '480', 'balanced': '1080'}.get(request.get('quality'), 'max'),
                audioFormat='best', downloadMode='audio' if request.get('downloadMode') == 'audio' else 'auto',
                convertGif=False, localProcessing='disabled',
                youtubeVideoCodec='av1' if request.get('quality') == 'max' else 'h264', youtubeVideoContainer='auto')
    result = client.json(body=body)
    status = result.get('status')
    if status == 'error':
        raise ValueError('service_unavailable')
    name = result.get('filename') or (result.get('output') or {}).get('filename') or 'Media'
    media = dict(title=worker.text(Path(name).stem), url=request['input'], source=platform(request['input']),
                 mediaType='unknown', quality=request.get('quality'), engine='cobalt')
    if status in ('redirect', 'tunnel') and isinstance(result.get('url'), str):
        return finish_stream(result['url'], media, request, client if status == 'tunnel' else None)
    if status == 'picker':
        items = result.get('picker')
        if not isinstance(items, list) or not 1 <= len(items) <= int(request.get('maxItems', 50)) or any(not isinstance(i, dict) for i in items):
            raise ValueError('ambiguous_media')
        item = choose_item(items, request['input'])
        if not isinstance(item.get('url'), str):
            raise ValueError('invalid_media')
        return finish_stream(item['url'], media, request, cobalt_client_for(item['url'], client))
    if status == 'local-processing':
        return cobalt_local(result, media, request, client)
    raise ValueError('service_no_single_stream')


def cobalt_local(result, media, request, client):
    """Handle finite progressive tunnels; never pass remote URLs to FFmpeg."""
    import vortex_worker as worker
    mode = result.get('type')
    tunnels = result.get('tunnel')
    if (result.get('isHLS') or mode not in ('merge', 'remux', 'audio', 'mute') or
            not isinstance(tunnels, list) or len(tunnels) != (2 if mode == 'merge' else 1) or
            any(not isinstance(t, str) for t in tunnels) or (result.get('output') or {}).get('subtitles') or
            (result.get('audio') or {}).get('cover')):
        raise ValueError('unsupported_local_processing')
    directory = Path(request['directory'])
    files, details, remaining = [], [], int(request['maxBytes'])
    try:
        for i, url in enumerate(tunnels):
            # Each URL must be exactly this configured service's /tunnel.
            artifact = download_stream(url, dict(request, maxBytes=remaining), client, stem='part-' + str(i))
            files.append(artifact)
            remaining -= artifact.stat().st_size
            details.append(worker.probe_file(artifact, request.get('ffmpeg'), directory))
        if mode != 'merge':
            if mode == 'audio':
                media['mediaType'] = 'audio'
            return finish_artifact(files[0], media, request)
        video = [i for i, d in enumerate(details) if any(s.get('codec_type') == 'video' and not (s.get('disposition') or {}).get('attached_pic') for s in d.get('streams', []))]
        audio = [i for i, d in enumerate(details) if any(s.get('codec_type') == 'audio' for s in d.get('streams', []))]
        if len(video) != 1 or not any(i != video[0] for i in audio):
            raise ValueError('not_video')
        audio_index = next(i for i in audio if i != video[0])
        durations = []
        for data in details:
            duration = worker.number((data.get('format') or {}).get('duration'))
            if not duration or duration > int(request.get('maxDuration', 7200)):
                raise ValueError('duration_limit')
            durations.append(duration)
        if max(durations) - min(durations) > max(2, max(durations) * .02):
            raise ValueError('incomplete_download')
        merged = directory / 'merged.mkv'
        flags = ['-protocol_whitelist', 'file,pipe', '-format_whitelist', worker.MEDIA_DEMUXERS]
        command = [request['ffmpeg'], '-v', 'error', '-nostdin', '-y', '-threads', '2',
                   *flags, '-i', str(files[video[0]]), *flags, '-i', str(files[audio_index]),
                   '-map', '0:V:0', '-map', '1:a:0', '-c', 'copy', '-fs', str(request['maxBytes']), str(merged)]
        subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True, timeout=600, **worker.SUBPROCESS_FLAGS)
        if merged.stat().st_size >= int(request['maxBytes']):
            raise ValueError('size_limit')
        media['mediaType'] = 'video'
        return finish_artifact(merged, media, request)
    finally:
        for path in files:
            path.unlink(missing_ok=True)


def run_instaloader(request):
    import instaloader
    import vortex_worker as worker
    code = instagram_shortcode(request['input'])
    if not code:
        raise ValueError('unsupported')
    loader = instaloader.Instaloader(quiet=True, download_pictures=False, download_videos=False,
        download_video_thumbnails=False, save_metadata=False, request_timeout=15, max_connection_attempts=1)
    try:
        post = instaloader.Post.from_shortcode(loader.context, code)
        if post.shortcode != code:
            raise ValueError('identity_mismatch')
        if post.typename == 'GraphSidecar':
            nodes = list(islice(post.get_sidecar_nodes(), int(request.get('maxItems', 50)) + 1))
            if len(nodes) > int(request.get('maxItems', 50)):
                raise ValueError('size_limit')
            items = [dict(type='video' if n.is_video else 'photo', url=n.video_url if n.is_video else None) for n in nodes]
        else:
            items = [dict(type='video' if post.is_video else 'photo', url=post.video_url if post.is_video else None)]
        chosen = choose_item(items, request['input'])
        media = dict(title=worker.text(post.caption or 'Instagram video'), url=request['input'], source='Instagram',
                     mediaType='video', quality=request.get('quality'), engine='instaloader')
        return finish_stream(chosen['url'], media, request)
    except instaloader.exceptions.InstaloaderException as error:
        raise worker.SourceError(worker.source_error_message(request['input'], error)) from None
    finally:
        loader.close()


class MediaPage(HTMLParser):
    """Only explicit page media declarations, not regexes over recommendations."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls, self.canonicals, self.title = [], [], ''
        self.players = 0
        self.in_player = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            key, value = (attrs.get('property') or attrs.get('name') or '').lower(), attrs.get('content', '')
            if key in ('og:video', 'og:video:url', 'og:video:secure_url', 'twitter:player:stream', 'og:audio', 'og:audio:secure_url'):
                self.urls.append(value)
            elif key == 'og:url':
                self.canonicals.append(value)
            elif key == 'og:title':
                self.title = value
        elif tag == 'link' and attrs.get('rel', '').lower() == 'canonical':
            self.canonicals.append(attrs.get('href', ''))
        elif tag in ('video', 'audio'):
            self.players += 1
            self.in_player = True
            if attrs.get('src'):
                self.urls.append(attrs['src'])
        elif tag == 'source' and self.in_player and attrs.get('src'):
            self.urls.append(attrs['src'])
        if len(self.urls) > 32 or len(self.canonicals) > 8:
            raise ValueError('ambiguous_media')

    def handle_endtag(self, tag):
        if tag in ('video', 'audio'):
            self.in_player = False


def page_identity(value):
    code = instagram_shortcode(value)
    return ('instagram', code) if code else identity(value)


def run_page_media(request):
    import requests
    import vortex_worker as worker
    session = requests.Session()
    session.trust_env = False
    response = None
    value = request['input']
    media = dict(title='Media', url=value, source=platform(value), mediaType='unknown',
                 quality=request.get('quality'), engine='page-media')
    try:
        response, final_url = guarded_get(session, value)
        mime = response.headers.get('Content-Type', '').split(';', 1)[0].lower()
        if mime.startswith(('video/', 'audio/')) or mime in ('application/octet-stream', 'binary/octet-stream'):
            artifact = Path(request['directory']) / 'source.mp4'
            copy_stream(response, artifact, int(request['maxBytes']))
            response.close()
            response = None
            return finish_artifact(artifact, media, request)
        if mime not in ('text/html', 'application/xhtml+xml'):
            raise ValueError('unsupported')
        raw = bytearray()
        for chunk in response.iter_content(65536):
            raw.extend(chunk)
            if len(raw) > 1024 * 1024:
                raise ValueError('page_limit')
        page = MediaPage()
        page.feed(raw.decode('utf-8', errors='replace'))
        response.close()
        response = None
        kind, index = selection(value)
        if kind == 'photo' or (index is not None and index != 1) or page.players > 1:
            raise ValueError('ambiguous_media')
        # Known social platforms must affirm the exact original post, not a
        # login page, profile, recommendation, quote or unrelated redirect.
        known = platform(value) != 'generic'
        canonical = [urljoin(final_url, u) for u in page.canonicals if u]
        if known and (not canonical or any(page_identity(u) != page_identity(value) for u in canonical)):
            raise ValueError('identity_mismatch')
        urls = list(dict.fromkeys(urljoin(final_url, u) for u in page.urls if u))
        if len(urls) != 1:
            raise ValueError('ambiguous_media')
        media['title'] = worker.text(page.title or 'Media')
        return finish_stream(urls[0], media, request)
    finally:
        if response is not None:
            response.close()
        session.close()


def run_instagram_direct(request):
    import vortex_worker as worker
    import requests
    direct = instagram_signed_source(request['input'])
    if not direct:
        raise ValueError('unsupported_instagram_media')
    media = dict(title='Instagram video', url=request['input'], source='Instagram',
                 mediaType='video', quality=request.get('quality', 'balanced'), engine='instagram-direct', ext='mp4')
    targets = [direct] if request['input'] == direct else [direct, request['input']]
    worker.emit(phase='Reading media details', progress=None)
    for index, target in enumerate(targets):
        try:
            return finish_stream(target, dict(media), request)
        except (requests.RequestException, ValueError, worker.WorkerError) as error:
            if str(error) in ('size_limit', 'duration_limit') or (isinstance(error, worker.WorkerError) and 'limit' in str(error).lower()):
                raise
            (Path(request['directory']) / 'source.mp4').unlink(missing_ok=True)
            if index == len(targets) - 1:
                raise
