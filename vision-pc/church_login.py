"""Interactive sign-in on the FUPCJ PC; no credentials are entered by code."""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=str(Path(__file__).with_name('config.json')))
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    settings = config.get('churchAdmins') or {}
    if not settings.get('enabled'):
        raise RuntimeError('Run FirstUPCJax Admins setup first.')
    sys.path.insert(0, settings['packagesPath'])
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = settings['browsersPath']
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.check)
        try:
            context = browser.new_context()
            page = context.new_page()
            if args.check:
                page.set_content('<title>FirstUPCJax browser check</title>')
                assert page.title() == 'FirstUPCJax browser check'
                print('Notification browser launches successfully. Provider login and sending were not tested.')
                return 0
            page.goto('https://services.planningcenteronline.com/', wait_until='domcontentloaded', timeout=60000)
            print('Sign in to Planning Center in the opened browser. Select FirstUPCJax Services.')
            input('After the Services page is open, press Enter here to save the service session: ')
            if not page.url.startswith('https://services.planningcenteronline.com/') or page.get_by_role('button',name='Log in',exact=True).is_visible():
                raise RuntimeError('Services sign-in was not completed. No session saved.')
            state = context.storage_state()
            state['cookies'] = [c for c in state['cookies'] if c['domain'].lstrip('.') == 'planningcenteronline.com' or c['domain'].lstrip('.').endswith('.planningcenteronline.com')]
            state['origins'] = [o for o in state['origins'] if o['origin'] in ('https://services.planningcenteronline.com','https://login.planningcenteronline.com')]
            if not state['cookies']:
                raise RuntimeError('No Planning Center session was available to save.')
            root = Path(config.get('dataDir') or 'data')
            if not root.is_absolute():
                root = config_path.parent / root
            directory = root / 'church-admin'
            directory.mkdir(exist_ok=True)
            stage = directory / 'browser-state.pending.tmp'
            stage.write_text(json.dumps(state),encoding='utf-8')
            os.replace(stage, directory / 'browser-state.pending.json')
            print('Session saved to the protected server folder. The SYSTEM worker will encrypt it with Windows DPAPI.')
            return 0
        finally:
            browser.close()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('Planning Center browser setup did not finish. Check the local sign-in and run setup again. No notification sent.')
        raise SystemExit(1)
