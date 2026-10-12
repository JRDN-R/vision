"""Local, transactional activity setup helper; no remote administration endpoint."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

HOOK = """# Optional private activity dashboard, enabled on the PC by Enable-Vision-Activity.ps1.
if Path(__file__).with_name('activity_dashboard.py').is_file():
    from activity_dashboard import register as register_activity_dashboard
    register_activity_dashboard(app, connect_db)


"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def patch_server(raw):
    text = raw.decode('utf-8-sig')
    normalized = text.replace('\r\n', '\n')
    if HOOK in normalized:
        return raw
    marker = "if __name__ == '__main__':"
    if normalized.count(marker) != 1 or 'register_activity_dashboard' in normalized:
        raise ValueError('The server startup is customized. No changes were made.')
    for required in ('def connect_db(', 'def authorize(', "g.uid, g.auth_kind = 'firebase:'", "app.config['AUDIT_LOGS']"):
        if required not in normalized:
            raise ValueError('Update the PC to the Google sign-in / audit-log build first.')
    updated = normalized.replace(marker, HOOK + marker, 1)
    compile(updated, 'server.py', 'exec')
    if '\r\n' in text:
        updated = updated.replace('\n', '\r\n')
    return updated.encode('utf-8')


def settings(root):
    root = root.resolve()
    config = json.loads((root / 'config.json').read_text(encoding='utf-8-sig'))
    firebase = config.get('firebaseAuth') or {}
    if firebase.get('enabled') is not True:
        raise ValueError('Enable Google sign-in on the Vision PC before enabling private activity.')
    data = Path(config.get('dataDir') or 'data')
    if not data.is_absolute():
        data = root / data
    data = data.resolve()
    if not data.is_relative_to(root):
        raise ValueError('This installation uses an external data folder. Review its access permissions before enabling activity.')
    if not (data / 'vision.sqlite3').is_file():
        raise ValueError('The existing Vision database was not found. Nothing was created or replaced.')
    return config, data


def prepare(root, stage, email=''):
    _config, data = settings(root)
    module = stage / 'activity_dashboard.py'
    accounts = stage / 'account_administration.py'
    compile(module.read_text(encoding='utf-8'), 'activity_dashboard.py', 'exec')
    compile(accounts.read_text(encoding='utf-8'), 'account_administration.py', 'exec')
    db = sqlite3.connect((data / 'vision.sqlite3').as_uri() + '?mode=ro', uri=True)
    try:
        rows = db.execute('SELECT uid,name,email FROM audit_users ORDER BY name,email').fetchall()
    finally:
        db.close()
    if not rows:
        raise ValueError('Use Vision with your Google account once, then run this command again.')
    email = (email or input('Google email allowed to view all Vision activity: ')).strip().casefold()
    matches = [row for row in rows if row[2].strip().casefold() == email]
    if len(matches) != 1:
        raise ValueError('Choose the exact Google email of one existing Vision account. No access was granted.')
    uid, name, actual_email = matches[0]
    if not uid.startswith('firebase:') or not 9 < len(uid) <= 137:
        raise ValueError('That record is not a valid Google account.')
    access = data / 'activity-admins.json'
    previous = json.loads(access.read_text(encoding='utf-8-sig')) if access.exists() else {'uids': []}
    uids = previous.get('uids')
    if not isinstance(uids, list) or any(not isinstance(item, str) or not item.startswith('firebase:') or not 9 < len(item) <= 137 for item in uids):
        raise ValueError('Review the existing local activity access file before changing it.')
    permitted = sorted(set(uids + [uid]))
    if len(permitted) > 100:
        raise ValueError('The dashboard supports at most 100 explicitly authorized accounts.')
    print('Selected Google account: ' + name + ' <' + actual_email + '>')
    print('This grants owner access to ALL user activity and Gemini access controls, and briefly restarts the PC processor.')
    if input('When processing jobs are idle, type YES to enable this account: ').strip() != 'YES':
        raise ValueError('Cancelled. No server files or access permissions were changed.')
    incoming, backup = stage / 'incoming', stage / 'backup'
    incoming.mkdir(exist_ok=True)
    backup.mkdir(exist_ok=True)
    (incoming / 'server.py').write_bytes(patch_server((root / 'server.py').read_bytes()))
    shutil.copy2(module, incoming / 'activity_dashboard.py')
    shutil.copy2(accounts, incoming / 'account_administration.py')
    (incoming / 'activity-admins.json').write_text(json.dumps({'version': 1, 'uids': permitted}, indent=2) + '\n', encoding='utf-8')
    plan = []
    for name, target in [('server.py', root / 'server.py'), ('activity_dashboard.py', root / 'activity_dashboard.py'), ('account_administration.py', root / 'account_administration.py'), ('activity-admins.json', access)]:
        old_hash = digest(target)
        if old_hash:
            shutil.copy2(target, backup / name)
        plan.append({'name': name, 'target': str(target), 'oldHash': old_hash,
                     'newHash': digest(incoming / name)})
    (stage / 'plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    print('Prepared. Existing projects, models, text logs, and Firebase settings are unchanged.')


def apply(stage):
    plan = json.loads((stage / 'plan.json').read_text(encoding='utf-8'))
    # Validate every target before changing any of them.
    for item in plan:
        if digest(Path(item['target'])) != item['oldHash'] or digest(stage / 'incoming' / item['name']) != item['newHash']:
            raise ValueError('The installation changed during setup. Run setup again; this update was not applied.')
    for item in plan:
        target = Path(item['target'])
        temporary = target.with_name(target.name + '.activity-part')
        shutil.copy2(stage / 'incoming' / item['name'], temporary)
        os.replace(temporary, target)


def rollback(stage):
    plan = json.loads((stage / 'plan.json').read_text(encoding='utf-8'))
    for item in plan:
        if digest(Path(item['target'])) not in (item['oldHash'], item['newHash']):
            raise ValueError('A server file changed independently. Refusing to overwrite it during recovery.')
        if item['oldHash'] and digest(stage / 'backup' / item['name']) != item['oldHash']:
            raise ValueError('A protected backup did not pass its integrity check. Manual recovery is required.')
    for item in plan:
        target = Path(item['target'])
        if item['oldHash']:
            shutil.copy2(stage / 'backup' / item['name'], target)
        else:
            target.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['prepare', 'apply', 'rollback'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--admin-email', default='')
    args = parser.parse_args()
    if args.phase == 'prepare':
        prepare(args.root.resolve(), args.stage.resolve(), args.admin_email)
    elif args.phase == 'apply':
        apply(args.stage.resolve())
    else:
        rollback(args.stage.resolve())


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, sqlite3.Error) as error:
        print('Activity setup stopped: ' + str(error))
        raise SystemExit(1)
