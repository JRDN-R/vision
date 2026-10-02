"""Rebuild the portable app, preserving its bundled media runtimes and artwork.

Vision.html supplies the existing bundled assets. Editable document structure,
application code, and styles live under web/ and can be published independently.
No credential or embedded runtime is printed by this command.
"""
from pathlib import Path
import hashlib
import re

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
SCRIPTS = ['base.js', 'cloud.js', 'console.js', 'projects.js', 'auth.js', 'board.js', 'handoff.js', 'imports.js', 'capture.js', 'video-preview.js', 'key-visibility.js']
STYLES = ['styles.css', 'console.css', 'projects.css', 'auth.css', 'board.css', 'handoff.css', 'imports.css', 'capture.css', 'video-preview.css']


def build():
    source = (ROOT / 'Vision.html').read_text(encoding='utf-8')
    # Keep large, unchanged offline runtimes out of routine source uploads.
    # SHA-addressed placeholders also prevent a missing or changed asset from
    # silently producing a broken portable download.
    bundles = {hashlib.sha256(m[1].encode('utf-8')).hexdigest(): m[1]
               for m in re.finditer(r'<script\b[^>]*>([\s\S]*?)</script>', source)}
    assets = {hashlib.sha256(m[0].encode('utf-8')).hexdigest(): m[0]
              for m in re.finditer(r'''data:[^\s"'<>]+''', source) if len(m[0]) > 500}
    template = (WEB / 'document.html').read_text(encoding='utf-8')
    def restore(match):
        kind, digest = match.groups()
        value = (bundles if kind == 'BUNDLE' else assets).get(digest)
        if value is None:
            raise ValueError('Missing preserved asset: ' + kind + ' ' + digest)
        return value
    source = re.sub(r'\{\{VISION_(BUNDLE|ASSET)_([a-f0-9]{64})\}\}', restore, template)
    chunks = [(WEB / name).read_text(encoding='utf-8') for name in SCRIPTS]
    # Boot only after all optional interfaces and persistence hooks are installed.
    chunks = [re.sub(r'\nrenderAll\(\);\s*$', '\n', part) for part in chunks]
    script = '\n'.join(chunks) + '\nrenderAll();\n})();\n'
    if '</script' in script.lower():
        raise ValueError('Application JavaScript contains a closing script tag')
    if source.count('{{VISION_APPLICATION}}') != 1:
        raise ValueError('Expected one application script placeholder')
    source = source.replace('{{VISION_APPLICATION}}', script)
    css = '\n'.join((WEB / name).read_text(encoding='utf-8') for name in STYLES)
    if source.count('{{VISION_STYLES}}') != 1:
        raise ValueError('Expected one stylesheet placeholder')
    source = source.replace('{{VISION_STYLES}}', css)
    (ROOT / 'Vision.html').write_text(source, encoding='utf-8')
    digest = hashlib.sha256(source.encode('utf-8')).hexdigest()
    index = ROOT / 'index.html'
    loader = re.sub(r'Vision\.html\?v=[a-f0-9]+', 'Vision.html?v=' + digest[:12], index.read_text(encoding='utf-8'))
    # document.write retains the loader's CSP. Both documents must permit the
    # same Firebase scripts/frames; a later policy cannot loosen the first one.
    policy_pattern = r'<meta http-equiv="Content-Security-Policy" content="[^"]+">'
    policy = re.search(policy_pattern, source)
    if policy is None or len(re.findall(policy_pattern, loader)) != 1:
        raise ValueError('Expected one app and loader Content Security Policy')
    loader = re.sub(policy_pattern, lambda _: policy.group(0), loader)
    index.write_text(loader, encoding='utf-8')
    print('Built Vision.html:', len(source.encode('utf-8')), 'bytes; SHA256', digest)


if __name__ == '__main__':
    build()
