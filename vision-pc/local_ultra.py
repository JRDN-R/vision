"""Pinned 27B profile, validated contexts, and bounded long-response generation."""
import hashlib
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
CONTEXTS = (4096, 8192, 16384, 32768, 65536, 131072, 262144)
MAX_TESTED = 'max-tested'  # Resolve against verified hardware receipt at job acceptance.
CONTEXT_MARGIN = 256
MAX_RESPONSE_PASSES = 64
MAX_RESPONSE_CHARS = 1_000_000
DEFAULTS = dict(contextTokens=MAX_TESTED, thinking=True, reasoningMode='deep', longResponses=True, performance='auto',
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
    if ((result['contextTokens'] != MAX_TESTED and
            (type(result['contextTokens']) is not int or result['contextTokens'] not in CONTEXTS)) or
            type(result['thinking']) is not bool or type(result['longResponses']) is not bool or
            result['reasoningMode'] not in ('standard', 'deep') or
            result['performance'] not in ('auto','cpu','boost')):
        raise LocalFailure('Choose supported Ultra context, thinking, reasoning, long-response and performance settings.')
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
        reserveRamGiB=max(10,float(settings.get('reserveRamGiB',10))), maxRunSeconds=18000,
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
            receipt.get('maxContextTokens') not in CONTEXTS[1:]):
        raise LocalFailure('Qwen Ultra needs its installer benchmark before use.')
    return receipt


def select_profile(settings, options=None):
    from local_runtime import LocalFailure
    controls = normalize(options)
    profile = base_profile(settings)
    receipt = readiness(profile)
    chosen_context = (receipt['maxContextTokens'] if controls['contextTokens'] == MAX_TESTED
                      else controls['contextTokens'])
    if chosen_context > receipt['maxContextTokens']:
        raise LocalFailure('This Ultra context has not been tested on the server. Run Tune with the larger context first.')
    if controls['performance'] == 'boost' and 'boost' not in receipt['profiles']:
        raise LocalFailure('Ultra Boost is not measured on this PC. Run Qwen-Ultra.ps1 -Action Tune -Boost before selecting it.')
    selected = receipt['profiles'][controls['performance']]
    profile.update(selected['profile'])
    profile.update(contextTokens=chosen_context, thinking=controls['thinking'],
                   reasoningMode=controls['reasoningMode'],
                   longResponses=controls['longResponses'],
                   sampling={k:controls[k] for k in RANGES}, ultraPerformance=controls['performance'],
                   ultraBenchmark=selected['benchmark'], ultraReceipt=receipt['id'])
    return profile


def check_ready(profile):
    from local_runtime import LocalFailure
    receipt = readiness(profile)
    if profile['ultraPerformance'] == 'boost' and 'boost' not in receipt['profiles']:
        raise LocalFailure('Ultra Boost was not tested on this PC. Retune before retrying.')
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
                          deepReasoning=True,
                          longResponses=True, codeGeneration=True, codeExecution=False, webSearch=False))
    try:
        profile = base_profile(settings)
        if Path(profile['modelPath']).is_file():
            result['status'] = 'needs-tuning'
        receipt = readiness(profile)
        result.update(enabled=settings.get('enabled') is True,
            status='ready' if settings.get('enabled') is True else 'disabled',
            contextTokens=receipt['maxContextTokens'],
            contexts=[n for n in CONTEXTS if n <= receipt['maxContextTokens']],
            performance={k:dict(threads=v['profile']['threads'],gpuLayers=v['profile']['gpuLayers'],
                               benchmark=v['benchmark']) for k,v in receipt['profiles'].items()})
    except (OSError,ValueError,KeyError,TypeError):
        pass
    return result


def benchmark_estimate(profile, prompt_tokens, output_tokens, elapsed=0):
    """Budget estimate for single-pass jobs, not an unknown-length continuation."""
    if profile.get('longResponses') or deep_reasoning(profile):
        return None
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


def long_response(profile):
    # Old queued snapshots have no flag and retain their original contract.
    return profile.get('modelVariant') == 'ultra' and profile.get('longResponses') is True


