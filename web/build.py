"""Rebuild the portable app, preserving its bundled media runtimes and artwork.

Vision.html supplies the existing bundled assets. Editable document structure,
application code, and styles live under web/ and can be published independently.
No credential or embedded runtime is printed by this command.
"""
from pathlib import Path
import base64
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
SCRIPTS = ['base.js', 'cloud.js', 'console.js', 'projects.js', 'launch.js', 'auth.js', 'board.js', 'handoff.js', 'imports.js', 'capture.js', 'video-preview.js', 'key-visibility.js', 'google-sources.js', 'vortex-import.js', 'documents.js', 'run-tools.js', 'local-app.js', 'model-parameters.js', 'venture-controls.js', 'venture-models.js', 'venture-funding.js', 'intelligent-context.js', 'rag-export.js', 'venture.js', 'workspace.js', 'navigation.js', 'toolbelt.js', 'account-profile.js']
STYLES = ['styles.css', 'console.css', 'projects.css', 'auth.css', 'board.css', 'handoff.css', 'imports.css', 'capture.css', 'video-preview.css', 'google-sources.css', 'vortex-import.css', 'documents.css', 'run-tools.css', 'venture.css', 'rag-export.css', 'workspace.css', 'navigation.css', 'toolbelt.css', 'account-profile.css']


def without_browser_gemini(source):
    """The owner Gemini key and direct Gemini client belong only on the PC.

    Remove these before asset restoration so future builds need neither the
    retired credential bundle nor its browser transcription runtime.
    """
    return re.sub(r'<script\b(?=[^>]*\bid=["\'](?:credential-source|transcription-source)["\'])[^>]*>[\s\S]*?</script>\s*', '', source, flags=re.I)


def normalize_version(parts):
    """Carry version digits into the next position after 9."""
    parts = list(parts)
    for i in (3, 2, 1):
        carry, parts[i] = divmod(parts[i], 10)
        parts[i - 1] += carry
    return parts


def select_release(requested, previous_raw, changed):
    """Normalize a legacy multi-digit release without counting migration as a new build."""
    previous_raw = list(previous_raw)
    requested = normalize_version(requested)
    previous = normalize_version(previous_raw)
    if requested > previous:
        return requested
    # 1.0.0.21 and 1.0.2.1 identify the same release. Once a release is
    # normalized, real source updates carry the last digit as usual.
    if changed and (previous_raw == previous or requested < previous):
        previous[3] += 1
    return normalize_version(previous)


def build():
    source = (ROOT / 'Vision.html').read_text(encoding='utf-8')
    previous_version = re.search(r'<meta name="vision-version" content="(\d+\.\d+\.\d+\.\d+)">', source)
    previous_source = re.search(r'<meta name="vision-source-id" content="([a-f0-9]{64})">', source)
    # Keep large, unchanged offline runtimes out of routine source uploads.
    # SHA-addressed placeholders also prevent a missing or changed asset from
    # silently producing a broken portable download.
    bundles = {hashlib.sha256(m[1].encode('utf-8')).hexdigest(): m[1]
               for m in re.finditer(r'<script\b[^>]*>([\s\S]*?)</script>', source)}
    assets = {hashlib.sha256(m[0].encode('utf-8')).hexdigest(): m[0]
              for m in re.finditer(r'''data:[^\s"'<>]+''', source) if len(m[0]) > 500}
    template = without_browser_gemini((WEB / 'document.html').read_text(encoding='utf-8'))
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
    parameters = json.loads((ROOT / 'vision-pc/model-parameters.json').read_text())
    script = script.replace('/* VISION_PARAMETERS */ {}', json.dumps(parameters, separators=(',', ':')))
    notes = json.loads((WEB / 'release-notes.json').read_text())
    if not isinstance(notes.get('title'), str) or not notes.get('items') or not all(isinstance(item, str) for item in notes['items']):
        raise ValueError('Release notes need a title and nonempty text bullet points')
    script = script.replace('/* VISION_RELEASE_NOTES */ {}', json.dumps(notes, separators=(',', ':')))
    if '</script' in script.lower():
        raise ValueError('Application JavaScript contains a closing script tag')
    if source.count('{{VISION_APPLICATION}}') != 1:
        raise ValueError('Expected one application script placeholder')
    firebase = (WEB / 'vendor/firebase.js').read_text(encoding='utf-8')
    if '</script' in firebase.lower():
        raise ValueError('Firebase bundle contains an unsafe closing script tag')
    lottie = (WEB / 'vendor/lottie_svg.min.js').read_text(encoding='utf-8')
    chevrons = {direction: json.loads((WEB / 'animations' / f'circle-chevron-{direction}-gradient-shift.json').read_text())
                for direction in ('right', 'left')}
    controller = (WEB / 'workspace-chevron.js').read_text().replace('/* VISION_CHEVRONS */ {}', json.dumps(chevrons, separators=(',', ':')))
    menu_data = json.loads((WEB / 'animations/menu-in-out.json').read_text())
    menu_controller = (WEB / 'navigation-animation.js').read_text().replace('/* VISION_MENU_ANIMATION */ {}', json.dumps(menu_data, separators=(',', ':')))
    source = source.replace('{{VISION_APPLICATION}}', firebase + '\n' + lottie + '\n' + controller + '\n' + menu_controller + '\n' + script)
    css = '\n'.join((WEB / name).read_text(encoding='utf-8') for name in STYLES)
    if source.count('{{VISION_STYLES}}') != 1:
        raise ValueError('Expected one stylesheet placeholder')
    source = source.replace('{{VISION_STYLES}}', css)
    font = base64.b64encode((WEB / 'vendor/LilitaOne-Regular.ttf').read_bytes()).decode('ascii')
    source = source.replace('{{VISION_LILITA_FONT}}', 'data:font/ttf;base64,' + font)
    # Reuse the original transparent head without changing its image pixels.
    # An SVG viewport trims its transparent margins only for header layout.
    logo = base64.b64encode((ROOT / 'logo or node.PNG').read_bytes()).decode('ascii')
    source = source.replace('{{VISION_HEADER_LOGO}}', 'data:image/png;base64,' + logo)
    version = (WEB / 'version.txt').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'\d+(?:\.[0-9]){3}', version):
        raise ValueError('Expected four numeric version positions (single digits after major)')
    # Keep the source fingerprint internal. Carry every tenth release into the
    # next position: 1.0.2.9 -> 1.0.3.0, never 1.0.2.10.
    source_id = hashlib.sha256((version + '\0' + source).encode('utf-8')).hexdigest()
    parts = normalize_version(int(part) for part in version.split('.'))
    if previous_version and previous_source:
        previous = [int(part) for part in previous_version[1].split('.')]
        parts = select_release(parts, previous, previous_source[1] != source_id)
    release = '.'.join(str(part) for part in parts)
    if source.count('{{VISION_VERSION}}') != 2:
        raise ValueError('Expected the release ID in the header and info dialog')
    source = source.replace('{{VISION_VERSION}}', release)
    source = source.replace('</head>', f'<meta name="vision-version" content="{release}">\n'
                            f'<meta name="vision-source-id" content="{source_id}">\n</head>', 1)
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
    print('Built Vision', release + ':', len(source.encode('utf-8')), 'bytes; SHA256', digest)


if __name__ == '__main__':
    build()
