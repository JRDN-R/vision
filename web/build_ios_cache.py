"""Build two public, self-contained app documents for the native disk cache."""
import base64
import hashlib
import json
import mimetypes
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def data_url(path):
    mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    return 'data:' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode()


def local_asset(value, base):
    if value.startswith(('data:', 'https:', 'http:', '#')):
        return value
    path = (base / value.split('?', 1)[0]).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError('Missing offline asset: ' + value)
    return data_url(path)


def build():
    page = (ROOT / 'vortex/index.html').read_text()
    def style(match):
        path = ROOT / 'vortex' / match[1].split('?', 1)[0]
        css = path.read_text()
        css = re.sub(r'''url\(["']?([^)'" ]+)["']?\)''', lambda m: 'url("' + local_asset(m[1], path.parent) + '")', css)
        return '<style>' + css + '</style>'
    page = re.sub(r'<link rel="stylesheet" href="([^"]+)">', style, page)
    page = re.sub(r'<script\b[^>]*src="[^"]+"[^>]*></script>', '', page)
    page = re.sub(r'^[ \t]*<link rel="manifest"[^>]*>\n?', '', page, flags=re.M)
    page = re.sub(r'(<link rel="icon"[^>]*\bhref=")([^"]+)(")', lambda m: m[1] + local_asset(m[2], ROOT / 'vortex') + m[3], page)
    page = re.sub(r'(<img\b[^>]*\bsrc=")([^"]+)(")', lambda m: m[1] + local_asset(m[2], ROOT / 'vortex') + m[3], page)
    auth = (ROOT / 'web/vortex/auth.js').read_text()
    exports = re.findall(r'export (?:async )?(?:function|const) (\w+)', auth)
    auth = '(function(){\n' + auth.replace('export ', '') + '\nreturn {' + ','.join(exports) + '};\n})()'
    app = (ROOT / 'web/vortex/app.js').read_text()
    app = re.sub(r"^import \{([^}]+)\} from '[^']+';", lambda m: 'const {' + m[1] + '} = ' + auth + ';', app)
    app = re.sub(r"^import '[^']+';\n", '', app, flags=re.M)
    animation = (ROOT / 'web/animations/menu-in-out.json').read_text()
    app = app.replace("const response = await fetch('../web/animations/menu-in-out.json');", 'const response = {ok:true, json:async()=>(' + animation + ')};')
    for match in set(re.findall(r"['\"](\.\./[^'\"]+\.(?:png|svg|jpg))['\"]", app)):
        app = app.replace(match, local_asset(match, ROOT / 'vortex'))
    scripts = [(ROOT / ('web/' + file)).read_text() for file in ['orientation-lock.js', 'vendor/firebase.js', 'vendor/lottie_svg.min.js', 'mobile-menu-swipe.js']]
    if any('</script' in script.lower() for script in scripts + [app]):
        raise ValueError('Unsafe script terminator in offline Vortex')
    page = page.replace('</body>', ''.join('<script>' + script + '</script>' for script in scripts) + '<script type="module">' + app + '</script></body>')
    (ROOT / 'vortex/offline.html').write_text(page)
    assets = []
    for name in ['Vision.html', 'vortex/offline.html']:
        data = (ROOT / name).read_bytes()
        assets.append(dict(path=name, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data)))
    (ROOT / 'ios-cache-manifest.json').write_text(json.dumps(dict(schema=1, assets=assets), indent=2) + '\n')
    print('Built verified iOS app cache:', sum(a['bytes'] for a in assets), 'bytes')

if __name__ == '__main__':
    build()
