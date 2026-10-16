"""Install Chromium from the isolated notification dependencies.

Keep this code in a file: Windows PowerShell 5.1 removes embedded quotes when
passing the former inline Python command to the native interpreter.
"""
import argparse
import os
from pathlib import Path
import runpy
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packages-path', type=Path, required=True)
    parser.add_argument('--browsers-path', type=Path, required=True)
    args = parser.parse_args(argv)
    packages = args.packages_path.resolve()
    if not (packages / 'playwright' / '__main__.py').is_file():
        parser.error('The isolated Playwright package is missing. Run setup again.')
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(args.browsers_path.resolve())
    sys.path.insert(0, str(packages))
    sys.argv = ['playwright', 'install', 'chromium']
    # Playwright's exit status must reach PowerShell; never report success after
    # a failed download. A fresh process avoids importing another installation.
    runpy.run_module('playwright', run_name='__main__')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
