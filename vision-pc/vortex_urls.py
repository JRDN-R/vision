"""Conservative URL canonicalization; signed query strings remain intact."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

HOSTS = {
    'youtube': {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be', 'www.youtu.be'},
    'twitter': {'x.com', 'www.x.com', 'mobile.x.com', 'twitter.com', 'www.twitter.com', 'mobile.twitter.com', 'm.twitter.com', 'fxtwitter.com', 'fixupx.com', 'vxtwitter.com'},
    'instagram': {'instagram.com', 'www.instagram.com', 'm.instagram.com'},
    'facebook': {'facebook.com', 'www.facebook.com', 'm.facebook.com', 'mbasic.facebook.com', 'fb.watch'},
    'tiktok': {'tiktok.com', 'www.tiktok.com', 'm.tiktok.com', 'vm.tiktok.com', 'vt.tiktok.com'},
    'reddit': {'reddit.com', 'www.reddit.com', 'old.reddit.com', 'new.reddit.com', 'm.reddit.com', 'redd.it'},
    'spotify': {'open.spotify.com', 'www.open.spotify.com', 'spotify.link', 'spoti.fi'},
}
SHORT_HOSTS = {'t.co', 'youtu.be', 'www.youtu.be', 'vm.tiktok.com', 'vt.tiktok.com', 'fb.watch', 'redd.it', 'spotify.link', 'spoti.fi'}
TRACKING = {'fbclid', 'gclid', 'igsh', 'igshid', 'cplk', 'si', 'feature', 'ref_src', 'ref_url', 's', 'share_id', 'share_app_id', 'is_from_webapp', 'sender_device', 'rdt'}


def is_instagram_cdn_video(value):
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 or c == '\\' for c in value):
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower().rstrip('.')
        return (parsed.scheme.lower() == 'https' and parsed.port in (None, 443)
                and parsed.username is None and parsed.password is None and not parsed.fragment
                and (host == 'scontent.cdninstagram.com'
                     or (host.startswith('scontent-') and host.endswith('.cdninstagram.com')))
                and parsed.path.lower().endswith('.mp4'))
    except ValueError:
        return False


def instagram_signed_source(value):
    """Decode only an explicitly supplied, tightly validated download wrapper."""
    if is_instagram_cdn_video(value):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() != 'https' or parsed.hostname != 'dl.videodropper.app'
                or parsed.port not in (None, 443) or parsed.path not in ('', '/')
                or parsed.username is not None or parsed.password is not None or parsed.fragment):
            return None
        fields = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=1)
        if len(fields) != 1 or fields[0][0] != 'url':
            return None
        direct = fields[0][1]
        return direct if is_instagram_cdn_video(direct) else None
    except ValueError:
        return None


def platform(value):
    if value.lower().startswith('spotify:'):
        return 'spotify'
    host = (urlsplit(value).hostname or '').lower().rstrip('.')
    return next((name for name, hosts in HOSTS.items() if host in hosts), 'generic')


def normalize_url(value):
    if not isinstance(value, str):
        return value
    value = value.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or '\\' in value:
        raise ValueError('This link contains unsupported characters.')
    value = re.sub(r'^(https?):/{1,}', r'\1://', value, flags=re.I)
    if value.startswith('//'):
        value = 'https:' + value
    elif re.match(r'^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d+)?(?:/|$)', value):
        value = 'https://' + value
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in ('http', 'https'):
        return value
    if parsed.username is not None or parsed.password is not None:
        raise ValueError('Links containing credentials are unsupported.')
    if (parsed.hostname or '').lower().rstrip('.') == 'dl.videodropper.app':
        if not instagram_signed_source(value):
            raise ValueError('Use a VideoDropper download link for a direct Instagram MP4.')
        return value
    provider = platform(value)
    if provider == 'generic':
        return value
    if parsed.port not in (None, 80 if parsed.scheme.lower() == 'http' else 443):
        return value
    host = (parsed.hostname or '').lower().rstrip('.')
    path = re.sub('/{2,}', '/', parsed.path or '/')
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k.lower() not in TRACKING and not k.lower().startswith('utm_')]
    if provider == 'youtube':
        match = re.fullmatch(r'/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})/?', path)
        short = re.fullmatch(r'/([A-Za-z0-9_-]{11})/?', path) if host.endswith('youtu.be') else None
        if match or short:
            path = '/watch'
            query = [('v', (match or short)[1]), *((k, v) for k, v in query if k != 'v')]
        host = 'music.youtube.com' if host == 'music.youtube.com' else 'www.youtube.com' if host not in {'youtu.be', 'www.youtu.be'} or short else host
        if parsed.fragment.startswith('t=') and not any(k == 't' for k, _ in query):
            query.append(('t', parsed.fragment[2:]))
    elif provider == 'twitter':
        match = re.fullmatch(r'/([A-Za-z0-9_]+|i/web)/status/(\d+)(?:/(video|photo)/(\d+))?/?', path)
        host = 'x.com'
        if match:
            path = f'/{match[1]}/status/{match[2]}'
            # /video/1 is meaningful to extractors. Retain both the selected
            # index and its kind; never turn /photo/2 into /video/2.
            if match[3]:
                path += '/' + match[3] + '/' + match[4]
            query = []
    elif provider == 'instagram':
        host = 'www.instagram.com'
        path = re.sub(r'^/reels/', '/reel/', path)
    elif provider == 'facebook' and host != 'fb.watch':
        host = 'www.facebook.com'
    elif provider == 'tiktok' and host not in SHORT_HOSTS:
        host = 'www.tiktok.com'
    elif provider == 'reddit' and host != 'redd.it':
        host = 'www.reddit.com'
    elif provider == 'spotify' and host not in SHORT_HOSTS:
        host = 'open.spotify.com'
        path = re.sub(r'^/intl-[a-z]{2}/', '/', path)
    return urlunsplit(('https', host, path, urlencode(query), ''))


def identity(value):
    parsed = urlsplit(normalize_url(value))
    name = platform(value)
    if name == 'youtube':
        return (name, next((v for k, v in parse_qsl(parsed.query) if k == 'v'), None))
    if name == 'twitter':
        match = re.search(r'/status/(\d+)', parsed.path)
        return (name, match[1] if match else None)
    return (name, parsed.path.rstrip('/'))


def resolve_shared_url(value, session=None):
    """Resolve known share routes, validating each redirect before fetching."""
    from vortex_network import validate_input
    from urllib.parse import urljoin
    value = validate_input(normalize_url(value), 'download')
    parsed = urlsplit(value)
    if (parsed.hostname not in SHORT_HOSTS and
            not (platform(value) in {'instagram', 'facebook', 'reddit'} and '/share/' in parsed.path) and
            not (platform(value) == 'reddit' and '/s/' in parsed.path)):
        return value
    if session is None:
        import requests
        session = requests.Session()
        session.trust_env = False
    for _ in range(6):
        with session.get(value, allow_redirects=False, stream=True, timeout=(5, 10)) as response:
            if response.status_code not in (301, 302, 303, 307, 308):
                response.raise_for_status()
                return normalize_url(value)
            value = validate_input(normalize_url(urljoin(value, response.headers.get('Location', ''))), 'download')
    raise ValueError('This shared link redirected too many times.')
