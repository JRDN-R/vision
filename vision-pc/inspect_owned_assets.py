"""Read-only retention inventory; never initializes, migrates, downloads or deletes."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from owned_assets import owner_key, reject_link


def regular_under(root, path):
    reject_link(root)
    root = root.resolve()
    path = Path(os.path.abspath(path))
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        reject_link(current)
    return path if path.is_file() else None


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def inspect(config_path, verify=False):
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict):
        raise ValueError('Invalid installation configuration')
    options = config.get('durableAssets', {})
    if options is None:
        options = {}
    if not isinstance(options, dict) or type(options.get('enabled', False)) is not bool:
        raise ValueError('Invalid retention configuration')
    data = Path(config.get('dataDir') or 'data')
    if not data.is_absolute():
        data = config_path.parent / data
    data = data.resolve()
    database = data / 'vision.sqlite3'
    report = dict(schemaVersion=1, dryRun=True, databaseModified=False,
                  observedAt=datetime.now(timezone.utc).isoformat(), dataDirectory=str(data),
                  integrityCheck='sha256' if verify else 'existence-and-size-only',
                  newImportRetentionEnabled=options.get('enabled') is True,
                  ownedAssets=[], legacyImports=[], sourceLeases=[], issues=[],
                  boardAttachmentImplemented=False, modelReadiness='not inspected',
                  limitations=['Point-in-time inventory, not a migration or consistent live filesystem backup.',
                               'Acquired proxies are not proof of full-quality platform originals.',
                               'No missing source is reconstructed from frames or transcripts.'])
    if not regular_under(data, database):
        report['issues'].append('database-unavailable')
        report['counts'] = dict(ownedAssets=0, legacyImports=0, sourceLeases=0, issues=1)
        return report
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'owned_assets' in tables:
            for row in db.execute('SELECT * FROM owned_assets ORDER BY created_at,id'):
                item = dict(assetId=row['id'], ownerHash=owner_key(row['uid']), state=row['state'], role=row['role'],
                            expectedBytes=row['byte_count'], exists=False, sha256Verified=None)
                try:
                    if not re.fullmatch(r'[0-9a-f]{24}', row['id']):
                        raise ValueError('invalid asset id')
                    base = data / 'owned-assets' / owner_key(row['uid'])
                    path = regular_under(data, base / (row['id'] + '.blob'))
                    stage = regular_under(data, base / (row['id'] + '.part'))
                    item['exists'] = path is not None
                    item['stagingExists'] = stage is not None
                    if path:
                        item['actualBytes'] = path.stat().st_size
                        item['sizeMatches'] = item['actualBytes'] == row['byte_count']
                        if verify and row['sha256']:
                            item['sha256Verified'] = digest(path) == row['sha256']
                    if row['state'] != 'ready':
                        report['issues'].append('unfinished-adoption:' + row['id'])
                    elif not path or not item['sizeMatches'] or item['sha256Verified'] is False:
                        report['issues'].append('asset-needs-recovery:' + row['id'])
                except (OSError, ValueError):
                    report['issues'].append('unsafe-or-unreadable-asset:' + row['id'])
                report['ownedAssets'].append(item)
        if 'owned_source_leases' in tables:
            report['sourceLeases'] = [dict(vortexId=r['vortex_id'], importId=r['import_id'], ownerHash=owner_key(r['uid']))
                                      for r in db.execute('SELECT * FROM owned_source_leases ORDER BY created_at')]
        if 'jobs' in tables and 'vortex_jobs' in tables:
            columns = {r[1] for r in db.execute('PRAGMA table_info(jobs)')}
            if {'source_kind', 'import_options', 'result_path', 'uid'} <= columns:
                for job in db.execute("SELECT * FROM jobs WHERE source_kind='vortex' ORDER BY created_at,id"):
                    if json.loads(job['import_options']).get('assetOwnership') == 1:
                        continue
                    low = db.execute("SELECT * FROM vortex_jobs WHERE uid=? AND request_id=?",
                                     (job['uid'], 'vision-import-' + job['id'])).fetchone()
                    item = dict(importId=job['id'], ownerHash=owner_key(job['uid']), status=job['status'],
                                availableAcquiredSources=[], resultAvailable=False, platformOriginalVerified=False)
                    candidates = [low] if low else []
                    if low and 'history_id' in low.keys() and low['history_id']:
                        history = db.execute('SELECT * FROM vortex_jobs WHERE id=? AND uid=?',
                                             (low['history_id'], job['uid'])).fetchone()
                        if history:
                            candidates.append(history)
                    for source in candidates:
                        name = source['filename']
                        if not name:
                            continue
                        try:
                            if not re.fullmatch(r'[0-9a-f]{24}', source['id']) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', name):
                                raise ValueError('invalid source location')
                            path = regular_under(data, data / 'vortex' / source['id'] / name)
                            if path:
                                item['availableAcquiredSources'].append(dict(vortexId=source['id'], bytes=path.stat().st_size,
                                                                            quality=source['quality'], temporary=True))
                        except (OSError, ValueError):
                            report['issues'].append('unsafe-legacy-source:' + job['id'])
                    if job['result_path']:
                        try:
                            item['resultAvailable'] = regular_under(data / 'results', Path(job['result_path'])) is not None
                        except (OSError, ValueError):
                            report['issues'].append('unsafe-legacy-result:' + job['id'])
                    item['recovery'] = ('acquired-source-available' if item['availableAcquiredSources'] else
                                        'evidence-only-source-unavailable' if item['resultAvailable'] else 'source-and-result-unavailable')
                    report['legacyImports'].append(item)
    report['counts'] = dict(ownedAssets=len(report['ownedAssets']), legacyImports=len(report['legacyImports']),
                            sourceLeases=len(report['sourceLeases']), issues=len(report['issues']))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--verify', action='store_true', help='Read retained files and verify recorded SHA-256 hashes')
    parser.add_argument('--output', type=Path, help='Create a NEW private JSON report; never overwrite a report')
    args = parser.parse_args()
    try:
        report = inspect(args.config, verify=args.verify)
        encoded = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
        if args.output:
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                output.write(encoded)
            print('Read-only retention report written. No installation, migration or activation occurred.')
        else:
            print(encoded)
        return 1 if args.verify and report['issues'] else 0
    except (OSError, ValueError, sqlite3.Error):
        print('The read-only inventory could not complete. Check local permissions, paths and database integrity.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
