import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'vision-pc'))
import standalone


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__); standalone.register(app); self.client = app.test_client()

    def test_bootstrap_is_small_and_pins_the_packaged_release(self):
        response = self.client.get('/standalone/')
        self.assertEqual(response.status_code, 200)
        manifest = json.loads((ROOT / 'vision-pc/standalone-manifest.json').read_text())
        self.assertIn(manifest['sha256'], response.text)
        self.assertLess(len(response.data), 10000)
        self.assertNotIn('__VISION_TRUSTED_RELEASE__', response.text)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
        self.assertEqual(self.client.post('/standalone/').status_code, 405)
        self.assertEqual(self.client.get('/standalone/config.json').status_code, 404)

    def test_missing_or_malformed_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(standalone, 'ROOT', Path(tmp)):
            self.assertEqual(self.client.get('/standalone/').status_code, 503)
            (Path(tmp) / 'standalone-manifest.json').write_text('{}')
            self.assertEqual(self.client.get('/standalone/').status_code, 503)


if __name__ == '__main__': unittest.main()
