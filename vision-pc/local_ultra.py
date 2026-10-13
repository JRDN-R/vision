"""Pinned, independent 27B model and strictly bounded per-request controls."""
import math
from pathlib import Path

MODEL = 'qwen-ultra-local'
SPEC = dict(modelRepository='JunLee88999/Qwen3.5-27B-heretic-v2-GGUF',
    modelRevision='85183955df1ec9eb613a816e0a6afc74e2807abb',
    modelFile='Qwen3.5-27B-heretic-v2-Q4_K_M.gguf', modelBytes=16540271744,
    modelSha256='56275d30c9e49a02584f5fcd2569275ac8c2c9e524d42203755bd4436cb38b7a',
    runtimeCommit='f2cc7282ce0042641df55e0e563b75e824e2b647')
SOURCE = 'llmfan46/Qwen3.5-27B-ultra-uncensored-heretic-v1'
SOURCE_REVISION = '0b134ea2c3e0fef5f4f78194d1c86258c1c81b8f'
SCHEMA = 'vision-ultra-ready-v1'
DEFAULTS = dict(contextTokens=8192, thinking=True, performance='auto',
    temperature=.6, topP=.95, topK=20, minP=0., repeatPenalty=1., presencePenalty=0., seed=-1)
RANGES = dict(temperature=(0.,2.), topP=(.01,1.), topK=(0,100), minP=(0.,1.),
              repeatPenalty=(.8,1.5), presencePenalty=(-2.,2.), seed=(-1,2147483647))


def normalize(value=None):
    from local_runtime import LocalFailure
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise LocalFailure('Invalid Qwen Ultra controls.')
    result = {**DEFAULTS, **value}
    if (type(result['contextTokens']) is not int or result['contextTokens'] not in (4096,8192,16384) or
            type(result['thinking']) is not bool or result['performance'] not in ('auto','cpu')):
        raise LocalFailure('Choose a supported Ultra context, thinking setting and performance profile.')
    for key, (low, high) in RANGES.items():
        x = result[key]
        if (type(x) not in (int,float) or not math.isfinite(x) or not low <= x <= high or
                (key in ('topK','seed') and type(x) is not int)):
            raise LocalFailure('Invalid Ultra ' + key + ' value.')
    return result


def root(settings):
    from local_variants import checked_path
    return checked_path(settings['modelPath']).parent / 'ultra' / SPEC['modelRevision']


def base_profile(settings):
    from local_variants import checked_path, PROFILE_FIELDS
    target = root(settings)
    profile = {k:v for k,v in settings.items() if k in PROFILE_FIELDS}
    profile.update(modelVariant='ultra', modelId=MODEL, modelPath=str(target / SPEC['modelFile']),
        manifestPath=str(target / 'installed-manifest.json'), contextTokens=8192, maxOutputTokens=4096,
        threads=8, gpuLayers=0, batchSize=256, ubatchSize=64, maxResidentGiB=30,
        reserveRamGiB=max(10,float(settings.get('reserveRamGiB',10))), maxRunSeconds=7200,
        visualManifestPath=str(checked_path(settings.get('visualManifestPath') or
            checked_path(settings['modelPath']).parent / 'visual' / 'visual-worker-manifest.json')))
    return profile


def readiness(profile):
    from local_variants import read_json, stamp
    from local_runtime import digest_file, LocalFailure
    manifest = read_json(profile['manifestPath'])
    receipt = read_json(Path(profile['manifestPath']).parent / 'ready.json')
    if (any(manifest.get(k) != v for k,v in SPEC.items()) or
            receipt.get('schema') != SCHEMA or receipt.get('passed') is not True or
            receipt.get('manifestSha256') != digest_file(profile['manifestPath']) or
            receipt.get('modelStamp') != stamp(profile['modelPath']) or
            receipt.get('runtimeStamp') != stamp(profile['runtimePath']) or
            stamp(profile['modelPath'])[0] != SPEC['modelBytes'] or
            receipt.get('maxContextTokens') not in (8192,16384)):
        raise LocalFailure('Qwen Ultra needs its installer benchmark before use.')
    return receipt


