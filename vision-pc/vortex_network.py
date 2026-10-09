"""Public-network boundary for Vortex's isolated extractor processes.

Installed in the worker, never in Flask or another Vision processor. Both DNS
answers and the actual socket destination are checked, including redirects and
DNS rebinding. Extractors receive no browser cookies or server credentials.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from urllib.parse import urlsplit, urlunsplit


class UnsafeDestination(ValueError):
    pass


def public_address(value):
    try:
        address = ipaddress.ip_address(str(value).split('%')[0])
        if getattr(address, 'ipv4_mapped', None):
            address = address.ipv4_mapped
        # is_global alone accepts some multicast/reserved addresses on Python versions.
        return address.is_global and not (address.is_multicast or address.is_reserved or
                                          address.is_loopback or address.is_link_local or
                                          address.is_unspecified)
    except ValueError:
        return False


def validate_host(host):
    if not isinstance(host, str) or not host or len(host) > 253:
        raise UnsafeDestination('Use a public media website.')
    host = host.rstrip('.').lower()
    if (any(c in host for c in ('%', '\\', '\x00')) or
            host in ('localhost', 'metadata.google.internal') or
            host.endswith(('.localhost', '.local', '.internal', '.ts.net'))):
        raise UnsafeDestination('Local and private network addresses are unavailable.')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r'[a-z0-9.-]+', host) or '.' not in host:
            raise UnsafeDestination('Use a public media website.')
    else:
        if not public_address(address):
            raise UnsafeDestination('Local and private network addresses are unavailable.')
    return host


def resolve_public(host, port, resolver=None, family=0, socktype=socket.SOCK_STREAM, proto=0, flags=0):
    host = validate_host(host.decode('ascii') if isinstance(host, bytes) else host)
    try:
        port = int(port)
    except (ValueError, TypeError):
        raise UnsafeDestination('Only public HTTP and HTTPS media are supported.') from None
    if port not in (80, 443):
        raise UnsafeDestination('Only standard HTTP and HTTPS ports are supported.')
    try:
        answers = (resolver or socket.getaddrinfo)(host, port, family, socktype, proto, flags)
    except socket.gaierror:
        raise UnsafeDestination('The media website could not be reached. Check the link.') from None
    if not answers or any(not public_address(item[4][0]) for item in answers):
        raise UnsafeDestination('Local and private network addresses are unavailable.')
    return answers


def validate_input(value, kind='inspect', resolve=True):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 2048:
        raise ValueError('Paste a media link or enter a search up to 2,048 characters.')
    from vortex_urls import normalize_url
    value = normalize_url(value)
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or '\\' in value:
        raise ValueError('This link or search contains unsupported characters.')
    if value.lower().startswith(('www.', 'youtu.be/', 'youtube.com/', 'music.youtube.com/', 'instagram.com/', 'open.spotify.com/')):
        value = 'https://' + value
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in ('http', 'https'):
        if parsed.scheme and not (parsed.scheme.lower() == 'spotify' and kind == 'inspect' and not value.startswith('spotify://')):
            raise ValueError('Only HTTP and HTTPS media links are supported.')
        if kind == 'download':
            raise ValueError('Select a media result or paste its public link first.')
        if len(value) > 300:
            raise ValueError('Keep searches under 300 characters.')
        return value
    try:
        port = parsed.port
    except ValueError:
        raise ValueError('Use a valid media website link.') from None
    if parsed.username is not None or parsed.password is not None or not parsed.hostname:
        raise ValueError('Links containing credentials are unsupported.')
    if port not in (None, 80 if parsed.scheme.lower() == 'http' else 443):
        raise ValueError('Only standard HTTP and HTTPS ports are supported.')
    host = validate_host(parsed.hostname)
    if resolve:
        resolve_public(host, port or (443 if parsed.scheme.lower() == 'https' else 80))
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or '/', parsed.query, ''))


def is_instagram_reel(value):
    parsed = urlsplit(value)
    host = (parsed.hostname or '').lower()
    return (host == 'instagram.com' or host.endswith('.instagram.com')) and bool(
        re.fullmatch(r'/(?:[A-Za-z0-9_.]+/)?(?:reels?|tv)/[A-Za-z0-9_-]+/?', parsed.path))


def engine_for(value):
    if value.lower().startswith('spotify:'):
        return 'spotdl'
    host = (urlsplit(value).hostname or '').lower()
    if host in ('open.spotify.com', 'spotify.link', 'spoti.fi'):
        return 'spotdl'
    if is_instagram_reel(value):
        return 'yt-dlp'
    galleries = ('reddit.com', 'redd.it', 'imgur.com', 'flickr.com', 'deviantart.com', 'pixiv.net', 'instagram.com')
    if any(host == domain or host.endswith('.' + domain) for domain in galleries):
        return 'gallery-dl'
    return 'yt-dlp'


def install_network_guard():
    """Pin every outbound Python socket to a checked, globally routable address."""
    if getattr(socket, '_vortex_guard_installed', False):
        return
    original_resolve = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    for key in list(os.environ):
        if key.lower() in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
            os.environ.pop(key, None)

    def checked_resolve(host, port, family=0, type=0, proto=0, flags=0):
        return resolve_public(host, port, original_resolve, family, type, proto, flags)

    def checked_target(sock, address):
        if sock.family not in (socket.AF_INET, socket.AF_INET6) or not isinstance(address, tuple) or len(address) < 2:
            raise UnsafeDestination('Only public media network connections are supported.')
        answers = resolve_public(address[0], address[1], original_resolve, sock.family, sock.type, sock.proto)
        return answers[0][4]

    def checked_connect(sock, address):
        return original_connect(sock, checked_target(sock, address))

    def checked_connect_ex(sock, address):
        return original_connect_ex(sock, checked_target(sock, address))

    socket.getaddrinfo = checked_resolve
    socket.socket.connect = checked_connect
    socket.socket.connect_ex = checked_connect_ex
    socket._vortex_guard_installed = True
