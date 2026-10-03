"""Optional offline PretrainedSED/BEATs inference in an isolated interpreter.

Only the setup command downloads assets. The HTTP/Whisper processes may import
this module without importing PyTorch. Predictions are approximate sound labels,
not a substitute for the speech transcript or evidence of a particular cause.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time
import traceback

ENGINE = 'PretrainedSED-BEATs'
UPSTREAM_COMMIT = '1aa47e482f7e89904cba2338999345025d8b4e36'
CHECKPOINT_SHA256 = 'db13a79ae90a0cfd0f9911a6a1d8cdb89324322bee642dcfe32de022123b8b54'
SAMPLE_RATE = 16000
WINDOW_SECONDS = 10
FRAME_SECONDS = 0.04
MAX_EVENTS = 20000
_ACTIVE_DECODE_LOCK = threading.Lock()
_ACTIVE_DECODE = None
# Drop spoken-language labels already handled by Whisper, and ontology roots
# that provide no useful caption. Nonverbal human sounds remain enabled.
IGNORED_LABELS = frozenset({
    'Speech', 'Male speech, man speaking', 'Female speech, woman speaking',
    'Child speech, kid speaking', 'Conversation', 'Narration, monologue',
    'Speech synthesizer', 'Whispering', 'Babbling', 'Human voice',
    'Human sounds', 'Human group actions', 'Human locomotion', 'Animal',
    'Vehicle', 'Domestic sounds, home sounds', 'Source-ambiguous sounds',
    'Channel, environment and background', 'Inside, small room',
    'Inside, large room or hall', 'Inside, public space',
    'Outside, urban or manmade', 'Outside, rural or natural',
    'Background noise', 'Environmental noise', 'Noise', 'Silence',
})


def capability(settings):
    settings = settings if isinstance(settings, dict) else {}
    enabled = settings.get('enabled') is True
    assets = Path(settings.get('assetsPath') or '__sound_assets_not_installed__')
    python = Path(settings.get('pythonPath') or '__sound_python_not_installed__')
    installed = python.is_file() and (assets / 'BEATs_strong_1.pt').is_file()
    ready = False
    try:
        marker = json.loads((assets / 'ready.json').read_text(encoding='utf-8'))
        if not isinstance(marker, dict):
            raise ValueError('invalid-sound-ready-marker')
        ready = installed and marker.get('engine') == ENGINE and marker.get('upstreamCommit') == UPSTREAM_COMMIT
        ready = ready and marker.get('checkpointSha256') == CHECKPOINT_SHA256
        ready = ready and marker.get('device') == settings.get('device', 'cpu')
        ready = ready and all((assets / item['path']).is_file() for item in _manifest()['files'])
    except (OSError, ValueError, TypeError, KeyError):
        pass
    status = 'ready' if enabled and ready else ('disabled' if not enabled else 'not-installed')
    return {'engine': ENGINE, 'model': 'BEATs AudioSet Strong (447 labels)',
            'enabled': enabled, 'installed': bool(installed), 'ready': bool(enabled and ready),
            'available': bool(enabled and ready), 'status': status}


def _manifest():
    return json.loads(Path(__file__).with_name('sound-model-manifest.json').read_text(encoding='utf-8'))


def verify_assets(assets):
    assets = Path(assets).resolve()
    for item in _manifest()['files']:
        path = (assets / item['path']).resolve()
        if not path.is_relative_to(assets) or not path.is_file():
            raise RuntimeError('sound-assets-missing')
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != item['sha256']:
            raise RuntimeError('sound-assets-checksum')


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        out.flush()
        os.fsync(out.fileno())
    for attempt in range(6):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.02 * (attempt + 1))


def decode_window(probabilities, labels, *, offset=0.0, duration=10.0,
                  threshold=0.5, median_frames=9, ignored=IGNORED_LABELS):
    """Decode class x frame probabilities on the fixed 10-second training grid.

    Padding is clipped, never stretched to the final window's shorter length.
    Timestamps approximate the model's 40 ms frame bins, not sample accuracy.
    """
    if len(probabilities) != len(labels) or not 0 < threshold <= 1:
        raise ValueError('invalid-sound-predictions')
    if not math.isfinite(offset) or not math.isfinite(duration) or duration <= 0 or duration > 10:
        raise ValueError('invalid-sound-duration')
    if median_frames < 1 or median_frames % 2 != 1:
        raise ValueError('invalid-sound-median')
    events = []
    for label, row in zip(labels, probabilities):
        if label in ignored:
            continue
        values = [float(v) for v in row]
        if len(values) != 250 or not all(math.isfinite(v) and 0 <= v <= 1 for v in values):
            raise ValueError('invalid-sound-frame-grid')
        half = median_frames // 2
        padded = [values[0]] * half + values + [values[-1]] * half
        scores = [statistics.median(padded[i:i + median_frames]) for i in range(250)]
        start = None
        for index in range(251):
            active = index < 250 and index * FRAME_SECONDS < duration and scores[index] >= threshold
            if active and start is None:
                start = index
            elif not active and start is not None:
                end = min(index * FRAME_SECONDS, duration)
                events.append({'start': round(offset + start * FRAME_SECONDS, 4),
                               'end': round(offset + end, 4), 'label': label,
                               'score': round(max(values[start:index]), 5)})
                start = None
    return events


def ontology_ancestors(ontology):
    """Name-to-ancestor names from the pinned official AudioSet DAG."""
    by_id = {item['id']: item for item in ontology}
    parents = {key: set() for key in by_id}
    for item in ontology:
        for child in item.get('child_ids', []):
            if child in parents:
                parents[child].add(item['id'])
    def walk(key, seen):
        result = set()
        for parent in parents[key] - seen:
            result.add(by_id[parent]['name'])
            result.update(walk(parent, seen | {parent}))
        return result
    return {item['name']: walk(key, {key}) for key, item in by_id.items()}


def postprocess_events(events, ancestors=None, *, merge_gap=0.2, min_duration=0.12):
    """Join chunk boundaries, filter brief events, suppress redundant parents.

    A parent is suppressed only when similarly confident descendants cover at
    least 80% of its interval. A brief meow does not erase a long cat event.
    """
    groups = {}
    for event in events:
        start, end, score = (float(event[key]) for key in ('start', 'end', 'score'))
        if not all(math.isfinite(v) for v in (start, end, score)) or not 0 <= start < end or not 0 <= score <= 1:
            raise ValueError('invalid-sound-event')
        label = event['label']
        if not isinstance(label, str) or not label or label in IGNORED_LABELS:
            continue
        groups.setdefault(label, []).append({'start': start, 'end': end, 'label': label, 'score': score})
    joined = []
    for group in groups.values():
        current = None
        for event in sorted(group, key=lambda item: (item['start'], item['end'])):
            if current and event['start'] <= current['end'] + merge_gap + 1e-8:
                current['end'] = max(current['end'], event['end'])
                current['score'] = max(current['score'], event['score'])
            else:
                if current:
                    joined.append(current)
                current = dict(event)
        if current:
            joined.append(current)
    joined = [e for e in joined if e['end'] - e['start'] + 1e-8 >= min_duration]
    if len(joined) > MAX_EVENTS:
        raise RuntimeError('too-many-sound-events')
    if ancestors:
        descendants = {}
        for event in joined:
            for parent in ancestors.get(event['label'], ()):
                descendants.setdefault(parent, []).append(event)
        index = {}
        for parent, children in descendants.items():
            children.sort(key=lambda e: e['start'])
            index[parent] = (children, [e['start'] for e in children])
        retained = []
        for event in joined:
            children, starts = index.get(event['label'], ([], []))
            intervals = [(max(event['start'], child['start']), min(event['end'], child['end']))
                         for child in children[:bisect.bisect_left(starts, event['end'])]
                         if child['end'] > event['start'] and child['score'] >= event['score'] - 0.1]
            coverage, cursor = 0.0, event['start']
            for begin, end in intervals:
                coverage += max(0.0, end - max(cursor, begin))
                cursor = max(cursor, end)
            if coverage < 0.8 * (event['end'] - event['start']):
                retained.append(event)
        joined = retained
    return sorted(joined, key=lambda e: (e['start'], e['end'], e['label']))


def sound_caption(label):
    return {'Meow': 'cat meows', 'Bark': 'dog barks', 'Wail, moan': 'wail/moan',
            'Smash, crash': 'smash/crash'}.get(label, label.lower())


def sound_events_srt(events):
    def stamp(seconds):
        ms = max(0, round(seconds * 1000))
        return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'
    return '\n\n'.join(f'{i}\n{stamp(e["start"])} --> {stamp(e["end"])}\n*{sound_caption(e["label"])}*'
                      for i, e in enumerate(sorted(events, key=lambda x: x['start']), 1))


def load_model(assets, device='cpu', threads=2):
    verify_assets(assets)
    import torch
    torch.set_num_threads(max(1, min(4, int(threads))))
    torch.set_num_interop_threads(1)
    if device not in ('cpu', 'cuda') or (device == 'cuda' and not torch.cuda.is_available()):
        raise RuntimeError('sound-device-unavailable')
    # Import only the BEATs inference modules. The upstream convenience script
    # imports every architecture and dataset dependencies, unnecessary here.
    sys.path.insert(0, str(Path(assets).resolve() / 'PretrainedSED'))
    from models.beats.BEATs_wrapper import BEATsWrapper
    from models.prediction_wrapper import PredictionsWrapper
    from data_util.audioset_classes import as_strong_train_classes
    if len(as_strong_train_classes) != 447:
        raise RuntimeError('sound-label-count')
    model = PredictionsWrapper(BEATsWrapper(), checkpoint=None)
    state = torch.load(Path(assets) / 'BEATs_strong_1.pt', map_location='cpu', weights_only=True)
    # Same key conversion as upstream PredictionsWrapper.load_checkpoint, but
    # no implicit download and no dropping mismatched classifier weights.
    state = {('model.beats.' + k[len('model.model.'):] if k.startswith('model.model.') else k): v
             for k, v in state.items()}
    model.load_state_dict(state, strict=True)
    model.eval().to(device)
    return model, list(as_strong_train_classes), torch


def predict_window(model, torch, pcm, device):
    import numpy as np
    samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0
    if len(samples) > SAMPLE_RATE * WINDOW_SECONDS:
        raise ValueError('sound-window-too-long')
    samples = np.pad(samples, (0, SAMPLE_RATE * WINDOW_SECONDS - len(samples)))
    waveform = torch.from_numpy(samples).unsqueeze(0).to(device)
    with torch.inference_mode():
        logits, _ = model(model.mel_forward(waveform))
        if tuple(logits.shape) != (1, 447, 250):
            raise RuntimeError('sound-model-output-shape')
        return torch.sigmoid(logits)[0].float().cpu().numpy()


def process_directory(directory, assets, ffmpeg, device='cpu', threads=2):
    global _ACTIVE_DECODE
    directory = Path(directory).resolve()
    def progress(stage, percent):
        atomic_json(directory / 'sound-progress.json', {'stage': stage, 'phase': 'Recognizing sounds', 'progress': percent})
    progress('model', 0)
    model, labels, torch = load_model(assets, device, threads)
    ancestors = ontology_ancestors(json.loads((Path(assets) / 'ontology.json').read_text(encoding='utf-8')))
    sections = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))['sections']
    if not isinstance(sections, list) or not 1 <= len(sections) <= 256:
        raise ValueError('invalid-sound-sections')
    total = sum(float(s['end']) - float(s['start']) for s in sections)
    if not 0 < total <= 8 * 3600:
        raise ValueError('invalid-sound-duration')
    processed, events, previous_end = 0.0, [], 0.0
    for section in sections:
        source = (directory / section['file']).resolve()
        start, end = float(section['start']), float(section['end'])
        if not source.is_relative_to(directory) or source.parent != directory or not source.is_file():
            raise ValueError('invalid-sound-source')
        if not math.isfinite(start + end) or not previous_end <= start < end <= 8 * 3600:
            raise ValueError('invalid-sound-section-time')
        if section.get('format') not in ('mp3', 'wav'):
            raise ValueError('invalid-sound-audio-format')
        previous_end = end
        span = end - start
        command = [str(ffmpeg), '-nostdin', '-hide_banner', '-loglevel', 'error',
                   '-protocol_whitelist', 'file,pipe', '-f', section['format'], '-i', str(source),
                   '-t', str(span), '-vn', '-sn', '-dn', '-ac', '1', '-ar', str(SAMPLE_RATE), '-f', 's16le', 'pipe:1']
        with _ACTIVE_DECODE_LOCK:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            _ACTIVE_DECODE = process
        section_events, sample_offset = [], 0
        try:
            while True:
                pcm = process.stdout.read(SAMPLE_RATE * WINDOW_SECONDS * 2)
                if not pcm:
                    break
                if len(pcm) % 2:
                    raise RuntimeError('sound-audio-decode')
                sample_count = len(pcm) // 2
                duration = min(sample_count / SAMPLE_RATE, span - sample_offset / SAMPLE_RATE)
                if duration <= 0:
                    break
                probabilities = predict_window(model, torch, pcm, device)
                section_events.extend(decode_window(probabilities, labels,
                    offset=start + sample_offset / SAMPLE_RATE, duration=duration))
                if len(section_events) + len(events) > MAX_EVENTS * 4:
                    raise RuntimeError('too-many-sound-events')
                sample_offset += sample_count
                progress('inference', min(99, 100 * (processed + sample_offset / SAMPLE_RATE) / total))
            if process.wait(timeout=30) != 0 or sample_offset == 0:
                raise RuntimeError('sound-audio-decode')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            with _ACTIVE_DECODE_LOCK:
                if _ACTIVE_DECODE is process:
                    _ACTIVE_DECODE = None
        # Never bridge intentionally excluded section gaps, even short gaps.
        section_events = postprocess_events(section_events, ancestors)
        events.extend(section_events)
        if len(events) > MAX_EVENTS:
            raise RuntimeError('too-many-sound-events')
        processed += span
    result = {'status': 'completed', 'engine': ENGINE, 'model': 'BEATs AudioSet Strong',
              'soundEvents': events,
              'threshold': 0.5, 'medianFrames': 9, 'frameSeconds': FRAME_SECONDS,
              'warnings': []}
    atomic_json(directory / 'sound-result.json', result)
    progress('complete', 100)
    return result


def start_parent_watch():
    descriptor = sys.stdin.fileno()
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        import msvcrt
        peek = ctypes.WinDLL('kernel32', use_last_error=True).PeekNamedPipe
        peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                         wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPDWORD]
        peek.restype = wintypes.BOOL
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
        def wait_for_close():
            while peek(handle, None, 0, None, None, None):
                time.sleep(0.5)
    else:
        def wait_for_close():
            os.read(descriptor, 1)
    def watch():
        try:
            wait_for_close()
        finally:
            with _ACTIVE_DECODE_LOCK:
                if _ACTIVE_DECODE is not None and _ACTIVE_DECODE.poll() is None:
                    try:
                        _ACTIVE_DECODE.kill()
                    except OSError:
                        pass
            os._exit(1)
    threading.Thread(target=watch, name='vision-sound-parent', daemon=True).start()


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--process')
    mode.add_argument('--check', action='store_true')
    parser.add_argument('--assets', required=True)
    parser.add_argument('--ffmpeg')
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
    parser.add_argument('--threads', type=int, default=2)
    args = parser.parse_args()
    def offline_only(event, _args):
        if event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto'):
            raise RuntimeError('Offline sound recognition cannot access the network.')
    sys.addaudithook(offline_only)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', OMP_NUM_THREADS=str(max(1, min(4, args.threads))))
    if os.name != 'nt':
        os.nice(10)
    try:
        if args.check:
            model, labels, torch = load_model(args.assets, args.device, args.threads)
            probabilities = predict_window(model, torch, bytes(SAMPLE_RATE * WINDOW_SECONDS * 2), args.device)
            if not torch.isfinite(torch.from_numpy(probabilities)).all().item():
                raise RuntimeError('sound-nonfinite-output')
            print(json.dumps({'ready': True, 'engine': ENGINE, 'upstreamCommit': UPSTREAM_COMMIT,
                              'checkpointSha256': CHECKPOINT_SHA256, 'device': args.device,
                              'classes': len(labels), 'frames': 250}))
        else:
            if not args.ffmpeg:
                raise ValueError('sound-ffmpeg-required')
            start_parent_watch()
            process_directory(args.process, args.assets, args.ffmpeg, args.device, args.threads)
    except Exception as error:
        traceback.print_exc()
        if args.process:
            atomic_json(Path(args.process) / 'sound-result.json', {
                'status': 'failed', 'engine': ENGINE, 'soundEvents': [],
                'error': 'Sound recognition could not finish on FUPCJ Server. The speech transcript is preserved.',
                'exception': type(error).__name__})
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