def select_profile(settings, options=None):
    from local_runtime import LocalFailure
    controls = normalize(options)
    profile = base_profile(settings)
    receipt = readiness(profile)
    if controls['contextTokens'] > receipt['maxContextTokens']:
        raise LocalFailure('This Ultra context has not been tested on the server. Run Tune with the larger context first.')
    selected = receipt['profiles'][controls['performance']]
    profile.update(selected['profile'])
    profile.update(contextTokens=controls['contextTokens'], thinking=controls['thinking'],
                   sampling={k:controls[k] for k in RANGES}, ultraPerformance=controls['performance'],
                   ultraBenchmark=selected['benchmark'], ultraReceipt=receipt['id'])
    return profile


def check_ready(profile):
    from local_runtime import LocalFailure
    receipt = readiness(profile)
    selected = receipt['profiles'][profile['ultraPerformance']]
    if (receipt.get('id') != profile.get('ultraReceipt') or
            profile['contextTokens'] > receipt['maxContextTokens'] or
            any(profile.get(k) != v for k,v in selected['profile'].items())):
        raise LocalFailure('The Ultra tuning profile changed. Retry explicitly with its current settings.')


def status(settings):
    result = dict(id=MODEL, name='Qwen Ultra', provider='local_llama_cpp', enabled=False,
        status='not-installed', contextTokens=8192, maxOutputTokens=4096,
        nativeContextTokens=262144, contexts=[4096,8192], defaults=DEFAULTS,
        modelBytes=SPEC['modelBytes'], quantization='Q4_K_M', noApiTokenCharge=True,
        capabilities=dict(text=True, projectRetrieval=True, localFiles=True, vision=False,
                          codeGeneration=True, codeExecution=False, webSearch=False))
    try:
        profile = base_profile(settings)
        if Path(profile['modelPath']).is_file():
            result['status'] = 'needs-tuning'
        receipt = readiness(profile)
        result.update(enabled=settings.get('enabled') is True,
            status='ready' if settings.get('enabled') is True else 'disabled',
            contextTokens=receipt['maxContextTokens'],
            contexts=[n for n in (4096,8192,16384) if n <= receipt['maxContextTokens']],
            performance={k:dict(threads=v['profile']['threads'],gpuLayers=v['profile']['gpuLayers'],
                               benchmark=v['benchmark']) for k,v in receipt['profiles'].items()})
    except (OSError,ValueError,KeyError,TypeError):
        pass
    return result


def benchmark_estimate(profile, prompt_tokens, output_tokens, elapsed=0):
    """Budget estimate, not a promise: output may end early; all thinking counts."""
    b = profile.get('ultraBenchmark') or {}
    try:
        rates = [float(b[k]) for k in ('promptTokensPerSecond','generationTokensPerSecond')]
        load = float(b['loadSeconds'])
        if any(not math.isfinite(v) or v <= 0 for v in rates) or not math.isfinite(load) or load < 0:
            return None
        seconds = load + max(1,prompt_tokens)/rates[0] + output_tokens/rates[1]
        low, high = max(5,seconds*.65), max(10,seconds*1.6)
        if elapsed >= high:
            return dict(estimateStatus='overrun',estimatedRemainingRange=None,estimatedRemainingSeconds=None,estimateSamples=1)
        return dict(estimateStatus='estimated',estimatedRemainingRange=[round(max(5,low-elapsed)),round(max(10,high-elapsed))],
                    estimatedRemainingSeconds=round(max(5,seconds-elapsed)),estimateSamples=1, estimateBasis='benchmark-output-budget')
    except (KeyError,ValueError,TypeError,ZeroDivisionError):
        return None
