"""Serve a small, hash-pinned launch document; no project or credential relay."""
import json
from pathlib import Path
import re

from flask import Response

ROOT = Path(__file__).resolve().parent
PUBLIC_PATH = '/standalone/'


def bridge_response():
    try:
        release = json.loads((ROOT / 'standalone-manifest.json').read_text(encoding='utf-8'))
        if (not re.fullmatch(r'[a-f0-9]{64}', release['sha256'])
                or type(release['size']) is not int or not 100_000 < release['size'] < 50 * 1024 * 1024
                or release['server'] != 'https://desktop-vjt2br2.tail385c9d.ts.net'
                or not isinstance(release['policy'], str) or '\n' in release['policy']):
            raise ValueError('Invalid standalone release')
        template = (ROOT / 'standalone-bridge.html').read_text(encoding='utf-8')
        if template.count('__VISION_TRUSTED_RELEASE__') != 1:
            raise ValueError('Invalid standalone bridge')
        public = {key: release[key] for key in ('sha256', 'size', 'server')}
        html = template.replace('__VISION_TRUSTED_RELEASE__', json.dumps(public).replace('<', '\\u003c'))
    except (OSError, ValueError, KeyError, TypeError):
        return Response('Standalone launch is not installed correctly. Update FUPCJ Server.', status=503, mimetype='text/plain')
    response = Response(html, mimetype='text/html')
    # document.write cannot loosen the initial response policy. Use the exact
    # policy of the pinned application, plus a response-only framing restriction.
    response.headers['Content-Security-Policy'] = release['policy'] + "; frame-ancestors 'none'"
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def register(app):
    app.add_url_rule(PUBLIC_PATH, 'standalone_bridge', bridge_response, methods=['GET'])
