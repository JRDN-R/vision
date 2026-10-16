"""Execute the installer entry point in fresh processes without downloading."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'vision-pc/church_browser_install.py'


def fixture_package(tmp_path):
    root = tmp_path / "Sound Room's caf\u00e9"
    packages = root / 'isolated packages'
    package = packages / 'playwright'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('', encoding='utf-8')
    (package / '__main__.py').write_text('''import json, os, sys
from pathlib import Path
Path(os.environ['BOOTSTRAP_CAPTURE']).write_text(json.dumps({
    'args': sys.argv, 'browsers': os.environ['PLAYWRIGHT_BROWSERS_PATH'],
    'module': __file__}), encoding='utf-8')
raise SystemExit(int(os.environ.get('BOOTSTRAP_EXIT', '0')))
''', encoding='utf-8')
    return packages, root / 'browser cache', root / 'capture.json'


def invoke(packages, browsers, capture, exit_code=0):
    env = {**os.environ, 'BOOTSTRAP_CAPTURE': str(capture),
           'BOOTSTRAP_EXIT': str(exit_code), 'PLAYWRIGHT_BROWSERS_PATH': 'wrong inherited path'}
    return subprocess.run([sys.executable, str(SCRIPT), '--packages-path', str(packages),
                           '--browsers-path', str(browsers)], env=env, capture_output=True,
                          text=True, timeout=20)


def test_installs_chromium_from_selected_isolated_package_with_spaced_unicode_paths(tmp_path):
    packages, browsers, capture = fixture_package(tmp_path)
    result = invoke(packages, browsers, capture)
    assert result.returncode == 0, result.stderr
    recorded = json.loads(capture.read_text(encoding='utf-8'))
    assert recorded['args'] == ['playwright', 'install', 'chromium']
    assert Path(recorded['module']).resolve() == packages / 'playwright/__main__.py'
    assert Path(recorded['browsers']) == browsers


@pytest.mark.parametrize('exit_code', [1, 23])
def test_browser_installer_propagates_provider_exit_status(tmp_path, exit_code):
    packages, browsers, capture = fixture_package(tmp_path)
    result = invoke(packages, browsers, capture, exit_code)
    assert result.returncode == exit_code
    assert capture.is_file()


def test_missing_isolated_package_does_not_fall_back_to_global_playwright(tmp_path):
    result = invoke(tmp_path / 'missing', tmp_path / 'browsers', tmp_path / 'capture.json')
    assert result.returncode != 0
    assert 'isolated Playwright package is missing' in result.stderr
    assert not (tmp_path / 'capture.json').exists()
