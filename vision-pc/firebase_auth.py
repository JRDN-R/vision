"""Verify Firebase ID tokens with Google's public signing certificates.

Only Firebase authentication is used here: project content and processing stay on the PC.
No service account or private Google credential is needed. Like ordinary Firebase
ID-token verification, this validates signature/expiry, not server-side revocation.
"""
from __future__ import annotations

import re
import math
import threading
import time

import requests

CERTIFICATES = 'https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com'
# Match Firebase's supported maximum clock tolerance for freshly issued tokens.
# Expiry remains strict; this only accommodates a PC slightly behind Google.
CLOCK_SKEW_SECONDS = 60
KEY_REFRESH_SECONDS = 60


class InvalidIdentity(Exception):
    def __init__(self, reason='invalid'):
        self.reason = reason
        super().__init__(reason)


class IdentityUnavailable(Exception):
    pass


class FirebaseIdentity:
    def __init__(self, project_id, providers=('google.com', 'password')):
        if not isinstance(project_id, str) or not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', project_id):
            raise ValueError('Choose a valid Firebase project ID.')
        self.project_id = project_id
        self.providers = tuple(providers)
        self._keys, self._expires = {}, 0
        self._last_key_fetch = float('-inf')
        self._lock = threading.Lock()

    def _key(self, kid):
        from cryptography import x509
        with self._lock:
            now = time.monotonic()
            # A fresh login can use a newly rotated key while older sessions
            # still verify. Refresh once, bounded across all unknown key IDs.
            unknown = kid not in self._keys
            if now >= self._expires or (unknown and now-self._last_key_fetch >= KEY_REFRESH_SECONDS):
                self._last_key_fetch = now
                try:
                    response = requests.get(CERTIFICATES, timeout=(5, 10))
                    try:
                        response.raise_for_status()
                        certificates = response.json()
                        if not isinstance(certificates, dict) or not certificates:
                            raise ValueError('Missing signing certificates')
                        keys = {name: x509.load_pem_x509_certificate(pem.encode('ascii')).public_key()
                                for name, pem in certificates.items()}
                        match = re.search(r'(?:^|,)\s*max-age=(\d+)', response.headers.get('Cache-Control', ''))
                        max_age = min(int(match.group(1)), 86400) if match else 300
                    finally:
                        response.close()
                except (requests.RequestException, ValueError, TypeError, AttributeError):
                    raise IdentityUnavailable() from None
                self._keys, self._expires = keys, time.monotonic() + max_age
            key = self._keys.get(kid)
            if key is None:
                raise InvalidIdentity('signing-key')
            return key

    def verify(self, token):
        try:
            import jwt
            import cryptography  # Required for RS256; imported only when enabled.
        except ImportError:
            raise IdentityUnavailable() from None
        if not isinstance(token, str) or not 1 <= len(token) <= 16384:
            raise InvalidIdentity()
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get('kid')
            if header.get('alg') != 'RS256' or not isinstance(kid, str) or not 1 <= len(kid) <= 200:
                raise InvalidIdentity()
            claims = jwt.decode(token, self._key(kid), algorithms=['RS256'],
                                audience=self.project_id,
                                issuer='https://securetoken.google.com/' + self.project_id,
                                options={'require': ['exp', 'iat', 'sub', 'aud', 'iss', 'auth_time'],
                                         'strict_aud': True, 'verify_iat': False})
            now = time.time()
            for field in ('exp', 'iat', 'auth_time'):
                value = claims[field]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise InvalidIdentity()
            if claims['auth_time'] > now+CLOCK_SKEW_SECONDS or claims['iat'] > now+CLOCK_SKEW_SECONDS:
                raise InvalidIdentity('clock')
            if claims['auth_time'] < 0 or claims['iat'] < 0:
                raise InvalidIdentity()
            uid = claims['sub']
            if not isinstance(uid, str) or not 1 <= len(uid) <= 128:
                raise InvalidIdentity()
            firebase = claims.get('firebase')
            if not isinstance(firebase, dict) or firebase.get('sign_in_provider') not in self.providers:
                raise InvalidIdentity()
            return claims
        except jwt.ExpiredSignatureError:
            raise InvalidIdentity('expired') from None
        except jwt.PyJWTError:
            raise InvalidIdentity() from None
