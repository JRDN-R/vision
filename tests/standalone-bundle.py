"""Check actual delivered bytes, assets, policy and executable-script syntax."""
import base64
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent


class Inspect(HTMLParser):
    def __init__(self):
        super().__init__(); self.assets = []; self.scripts = []; self.active = None; self.policy = ''

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('http-equiv') == 'Content-Security-Policy':
            self.policy = attrs['content']
        if tag in ('img', 'script', 'iframe', 'source', 'link'):
            value = attrs.get('src') or attrs.get('href')
            if value:
                self.assets.append(value)
        if tag == 'script' and attrs.get('type', '') in ('', 'text/javascript', 'application/javascript'):
            self.active = []

    def handle_data(self, value):
        if self.active is not None: self.active.append(value)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active is not None:
            self.scripts.append(''.join(self.active)); self.active = None


def check():
    outer = (ROOT / 'Vision-Standalone.html').read_text(encoding='utf-8')
    data = base64.b64decode(re.search(r'<script id="vision-payload" type="application/octet-stream">([^<]+)</script>', outer)[1], validate=True)
    source = data.decode('utf-8')
    manifest = json.loads((ROOT / 'vision-pc/standalone-manifest.json').read_text())
    assert len(data) == manifest['size']
    assert hashlib.sha256(data).hexdigest() == manifest['sha256']
    inspections = []
    for html in (outer, source):
        parsed = Inspect(); parsed.feed(html); inspections.append(parsed)
        assert parsed.assets and all(url.startswith('data:') for url in parsed.assets), 'unbundled initial asset'
        with tempfile.TemporaryDirectory() as temp:
            for i, script in enumerate(parsed.scripts):
                path = Path(temp) / f'script-{i}.js'; path.write_text(script, encoding='utf-8')
                subprocess.run(['node', '--check', str(path)], check=True, capture_output=True)
    assert inspections[1].policy == manifest['policy']
    assert "connect-src 'self'" in inspections[1].policy
    assert 'raw.githubusercontent.com' not in inspections[1].policy
    assert 'localCheckUpdates' not in source
    assert "import('https://www.gstatic.com/firebasejs" not in source
    assert 'credential-source' not in source and 'transcription-source' not in source
    for runtime in ('ffmpeg-core-source','ffmpeg-wasm-source','fvad-core-source','fvad-wasm-source','decoder-source'):
        assert f'id="{runtime}"' in source
    assert 'VisionFirebaseSDK' in source and 'data:font/ttf;base64,' in source
    print(f'PASS: {len(outer.encode()):,}-byte standalone HTML, matching trust hash, {sum(len(x.scripts) for x in inspections)} valid scripts, {len(inspections[1].assets)} embedded assets, no external initial assets or update client.')


if __name__ == '__main__': check()
