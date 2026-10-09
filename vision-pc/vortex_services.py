"""Explicit administrator-owned services. Never a default third-party API.

The only local-network exception is a dedicated connection to a fixed configured
loopback origin. It is not installed in requests, socket or any extractor.
Redirects, arbitrary local paths and credential forwarding are prohibited.
"""
import http.client
import json
import socket
import ssl
from urllib.parse import urlsplit

from vortex_network import resolve_public

_CONNECT = socket.socket.connect
_RESOLVE = socket.getaddrinfo
MAX_JSON = 1024 * 1024


def service_config(config):
    if not isinstance(config, dict) or config.get('enabled') is not True:
        raise ValueError('service_disabled')
    parsed = urlsplit(config.get('url', ''))
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or
            parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('service_configuration')
    local = parsed.hostname == '127.0.0.1'
    if not local and (parsed.scheme != 'https' or config.get('allowExternal') is not True):
        raise ValueError('service_authorization')
    # Local sidecars must have a separately enforced public-only egress policy.
    if local and config.get('publicEgressOnly') is not True:
        raise ValueError('service_egress_policy')
    return parsed, local


class ServiceClient:
    def __init__(self, config):
        self.config = config
        self.parsed, self.local = service_config(config)

    def open(self, path='/', body=None):
        if not path.startswith('/') or path.startswith('//') or any(c in path for c in '\r\n'):
            raise ValueError('service_path')
        host = self.parsed.hostname
        port = self.parsed.port or (443 if self.parsed.scheme == 'https' else 80)
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (host, port))] if self.local else resolve_public(host, port, _RESOLVE)
        family, socktype, proto, _, address = addresses[0]
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(15)
        try:
            _CONNECT(sock, address)
            if self.parsed.scheme == 'https':
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            connection = http.client.HTTPConnection(host, port, timeout=15)
            connection.sock = sock
            headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
            # FxEmbed routes the local worker by Host; this never changes the
            # network destination or sends data to the public FxEmbed service.
            if self.local and self.config.get('apiHost') == 'api.fxtwitter.com':
                headers['Host'] = 'api.fxtwitter.com'
            connection.request('POST' if body is not None else 'GET', path,
                               body=json.dumps(body).encode() if body is not None else None, headers=headers)
            response = connection.getresponse()
            if response.status != 200:
                response.close()
                raise ValueError('service_unavailable')
            return connection, response
        except Exception:
            sock.close()
            raise

    def json(self, path='/', body=None):
        connection, response = self.open(path, body)
        try:
            content = response.read(MAX_JSON + 1)
            if len(content) > MAX_JSON:
                raise ValueError('service_response_limit')
            return json.loads(content)
        finally:
            response.close()
            connection.close()

    def tunnel(self, value):
        parsed = urlsplit(value)
        if ((parsed.scheme, parsed.netloc) != (self.parsed.scheme, self.parsed.netloc) or
                parsed.path != '/tunnel' or parsed.fragment or parsed.username is not None):
            raise ValueError('service_tunnel_origin')
        return self.open('/tunnel?' + parsed.query)
