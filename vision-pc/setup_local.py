"""Install-time model download and offline CPU validation; never reads Vision secrets."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

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
    (model_path / "vision-model.json").write_text(json.dumps({
        "repository": MODEL_REPO, "revision": MODEL_REVISION,
        "device": "cpu", "computeType": "int8", "cpuThreads": 4,
    }, indent=2) + "\n", encoding="utf-8")
    print("Local model load and CPU inference passed.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages-path", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    try:
        prepare(args.packages_path, args.model_path, args.download)
    except Exception as exc:
        print(f"Local transcription setup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Existing Vision configuration was not enabled by this check.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