def deep_reasoning(profile):
    # Accepted jobs are immutable. Old snapshots keep their original behavior.
    return profile.get('modelVariant') == 'ultra' and profile.get('reasoningMode') == 'deep'


def response_budget(context_tokens, prompt_tokens, answer_reserve, thinking, thinking_limit=None):
    """Use actual rendered prompt size; leave space for visible output and closing tags."""
    from local_runtime import LocalFailure
    if (type(context_tokens) is not int or context_tokens not in CONTEXTS or
            type(prompt_tokens) is not int or prompt_tokens < 0 or
            type(answer_reserve) is not int or not 512 <= answer_reserve <= 4096):
        raise LocalFailure('Invalid long-response budget.')
    available = context_tokens - prompt_tokens - CONTEXT_MARGIN
    if available < answer_reserve:
        raise LocalFailure('The saved request leaves too little answer space in the tested context. '
                           'Tune/select a larger context or reduce the selected answer reserve. Partial output is retained.')
    reserve = answer_reserve
    thought = max(0, available-reserve-64) if thinking else 0
    if thinking_limit is not None:
        if type(thinking_limit) is not int or thinking_limit < 0:
            raise LocalFailure('Invalid bounded thinking budget.')
        thought = min(thought, thinking_limit)
        available = min(available, reserve + thought + 64)
    return dict(maxTokens=available, answerReserveTokens=reserve, thinkingBudgetTokens=thought)


