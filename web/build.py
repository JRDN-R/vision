"""Rebuild the portable app, preserving its bundled media runtimes and artwork.

Vision.html is also the asset template. All editable application code lives here;
the builder replaces the final application script and the first style element.
No credential or embedded runtime is printed by this command.
"""
from pathlib import Path
import hashlib
import re

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
SCRIPTS = ['base.js', 'cloud.js', 'console.js', 'projects.js', 'board.js', 'handoff.js']
STYLES = ['styles.css', 'console.css', 'projects.css', 'board.css', 'handoff.css']


def build():
    source = (ROOT / 'Vision.html').read_text(encoding='utf-8')
    chunks = [(WEB / name).read_text(encoding='utf-8') for name in SCRIPTS]
    # Boot only after all optional interfaces and persistence hooks are installed.
    chunks = [re.sub(r'\nrenderAll\(\);\s*$', '\n', part) for part in chunks]
    script = '\n'.join(chunks) + '\nrenderAll();\n})();\n'
    if '</script' in script.lower():
        raise ValueError('Application JavaScript contains a closing script tag')
    scripts = list(re.finditer(r'<script\b[^>]*>([\s\S]*?)</script>', source))
    target = scripts[-1]
    if 'const APP_SOURCE=' not in target.group(1):
        raise ValueError('Could not locate the application script')
    source = source[:target.start(1)] + script + source[target.end(1):]
    css = '\n'.join((WEB / name).read_text(encoding='utf-8') for name in STYLES)
    source, count = re.subn(r'(<style\b[^>]*>)[\s\S]*?(</style>)', lambda m: m[1] + css + m[2], source, count=1)
    if count != 1:
        raise ValueError('Could not locate the application stylesheet')
    (ROOT / 'Vision.html').write_text(source, encoding='utf-8')
    digest = hashlib.sha256(source.encode('utf-8')).hexdigest()
    index = ROOT / 'index.html'
    loader = re.sub(r'Vision\.html\?v=[a-f0-9]+', 'Vision.html?v=' + digest[:12], index.read_text(encoding='utf-8'))
    index.write_text(loader, encoding='utf-8')
    print('Built Vision.html:', len(source.encode('utf-8')), 'bytes; SHA256', digest)


if __name__ == '__main__':
    build()
