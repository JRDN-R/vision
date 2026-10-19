"""Operator-only Heretic installation. Does not edit config or restart services.

Download writes a separate pinned GGUF and manifest. Enable verifies both models
and the runtime, then runs a benign two-turn CPU smoke test before publishing a
readiness receipt. It does not certify refusal behavior or livestream performance.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import time
from urllib.parse import urlparse

import requests

from local_runtime import HERETIC_SPEC, LlamaRuntime, LocalFailure, digest_file
from local_variants import (RECEIPT_SCHEMA, checked_path, profile_key, read_json,
                            receipt_path, select_profile, stamp, variant_root)
from owned_assets import exclusive_file


def atomic_json(path, value):
    path = checked_path(path)
    temp = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def download_model(destination, spec=None):
    """Bounded streaming and resumable staging; never replace existing weights."""
    spec = HERETIC_SPEC if spec is None else spec
    destination = checked_path(destination)
    size, digest = spec['modelBytes'], spec['modelSha256']
    if destination.exists():
        if destination.stat().st_size != size or digest_file(destination) != digest:
            raise LocalFailure('Existing model bytes differ from the pin. Nothing was replaced.')
        return
    partial = checked_path(str(destination) + '.part')
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > size:
        raise LocalFailure('Oversized partial model. Inspect it before retrying.')
    if shutil.disk_usage(destination.parent).free < size - offset + 2 * 1024**3:
        raise LocalFailure('Insufficient space for the model plus 2 GiB operating headroom.')
    url = ('https://huggingface.co/' + spec['modelRepository'] + '/resolve/' +
           spec['modelRevision'] + '/' + spec['modelFile'])
    if offset < size:
        with requests.Session() as http:
            http.trust_env = False
            # Follow only HTTPS redirects; no account tokens or prompts are sent.
            for _ in range(8):
                response = http.get(url, headers={'Range': 'bytes=' + str(offset) + '-'} if offset else {},
                                    stream=True, allow_redirects=False, timeout=(15, 120))
                if response.is_redirect:
                    location = response.headers.get('Location', '')
                    response.close()
                    if urlparse(location).scheme != 'https':
                        raise LocalFailure('Model download redirected away from HTTPS.')
                    url = location
                    continue
                break
            else:
                raise LocalFailure('Too many model download redirects.')
            with response:
                response.raise_for_status()
                if response.status_code == 206:
                    expected = 'bytes ' + str(offset) + '-'
                    if not response.headers.get('Content-Range', '').startswith(expected):
                        raise LocalFailure('The model resume range did not match.')
                elif response.status_code == 200:
                    offset = 0  # A server ignoring Range must restart, never append.
                else:
                    raise LocalFailure('Unexpected model download response.')
                total = offset
                reported = time.monotonic()
                started, start_bytes = reported, offset
                with partial.open('ab' if offset else 'wb') as stream:
                    for chunk in response.iter_content(1024 * 1024):
                        total += len(chunk)
                        if total > size:
                            raise LocalFailure('Model download exceeded its pinned byte length.')
                        stream.write(chunk)
                        if spec is not HERETIC_SPEC and time.monotonic() - reported >= 10:
                            reported = time.monotonic()
                            rate = (total-start_bytes)/max(1,reported-started)
                            print(f'Model download {total/size:.0%}; approximately {(size-total)/max(1,rate)/60:.0f} minutes left.',flush=True)
                    stream.flush()
                    os.fsync(stream.fileno())
    if partial.stat().st_size != size or digest_file(partial) != digest:
        raise LocalFailure('Model checksum failed. Staging was retained, not activated.')
    partial.rename(destination)


def smoke_test(profile, runtime_factory=LlamaRuntime):
    messages = [{'role': 'system', 'content': 'Follow the request and use earlier messages. Answer briefly.'},
                {'role': 'user', 'content': 'Remember the project label COBALT-731 for this conversation. Reply with that label only.'}]
    with runtime_factory(profile, lambda: False, print) as runtime:
        for index in range(2):
            if runtime.count(messages) + 2048 + 64 > profile_key(profile)['contextTokens']:
                raise LocalFailure('The smoke-test prompt does not fit this profile.')
            text, finish = '', None
            for event in runtime.generate(messages, 2048):
                if event.get('error'):
                    raise LocalFailure('Heretic returned an inference error.')
                for choice in event.get('choices', []):
                    text += (choice.get('delta') or {}).get('content') or ''
                    finish = choice.get('finish_reason') or finish
                    if len(text) > 8192 or '<think>' in text or '</think>' in text:
                        raise LocalFailure('Heretic output/template validation failed.')
            if finish != 'stop' or 'COBALT-731' not in text:
                raise LocalFailure('Heretic did not pass the two-turn smoke test. No readiness receipt was published.')
            if index == 0:
                messages += [{'role': 'assistant', 'content': text},
                             {'role': 'user', 'content': 'What project label did I give you? Reply with the label only.'}]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--action', choices=['download', 'check', 'enable', 'disable'], default='check')
    parser.add_argument('--approved-quiet-window', action='store_true')
    args = parser.parse_args()
    if args.action == 'enable' and not args.approved_quiet_window:
        parser.error('Enable runs a real CPU test. Add --approved-quiet-window outside livestream work.')
    path = checked_path(Path(args.config).absolute())
    config = read_json(path)
    settings = dict(config.get('localAI') or {})
    profile = select_profile(settings, True, require_ready=False)
    root = checked_path(variant_root(settings))
    root.mkdir(parents=True, exist_ok=True)
    data = Path(config.get('dataDir') or 'data')
    if not data.is_absolute():
        data = path.parent / data
    # Same lock as every local answering/visual job. Never overlap models.
    from local_standby import maintenance_window
    with maintenance_window(checked_path(data)):
        receipt = receipt_path(profile)
        if args.action == 'disable':
            receipt.unlink(missing_ok=True)
            print('Heretic disabled for new and queued requests. Standard model and all conversations are unchanged.')
            return
        base = LlamaRuntime(select_profile(settings, False), lambda: False, print)
        try:
            base.verify()
        finally:
            base.http.close()
        if args.action == 'download':
            download_model(Path(profile['modelPath']))
            original = read_json(settings['manifestPath'])
            manifest = {k:v for k,v in original.items() if k.startswith('runtime')}
            manifest.update(HERETIC_SPEC)
            manifest.update(sourceModel='rohit267/Qwen3.8-9B-heretic-uncensored',
                            license='apache-2.0', modelVariant='heretic')
            target = Path(profile['manifestPath'])
            if not target.exists():
                atomic_json(target, manifest)
            elif read_json(target) != manifest:
                raise LocalFailure('Variant manifest differs from the current runtime. Inspect it before replacement.')
            print('Heretic downloaded and checksum verified. Run check, then enable during an approved quiet window.')
            return
        live = LlamaRuntime(profile, lambda: False, print)
        try:
            live.verify()
        finally:
            live.http.close()
        if args.action == 'check':
            print('Heretic and the existing runtime verified. No model was loaded or default changed.')
            return
        receipt.unlink(missing_ok=True)  # Failed revalidation must not retain an old Enable receipt.
        smoke_test(profile)
        atomic_json(receipt, dict(schema=RECEIPT_SCHEMA, passed=True, enabledAt=time.time(),
            profile=profile_key(profile), manifestSha256=digest_file(profile['manifestPath']),
            modelStamp=stamp(profile['modelPath']), runtimeStamp=stamp(profile['runtimePath'])))
        print('Heretic enabled as an optional model. Refresh Venture, select Qwen Local, then turn Uncensored on.')
        print('Standard remains the default. No config edit, service restart, cloud fallback, or account migration occurred.')


if __name__ == '__main__':
    try:
        main()
    except (LocalFailure, OSError, ValueError, requests.RequestException) as error:
        raise SystemExit('Heretic setup stopped: ' + str(error)) from None
