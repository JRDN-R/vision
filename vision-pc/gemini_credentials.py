"""Reuse the existing Gemini credential exclusively in the protected PC service.

The updater stages the previously deployed app in the private installation data
directory. The SYSTEM service imports it once using the same user-scope DPAPI
protection as saved OpenAI sessions. No credential is accepted from the browser,
returned by a route, written to config.json, or supplied on a command line.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from sessions import protect_secret, unprotect_secret


# The exact credential script from the existing deployment. The updater pins
# its source revision as well; do not execute or interpret arbitrary JavaScript.
LEGACY_BUNDLE_SHA256 = 'b2f022f65a13abd145e299008a95eea96285667dd993e18400e2fc1882f80865'
MAX_LEGACY_BYTES = 128 * 1024 * 1024
PROTECTED_FILENAME = 'gemini-credential.dpapi'
MIGRATION_FILENAME = 'gemini-credential-migration.html'


class GeminiCredentialUnavailable(RuntimeError):
    def __init__(self):
        super().__init__('The saved Gemini connection is unavailable on the PC. Run the existing Vision PC updater.')


def _valid_key(key):
    return isinstance(key, str) and re.fullmatch(r'[A-Za-z0-9._-]{20,256}', key) is not None


def _legacy_key(document):
    scripts = re.findall(r'<script\b[^>]*\bid=["\']credential-source["\'][^>]*>([\s\S]*?)</script>', document, re.I)
    if len(scripts) != 1 or hashlib.sha256(scripts[0].encode('utf-8')).hexdigest() != LEGACY_BUNDLE_SHA256:
        raise GeminiCredentialUnavailable()
    # Only the known original string is decoded; no script evaluation occurs.
    match = re.search(r'const\s+storedKey\s*=\s*("[^"\\\r\n]+")\s*;', scripts[0])
    if not match:
        raise GeminiCredentialUnavailable()
    try:
        key = json.loads(match[1]).replace('~', 'A').replace('£', 'a')
    except (ValueError, TypeError):
        raise GeminiCredentialUnavailable() from None
    if not _valid_key(key):
        raise GeminiCredentialUnavailable()
    return key


class GeminiCredentials:
    def __init__(self, config_path, data_dir):
        # config_path is accepted alongside the other processor components.
        # It must never become a source of browser-supplied credentials.
        self.config_path = Path(config_path)
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / PROTECTED_FILENAME
        self.migration_path = self.data_dir / MIGRATION_FILENAME

    def initialize(self, import_legacy=False):
        """Import only at actual service startup, never an installer --check.

        DPAPI is scoped to the process Windows identity. Running the import as
        the interactive administrator would prevent the SYSTEM task reading it.
        Missing credentials leave the Whisper-only service available.
        """
        if not import_legacy:
            return False
        if self.path.is_file():
            # Verify before discarding a staged source; do not overwrite an
            # existing protected key or silently change its identity/scope.
            self.get()
            if self.migration_path.is_file() and not self.migration_path.is_symlink():
                self.migration_path.unlink()
            return False
        if not self.migration_path.is_file():
            return False
        if (self.path.is_symlink() or self.migration_path.is_symlink()
                or self.data_dir.is_symlink()
                or self.migration_path.stat().st_size > MAX_LEGACY_BYTES):
            raise GeminiCredentialUnavailable()
        try:
            key = _legacy_key(self.migration_path.read_text(encoding='utf-8-sig'))
            ciphertext = protect_secret(key)
            if not ciphertext or unprotect_secret(ciphertext) != key:
                raise GeminiCredentialUnavailable()
            self.data_dir.mkdir(parents=True, exist_ok=True)
            # NamedTemporaryFile inherits the existing private directory ACL.
            # os.replace prevents a partial protected key after interruption.
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.data_dir, prefix='.gemini-', suffix='.tmp', delete=False) as output:
                    temp_path = Path(output.name)
                    output.write(ciphertext)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temp_path, self.path)
            finally:
                if temp_path is not None and temp_path.exists():
                    temp_path.unlink()
            self.migration_path.unlink()
            return True
        except Exception:
            # Neither DPAPI errors nor malformed source strings may disclose
            # key material in application logs or HTTP errors.
            raise GeminiCredentialUnavailable() from None

    def get(self):
        """Read only within an authorized worker; never expose through an API."""
        try:
            if self.path.is_symlink() or self.path.stat().st_size > 65536:
                raise GeminiCredentialUnavailable()
            key = unprotect_secret(self.path.read_bytes())
            if not _valid_key(key):
                raise GeminiCredentialUnavailable()
            return key
        except Exception:
            raise GeminiCredentialUnavailable() from None

    def available(self):
        try:
            self.get()
            return True
        except GeminiCredentialUnavailable:
            return False