def continuation_messages(runtime, original, answer, context_tokens, reserve):
    """Keep original instructions/evidence; use a labelled answer tail only when required."""
    from local_runtime import LocalFailure
    tail = answer
    while tail:
        clipped = len(tail) < len(answer)
        instruction = ('Continue the unfinished answer immediately after the supplied ending, without repeating it. '
                       'Keep following the original request and system instructions. Stop when the answer is complete. '
                       'Do not invent missing facts or claim to see omitted text.')
        if clipped:
            instruction += ' Only the ending of your saved answer is included here; earlier sections remain saved but are omitted from this context.'
        messages = [*original, dict(role='assistant', content=tail), dict(role='user', content=instruction)]
        count = runtime.count(messages)
        if count + reserve + CONTEXT_MARGIN <= context_tokens:
            return messages, count, clipped
        if len(tail) <= 256:
            break
        tail = answer[-max(256, len(tail)*3//4):]
    raise LocalFailure('There is not enough tested context to continue this answer without dropping its original instructions. '
                       'The partial answer is saved; use a larger validated context.')


def response_events(runtime, messages, answer_reserve, context_tokens, profile, thinking_limit=None):
    """One durable job; native reasoning reserve, then bounded visible continuations.

    The caller owns the same deadline/cancel callback for every pass. Never retry
    stop/refusal/filter/error results. Private reasoning is neither retained nor
    exposed. No new model, external call, or instruction override is introduced.
    """
    from local_progress import summary_events
    if deep_reasoning(profile):
        from local_reasoning import deep_events
        yield from deep_events(runtime, messages, answer_reserve, context_tokens, profile)
        return
    if not long_response(profile):
        original_budget = getattr(runtime, 'thinking_budget', None)
        try:
            budget = answer_reserve
            if thinking_limit is not None:
                bounded = response_budget(context_tokens, runtime.count(messages), answer_reserve,
                                          runtime.thinking, thinking_limit)
                runtime.thinking_budget = bounded['thinkingBudgetTokens']
                budget = bounded['maxTokens']
            yield from summary_events(runtime.generate(messages, budget))
        finally:
            runtime.thinking_budget = original_budget
        return
    from local_runtime import LocalFailure
    original_thinking = getattr(runtime, 'thinking', profile.get('thinking', True))
    original_budget = getattr(runtime, 'thinking_budget', None)
    answer, seen, totals = '', set(), {}
    direct_recovery = False
    clipped_any = False
    progress = {}
    try:
        for part in range(1, MAX_RESPONSE_PASSES + 1):
            if runtime.cancelled():
                raise InterruptedError()
            # Continuations write the existing answer instead of redoing its private thought process.
            runtime.thinking = bool(original_thinking and part == 1)
            if answer:
                current, count, clipped = continuation_messages(runtime, messages, answer, context_tokens, answer_reserve)
                clipped_any |= clipped
            else:
                current, count = messages, runtime.count(messages)
            budget = response_budget(context_tokens, count, answer_reserve, runtime.thinking, thinking_limit)
            runtime.thinking_budget = budget['thinkingBudgetTokens']
            progress = dict(passNumber=part, completedPasses=part-1, inputTokens=count,
                            continuationContextTruncated=clipped_any, directRecovery=direct_recovery, **budget)
            yield {'localGeneration': progress,
                   'localPhase': 'Continuing saved answer' if answer else 'Writing a direct response' if direct_recovery else 'Working on your answer'}
            visible, finish, pass_usage = '', None, {}
            for event in summary_events(runtime.generate(current, budget['maxTokens'])):
                if runtime.cancelled():
                    raise InterruptedError()
                if event.get('error'):
                    raise LocalFailure('The local model reported a generation failure. Partial output is retained.')
                if isinstance(event.get('usage'), dict):
                    pass_usage = {k:v for k,v in event['usage'].items()
                                  if k in ('prompt_tokens','completion_tokens','total_tokens') and type(v) is int and v >= 0}
                forwarded = {'choices': []}
                for field in ('localThinking', 'localThinkingCharacters', 'localProgressSummaries', 'localActivity'):
                    if field in event:
                        forwarded[field] = event[field]
                if isinstance(event.get('timings'), dict):
                    forwarded['timings'] = event['timings']
                for choice in event.get('choices', []):
                    delta = choice.get('delta') or {}
                    content = delta.get('content') or delta.get('refusal') or ''
                    if not isinstance(content, str):
                        raise LocalFailure('Invalid local answer stream. Partial output is retained.')
                    visible += content
                    if len(answer) + len(visible) > MAX_RESPONSE_CHARS:
                        raise LocalFailure('The saved answer reached the one-million-character retention limit. Partial output is retained.')
                    finish = choice.get('finish_reason') or finish
                    # The worker only sees the terminal finish reason after all continuations.
                    forwarded['choices'].append({'delta': {'content': content}})
                yield forwarded
            for key, value in pass_usage.items():
                totals[key] = totals.get(key, 0) + value
            progress = {**progress, 'completedPasses': part, 'usage': dict(totals)}
            yield {'localGeneration': progress, 'usage': dict(totals)}
            if not finish:
                raise LocalFailure('The local stream disconnected before a final result. Partial output is retained; it was not replayed.')
            normalized = ''.join(visible.split())
            digest = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
            if len(normalized) >= 256 and digest in seen:
                yield {'localRetractCharacters': len(visible)}
                yield {'localGeneration': {**progress, 'message': 'Qwen repeated a saved section. The duplicate was removed and the partial answer retained.'},
                       'choices': [{'delta': {}, 'finish_reason': 'length'}], 'usage': dict(totals)}
                return
            seen.add(digest)
            answer += visible
            if finish != 'length':
                # Includes a normal refusal: a completed refusal is not a failed generation.
                yield {'choices': [{'delta': {}, 'finish_reason': finish}], 'usage': dict(totals)}
                return
            if not visible.strip():
                if direct_recovery:
                    yield {'localGeneration': {**progress, 'message': 'Qwen produced no visible text even after one direct-response recovery. Partial output is retained; no more retries were started.'},
                           'choices': [{'delta': {}, 'finish_reason': 'length'}], 'usage': dict(totals)}
                    return
                direct_recovery = True
        yield {'localGeneration': {**progress, 'message': 'This request reached 64 generation passes. The partial answer is saved; no unbounded retry loop was started.'},
               'choices': [{'delta': {}, 'finish_reason': 'length'}], 'usage': dict(totals)}
    finally:
        runtime.thinking = original_thinking
        runtime.thinking_budget = original_budget
