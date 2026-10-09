"""Conservative, idempotent URL canonicalization. No network I/O here.

Unknown hosts and signed URLs are left intact. Redirect expansion belongs in
the guarded worker, not the HTTP request or the idempotency key calculation.
"""
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
    # Only repair a recognisable scheme delimiter, never guess an identifier.
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
    provider = platform(value)
    if provider == 'generic':
        return value
    # Preserve port syntax so validate_input can reject a nonstandard port.
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
        match = re.fullmatch(r'/([A-Za-z0-9_]+|i/web)/status/(\d+)(?:/(?:video|photo)/(\d+))?/?', path)
        host = 'x.com'
        if match:
            path = f'/{match[1]}/status/{match[2]}'
            # First media is implicit; preserve a non-first selection rather
            # than silently changing a user's requested media identity.
            if match[3] and match[3] != '1':
                path += '/video/' + match[3]
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
    """Provider-qualified identity, never a reply/quote's media ID."""
    parsed = urlsplit(normalize_url(value))
    name = platform(value)
    if name == 'youtube':
        return (name, next((v for k, v in parse_qsl(parsed.query) if k == 'v'), None))
    if name == 'twitter':
        match = re.search(r'/status/(\d+)', parsed.path)
        return (name, match[1] if match else None)
    return (name, parsed.path.rstrip('/'))


def resolve_shared_url(value, session=None):
    """Expand only known share routes, checking *each* redirect before fetching.

    Called only inside a worker with the socket guard installed. No cookies,
    Authorization or service API headers are attached to these requests.
    """
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
