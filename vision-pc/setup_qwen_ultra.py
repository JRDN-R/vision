"""Install and benchmark Ultra beside existing models; never edit clocks or BIOS."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import time

import psutil
import requests
import local_ultra as ultra
from local_runtime import LlamaRuntime, LocalFailure, digest_file
from local_variants import checked_path, read_json, stamp
from owned_assets import exclusive_file
from setup_local_variant import atomic_json, download_model, smoke_test


def quiet_machine():
    # The installer never kills another application to win a benchmark.
    for process in psutil.process_iter(['name']):
        if (process.info['name'] or '').lower() in ('obs64.exe','obs32.exe','streamlabs obs.exe'):
            raise LocalFailure('Close OBS/Streamlabs before tuning. Download can be run separately; no process was stopped.')


def cpu_candidates(cores, reserve=2):
    # Standard mode leaves two physical cores free for Windows and OBS.
    # An operator may explicitly bench all 16 physical cores in Boost.
    cap = max(1,min(16,cores-reserve if cores > 4 else cores))
    return sorted({min(n,cap) for n in (8,12,16)})


def gpu_candidates(devices, boost=False):
    # Use backend-reported free VRAM, not total VRAM or Windows' adapter size.
    free = [int(n) for n in re.findall(r'(\d+)\s*MiB\s+free',devices,re.I)]
    if not free:
        return []
    # Boost explores up to 75% of reported free VRAM, while still reserving
    # >=2 GiB. These are candidate estimates, not promised allocations;
    # every profile must complete a real inference and context smoke test.
    available = max(free)
    budget = min(available * (.75 if boost else .5), available-2048)
    return [layers for layers in ((4,8,12,16) if boost else (4,8,12))
            if layers*400+512 <= budget]


def benchmark(profile, runtime_factory=LlamaRuntime):
    start = time.monotonic()
    prompt = 'Use this reference only. ' + ('The project label is COBALT-731. ' * 100)
    prompt += 'Write the numbers from 1 to 200, one per line.'
    with runtime_factory({**profile,'thinking':False,'benchmark':True},lambda:False,lambda s:print(s,flush=True)) as live:
        loaded = time.monotonic()-start
        messages = [dict(role='user',content=prompt)]
        live.count(messages)
        timings, text, finish = {}, '', None
        for event in live.generate(messages,128):
            timings.update(event.get('timings') or {})
            if event.get('error'):
                raise LocalFailure('Ultra benchmark stream failed.')
            for choice in event.get('choices',[]):
                text += (choice.get('delta') or {}).get('content') or ''
                finish = choice.get('finish_reason') or finish
        pp, tg = timings.get('prompt_per_second',0), timings.get('predicted_per_second',0)
        if not text.strip() or finish not in ('stop','length') or any(type(n) not in (int,float) or not math.isfinite(n) or n <= 0 for n in (pp,tg)):
            raise LocalFailure('Runtime did not return a completed benchmark with measured prompt/generation rates.')
        return dict(loadSeconds=round(loaded,3),promptTokensPerSecond=pp,generationTokensPerSecond=tg,
                    peakResidentBytes=live.peak_rss,minimumAvailableRamBytes=live.minimum_available_ram)


def score(result):
    b = result['benchmark']
    return b['loadSeconds'] + 1024/b['promptTokensPerSecond'] + 2048/b['generationTokensPerSecond']


def context_check(profile, runtime_factory=LlamaRuntime):
    # Exercise prompt evaluation near the selected context, beyond merely
    # allocating a KV cache or accepting a command-line flag.
    with runtime_factory({**profile,'thinking':False},lambda:False,print) as live:
        content = 'Reference: the label is COBALT-731.\n'
        tail = 'Reply with the label only.'
        messages = [dict(role='user',content=content+tail)]
        target = profile['contextTokens'] - 256
        one = live.count(messages)
        messages[0]['content'] = content*2+tail
        unit_tokens = max(1,live.count(messages)-one)
        repeats = max(1,(target-one)//unit_tokens+1)
        messages[0]['content'] = content*repeats + tail
        count = live.count(messages)
        while count > target:
            repeats = max(1,int(repeats * target/count)-1)
            messages[0]['content'] = content*repeats + tail
            count = live.count(messages)
        text, finish = '', None
        for event in live.generate(messages,128):
            for choice in event.get('choices',[]):
                text += (choice.get('delta') or {}).get('content') or ''
                finish = choice.get('finish_reason') or finish
        if finish != 'stop' or 'COBALT-731' not in text or count < target*.8:
            raise LocalFailure('Ultra did not pass the large-context check.')


def tune(profile, target, max_context, boost=False):
    quiet_machine()
    deadline = time.monotonic()+7200
    # Large-context evaluation can be much slower than a short throughput trial.
    profile.update(contextTokens=max_context, maxRunSeconds=18000)
    results, errors, attempted = [], [], set()

    def trial(threads, layers):
        if (threads,layers) in attempted:
            return
        attempted.add((threads,layers))
        quiet_machine()
        if time.monotonic()>deadline:
            raise LocalFailure('Tuning reached two hours. Partial measurements are retained; rerun Tune later.')
        knobs = dict(threads=threads,gpuLayers=layers,batchSize=256,ubatchSize=64)
        print(f'Benchmark: {threads} CPU threads, {layers} GPU layers. No clock or power changes.',flush=True)
        try:
            result = dict(profile=knobs,benchmark=benchmark({**profile,**knobs}))
            results.append(result)
            print(f"Measured {result['benchmark']['generationTokensPerSecond']:.2f} generated tokens/sec.",flush=True)
        except (OSError,ValueError,requests.RequestException) as error:
            errors.append(dict(profile=knobs,error=str(error)[:600]))
            print('Profile rejected: '+str(error),flush=True)
        atomic_json(target/'tuning-report.json',dict(results=results,errors=errors))

    physical = psutil.cpu_count(logical=False) or 4
    for threads in cpu_candidates(physical):
        trial(threads,0)
    if not results:
        raise LocalFailure('No CPU profile passed. See tuning-report.json; Ultra remains unavailable.')
    cpu = min(results,key=score)
    try:
        devices = subprocess.run([profile['runtimePath'],'--list-devices'],capture_output=True,text=True,timeout=30,check=True)
        inventory = devices.stdout+devices.stderr
        layers_to_test = gpu_candidates(inventory)
    except (OSError,subprocess.SubprocessError):
        inventory = ''
        layers_to_test = []
        print('GPU inventory unavailable; retaining measured CPU candidates.',flush=True)
    for layers in layers_to_test:
        trial(cpu['profile']['threads'],layers)
    # Auto never changes the existing conservative search envelope.
    fastest = min(results,key=score)
    auto = fastest if score(fastest)<score(cpu)*.95 else cpu
    selected = dict(cpu=cpu,auto=auto)

    if boost:
        # This is an explicit opt-in benchmark: try the full physical-core
        # allowance and wider (still reserved) VRAM budgets. No clocks,
        # BIOS, power limits, memory reserves, or model provenance change.
        for threads in cpu_candidates(physical,reserve=0):
            trial(threads,0)
        boost_cpu = min((r for r in results if r['profile']['gpuLayers']==0),key=score)
        for layers in gpu_candidates(inventory,boost=True):
            trial(boost_cpu['profile']['threads'],layers)
        fastest_boost = min(results,key=score)
        # Reject trivial measurement noise rather than promising faster output.
        selected['boost'] = fastest_boost if score(fastest_boost)<score(auto)*.95 else auto

    validated = set()
    for chosen in selected.values():
        key = (chosen['profile']['threads'],chosen['profile']['gpuLayers'])
        if key in validated:
            continue
        validated.add(key)
        quiet_machine()
        tested = {**profile,**chosen['profile']}
        print('Checking conversation, thinking and the selected context…',flush=True)
        smoke_test(tested)
        context_check(tested)
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--action',choices=['install','download','check','tune','disable'],default='install')
    parser.add_argument('--context',type=int,choices=list(ultra.CONTEXTS[1:]),default=8192)
    parser.add_argument('--boost',action='store_true',help='Bench extra threads and GPU layers; no overclocking')
    args = parser.parse_args()
    if args.boost and args.action not in ('install','tune'):
        parser.error('--boost is only available with install or tune')
    path = checked_path(Path(args.config).absolute())
    config = read_json(path)
    settings = config.get('localAI') or {}
    profile = ultra.base_profile(settings)
    target = checked_path(ultra.root(settings))
    target.mkdir(parents=True,exist_ok=True)
    data = Path(config.get('dataDir') or 'data')
    if not data.is_absolute():data = path.parent/data
    with exclusive_file(checked_path(data)/'local-inference.lock'):
        receipt = target/'ready.json'
        if args.action == 'disable':
            receipt.unlink(missing_ok=True)
            print('Qwen Ultra disabled. Existing models and conversations are retained.')
            return
        if args.action in ('install','download'):
            original = read_json(settings['manifestPath'])
            manifest = {k:v for k,v in original.items() if k.startswith('runtime')}
            manifest.update(ultra.SPEC,sourceModel=ultra.SOURCE,sourceModelRevision=ultra.SOURCE_REVISION,
                            license='apache-2.0',modelVariant='ultra')
            download_model(Path(profile['modelPath']),ultra.SPEC)
            if Path(profile['manifestPath']).exists() and read_json(profile['manifestPath']) != manifest:
                raise LocalFailure('Existing Ultra manifest differs. No installation was overwritten.')
            atomic_json(Path(profile['manifestPath']),manifest)
        live = LlamaRuntime(profile,lambda:False,print)
        try:live.verify()
        finally:live.http.close()
        if args.action in ('check','download'):
            print('Ultra model and runtime verified. Run Tune to enable it; no account default changed.')
            return
        quiet_machine()
        # A failed context upgrade must not discard a still-valid smaller context.
        # Stale/mismatched receipts and ordinary revalidation remain fail-closed.
        try:
            keep_smaller = ultra.readiness(profile)['maxContextTokens'] < args.context
        except (OSError,ValueError,KeyError,TypeError):
            keep_smaller = False
        if not keep_smaller:
            receipt.unlink(missing_ok=True)
        try:
            profiles = tune(profile,target,args.context,boost=args.boost)
        except Exception:
            if keep_smaller:
                print('Larger-context test failed. The previously verified smaller context remains enabled.',flush=True)
            raise
        atomic_json(receipt,dict(schema=ultra.SCHEMA,passed=True,id=secrets.token_hex(16),
            enabledAt=time.time(),profiles=profiles,maxContextTokens=args.context,
            manifestSha256=digest_file(profile['manifestPath']),modelStamp=stamp(profile['modelPath']),
            runtimeStamp=stamp(profile['runtimePath'])))
        b = profiles['auto']['benchmark']
        print(f"Qwen Ultra ready: {profiles['auto']['profile']['threads']} threads, "
              f"{profiles['auto']['profile']['gpuLayers']} GPU layers; {b['generationTokensPerSecond']:.2f} tokens/sec.")
        if 'boost' in profiles:
            boosted = profiles['boost']
            print(f"Optional Boost validated: {boosted['profile']['threads']} threads, "
                  f"{boosted['profile']['gpuLayers']} GPU layers; "
                  f"{boosted['benchmark']['generationTokensPerSecond']:.2f} tokens/sec. "
                  'Selecting Boost does not guarantee a speedup for every prompt.')
        print('Refresh Venture and select Qwen Ultra. No account default, BIOS, power plan, or clock was changed.')
        print('Image analysis uses the existing sequential visual worker when installed. This benchmark does not certify OBS coexistence.')


if __name__ == '__main__':
    try:main()
    except BlockingIOError:
        raise SystemExit('Qwen is busy. Wait for its active request or installer to finish, then rerun; no work was interrupted.') from None
    except (OSError,ValueError,KeyError,requests.RequestException,subprocess.SubprocessError) as error:
        raise SystemExit('Ultra setup stopped: '+str(error)) from None
