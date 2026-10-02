"""Install-time model download and offline CPU validation; never reads Vision secrets."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave

MODEL_REPO = "Systran/faster-whisper-small.en"
MODEL_REVISION = "d1d751a5f8271d482d14ca55d9e2deeebbae577f"
MODEL_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")


def prepare(packages_path: Path, model_path: Path, download: bool) -> None:
    if not packages_path.is_absolute() or not packages_path.is_dir():
        raise ValueError("A private absolute packages directory is required.")
    if not model_path.is_absolute():
        raise ValueError("An absolute model directory is required.")
    # The optional packages only affect this short-lived subprocess.
    sys.path.insert(0, str(packages_path))
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "120"
    os.environ["OMP_NUM_THREADS"] = "4"
    os.environ["HF_HUB_OFFLINE"] = "0" if download else "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "0" if download else "1"
    if download:
        from huggingface_hub import snapshot_download

        print("Downloading the pinned English Whisper model (about 486 MB)...", flush=True)
        snapshot_download(
            repo_id=MODEL_REPO,
            revision=MODEL_REVISION,
            allow_patterns=list(MODEL_FILES),
            local_dir=str(model_path),
            token=False,
            max_workers=2,
        )
    missing = [name for name in MODEL_FILES if not (model_path / name).is_file()]
    if missing:
        raise ValueError("The local model is incomplete: " + ", ".join(missing))
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from faster_whisper import WhisperModel
    import numpy as np

    print("Checking the local model on CPU with four threads...", flush=True)
    model = WhisperModel(
        str(model_path), device="cpu", compute_type="int8", cpu_threads=4,
        num_workers=1, local_files_only=True,
    )
    # Loading alone can miss failures in inference DLLs or kernels. Decode one
    # second of silence without downloading audio or enabling a paid provider.
    segments, _ = model.transcribe(
        np.zeros(16000, dtype=np.float32), language="en", beam_size=1,
        vad_filter=False, condition_on_previous_text=False,
    )
    list(segments)
    # The real worker also decodes WAV files and runs the bundled ONNX speech
    # filter. An array-only inference check misses failures in either path.
    with tempfile.TemporaryDirectory(prefix='vision-audio-check-') as temp:
        audio = Path(temp) / 'silence.wav'
        write_check_audio(audio)
        segments, _ = model.transcribe(str(audio), language='en', vad_filter=True,
                                      condition_on_previous_text=False)
        list(segments)
    (model_path / "vision-model.json").write_text(json.dumps({
        "repository": MODEL_REPO, "revision": MODEL_REVISION,
        "device": "cpu", "computeType": "int8", "cpuThreads": 4,
    }, indent=2) + "\n", encoding="utf-8")
    print("Local model, CPU inference, audio decoding, and speech filter passed.", flush=True)


def write_check_audio(path):
    """Two seconds of generated silence; no user recording or cloud service."""
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b'\0' * 64000)


def check_server(config_path: Path, timeout=120):
    """Exercise MP3/WAV, VAD and persistence under the running task's identity."""
    config_path = config_path.resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    root = config_path.parent
    data_dir = Path(config.get('dataDir') or 'data')
    if not data_dir.is_absolute():
        data_dir = root / data_dir
    ffmpeg = Path(config.get('ffmpeg') or 'tools/ffmpeg.exe')
    if not ffmpeg.is_absolute():
        ffmpeg = root / ffmpeg
    port = int(config.get('port', 8765))
    if not 1024 <= port <= 65535:
        raise ValueError('Invalid local processor port.')
    state_path = data_dir / 'local-transcription-check.json'
    if state_path.is_file():
        saved = json.loads(state_path.read_text(encoding='utf-8'))
    else:
        saved = {'projectId': 'vision_check_' + secrets.token_hex(16), 'projectKey': secrets.token_urlsafe(36)}
        state_path.write_text(json.dumps(saved), encoding='utf-8')
    headers = {'Authorization': 'Bearer ' + config['token'],
               'X-Vision-Project-Key': saved['projectKey'], 'Content-Type': 'application/json'}
    # No proxy or external network is needed for this check. Do not print keys.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def call(method, path, body=None):
        payload = None if body is None else json.dumps(body).encode('utf-8')
        request = urllib.request.Request(f'http://127.0.0.1:{port}/api' + path, data=payload, headers=headers, method=method)
        with opener.open(request, timeout=10) as response:
            return json.load(response)
    health = call('GET', '/health')
    if not health.get('localTranscription', {}).get('available'):
        raise RuntimeError('The local transcription plugin is not enabled and ready.')
    base = '/projects/' + saved['projectId']
    try:
        call('GET', base)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        call('PUT', base, {'project': {'name': 'Local transcription check', 'nodes': []}, 'revision': 0})
    with tempfile.TemporaryDirectory(prefix='local-check-', dir=data_dir) as temp:
        wav, mp3 = Path(temp) / 'check.wav', Path(temp) / 'check.mp3'
        write_check_audio(wav)
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        subprocess.run([str(ffmpeg), '-v', 'error', '-nostdin', '-y', '-i', str(wav), '-c:a', 'libmp3lame', str(mp3)],
                       check=True, timeout=30, **flags)
        sections = [{'start': i*2, 'end': i*2+2, 'mimeType': mime,
                     'audioData': 'data:' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode('ascii')}
                    for i, (path, mime) in enumerate(((wav, 'audio/wav'), (mp3, 'audio/mpeg')))]
        receipt = call('POST', base + '/transcriptions', {'clientRequestId': 'check_' + secrets.token_hex(12),
                       'sourceName': 'Generated audio check', 'sections': sections})
    url = base + '/transcriptions/' + receipt['id']
    print('Checking generated WAV and MP3 through the background processor...', flush=True)
    deadline, previous, finished = time.monotonic() + timeout, None, False
    try:
        while time.monotonic() < deadline:
            value = call('GET', url)
            if value['phase'] != previous:
                print(value['phase'], flush=True)
                previous = value['phase']
            if value['status'] == 'complete':
                finished = True
                print('PASS: background worker, WAV/MP3 decoding, Whisper, speech filter, and saved result.', flush=True)
                return
            if value['status'] in ('error', 'cancelled'):
                finished = True
                print_worker_diagnostic(data_dir / 'server.log', receipt['id'])
                raise RuntimeError(value.get('error') or 'The check was cancelled.')
            time.sleep(1)
        raise RuntimeError('The check did not finish within two minutes. The PC may be busy or the worker may be stalled. Check Activity and the PC server log.')
    finally:
        if not finished:
            try:
                call('DELETE', url)  # cancel only this generated check, never other work
            except Exception:
                pass


def print_worker_diagnostic(log_path, job_id):
    """Print only this generated check's worker failure, excluding other jobs."""
    try:
        with log_path.open('rb') as source:
            source.seek(max(0, log_path.stat().st_size - 96*1024))
            text = source.read().decode('utf-8', errors='replace')
        marker = f'Local transcription job {job_id} worker exited '
        if marker in text:
            failure = text.rsplit(marker, 1)[1]
            failure = re.split(r'\n\d{4}-\d{2}-\d{2} ', failure, maxsplit=1)[0]
            print('PC worker diagnostic:\n' + '\n'.join(failure.splitlines()[-18:]), file=sys.stderr)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages-path", type=Path)
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--check-server", type=Path, metavar='CONFIG', help='Check the running processor with generated audio; no downloads')
    args = parser.parse_args()
    try:
        if args.check_server:
            check_server(args.check_server)
        elif args.packages_path and args.model_path:
            prepare(args.packages_path, args.model_path, args.download)
        else:
            parser.error('Supply --check-server CONFIG or --packages-path and --model-path.')
    except Exception as exc:
        print(f"Local transcription setup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("This check did not change the existing Vision configuration.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
