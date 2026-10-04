"""Offline checks for credential reuse, scope, tampering, and browser removal."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import gemini_credentials as credentials


class GeminiCredentialsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = credentials.GeminiCredentials(self.root / 'config.json', self.root)
        self.key = 'AI-test-key-only-12345678901234567890'
        self.script = '\nconst storedKey="' + self.key.replace('A', '~').replace('a', '£') + '";\n'
        self.store.migration_path.write_text('<script id="credential-source">' + self.script + '</script>', encoding='utf-8')
        self.hash_patch = patch.object(credentials, 'LEGACY_BUNDLE_SHA256', hashlib.sha256(self.script.encode()).hexdigest())
        self.hash_patch.start()
        self.addCleanup(self.hash_patch.stop)
        self.dpapi = patch.multiple(credentials, protect_secret=lambda value: b'opaque-DPAPI-cipher', unprotect_secret=lambda value: self.key)
        self.dpapi.start()
        self.addCleanup(self.dpapi.stop)

    def test_service_import_reuses_key_and_removes_plaintext(self):
        self.assertTrue(self.store.initialize(import_legacy=True))
        self.assertEqual(self.store.path.read_bytes(), b'opaque-DPAPI-cipher')
        self.assertNotIn(self.key.encode(), self.store.path.read_bytes())
        self.assertFalse(self.store.migration_path.exists())
        self.assertEqual(self.store.get(), self.key)
        self.assertTrue(self.store.available())
        self.assertFalse(self.store.initialize(import_legacy=True))

    def test_check_does_not_import_as_interactive_account(self):
        self.assertFalse(self.store.initialize())
        self.assertFalse(self.store.path.exists())
        self.assertTrue(self.store.migration_path.exists())

    def test_altered_source_is_rejected_without_storing_key(self):
        self.store.migration_path.write_text('<script id="credential-source">const storedKey="attacker-token-1234567890";</script>')
        with self.assertRaises(credentials.GeminiCredentialUnavailable):
            self.store.initialize(import_legacy=True)
        self.assertFalse(self.store.path.exists())

    def test_existing_cipher_is_never_overwritten(self):
        self.store.path.write_bytes(b'original-DPAPI-cipher')
        self.assertFalse(self.store.initialize(import_legacy=True))
        self.assertEqual(self.store.path.read_bytes(), b'original-DPAPI-cipher')
        self.assertFalse(self.store.migration_path.exists())

    def test_protection_failure_leaves_source_and_no_partial_cipher(self):
        with patch.object(credentials, 'protect_secret', side_effect=RuntimeError(self.key)):
            with self.assertRaises(credentials.GeminiCredentialUnavailable) as error:
                self.store.initialize(import_legacy=True)
        self.assertNotIn(self.key, str(error.exception))
        self.assertTrue(self.store.migration_path.exists())
        self.assertFalse(self.store.path.exists())
        self.assertEqual(list(self.root.glob('.gemini-*.tmp')), [])

    def test_wrong_windows_identity_fails_closed_without_leaking_key(self):
        self.store.path.write_bytes(b'encrypted-key')
        with patch.object(credentials, 'unprotect_secret', side_effect=RuntimeError(self.key)):
            self.assertFalse(self.store.available())
            with self.assertRaises(credentials.GeminiCredentialUnavailable) as error:
                self.store.get()
        self.assertNotIn(self.key, str(error.exception))

    def test_missing_key_keeps_existing_non_gemini_service_available(self):
        self.store.migration_path.unlink()
        self.assertFalse(self.store.initialize(import_legacy=True))
        self.assertFalse(self.store.available())

    def test_browser_build_removes_retired_bundles_before_restoring_assets(self):
        location = Path(__file__).resolve().parent.parent / 'web' / 'build.py'
        spec = importlib.util.spec_from_file_location('vision_web_build', location)
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        source = ('<script id="credential-source">{{VISION_BUNDLE_secret}}</script>\n'
                  '<script id="transcription-source">{{VISION_BUNDLE_client}}</script>\n'
                  '<script id="openai-session">keep me</script>')
        self.assertEqual(build.without_browser_gemini(source), '<script id="openai-session">keep me</script>')


if __name__ == '__main__':
    unittest.main()
