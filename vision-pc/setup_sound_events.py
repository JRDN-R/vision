"""Download verified inference assets and test the separate sound-event runtime.

Only the optional runtime executes this file. No model dependency is imported by
the normal Vision processor. Existing matching downloads are reused, and checks
are deliberately offline unless --download was explicitly supplied.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import urllib.parse
import urllib.request


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for part in iter(lambda: source.read(1024 * 1024), b""):
            result.update(part)
    return result.hexdigest()


def linked(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except FileNotFoundError:
        return False


def validate_entry(root: Path, entry: dict) -> Path:
    relative = PurePosixPath(entry["path"])
    if (relative.is_absolute() or not relative.parts or ".." in relative.parts
            or "\\" in str(relative) or ":" in str(relative)):
        raise ValueError("Invalid sound asset path")
    if not re.fullmatch(r"[a-f0-9]{64}", entry.get("sha256", "")):
        raise ValueError("Sound asset is missing a pinned SHA-256")
    url = urllib.parse.urlsplit(entry["url"])
    if url.scheme != "https" or url.hostname not in {
        "raw.githubusercontent.com", "github.com", "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    } or url.username or url.password:
        raise ValueError("Sound asset must come from the pinned HTTPS upstream")
    target = root.joinpath(*relative.parts)
    # Reject links at every path component, including a pre-existing target.
    for path in (root, *target.parents, target):
        if linked(path):
            raise ValueError("Sound asset path cannot be a symbolic link")
    if root.resolve() not in target.resolve().parents:
        raise ValueError("Sound asset escaped its private directory")
    return target


def matches(path: Path, entry: dict) -> bool:
    return (path.is_file()
            and (not entry.get("size") or path.stat().st_size == entry["size"])
            and digest(path) == entry["sha256"])


def prepare_assets(manifest_path: Path, assets: Path, download: bool) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("The pinned sound asset manifest is empty")
    assets.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        target = validate_entry(assets, entry)
        if matches(target, entry):
            print("Verified:", entry["path"], flush=True)
            continue
        if not download:
            raise ValueError("Missing or changed sound asset: " + entry["path"])
        print("Downloading:", entry["path"], flush=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".download-part")
        if linked(temporary):
            raise ValueError("Download destination cannot be a symbolic link")
        try:
            request = urllib.request.Request(entry["url"], headers={"User-Agent": "Vision-Sound-Setup/1"})
            # A release checkpoint may redirect to GitHub's signed download CDN.
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
                if urllib.parse.urlsplit(response.url).scheme != "https":
                    raise ValueError("Sound asset redirected away from HTTPS")
                total = 0
                for part in iter(lambda: response.read(1024 * 1024), b""):
                    total += len(part)
                    limit = int(entry.get("size") or 512 * 1024 * 1024)
                    if total > limit:
                        raise ValueError("Sound asset exceeded its expected size")
                    output.write(part)
            if not matches(temporary, entry):
                raise ValueError("Checksum mismatch for sound asset: " + entry["path"])
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("sound-model-manifest.json"))
    parser.add_argument("--worker", type=Path, default=Path(__file__).with_name("sound_model.py"))
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--threads", type=int, default=2, choices=range(1, 5))
    args = parser.parse_args()
    prepare_assets(args.manifest, args.assets, args.download)
    environment = dict(os.environ)
    environment.update({"OMP_NUM_THREADS": str(args.threads), "MKL_NUM_THREADS": str(args.threads),
                        "OPENBLAS_NUM_THREADS": str(args.threads), "HF_HUB_OFFLINE": "1",
                        "TRANSFORMERS_OFFLINE": "1", "PYTHONNOUSERSITE": "1"})
    print("Running actual offline BEATs inference on a generated ten-second silent clip...", flush=True)
    flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) if os.name == "nt" else 0
    subprocess.run([sys.executable, str(args.worker.resolve()), "--check", "--assets",
                    str(args.assets.resolve()), "--device", "cpu", "--threads", str(args.threads)],
                   env=environment, check=True, timeout=1200, creationflags=flags)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    checkpoint = next(entry for entry in manifest["files"] if entry["path"] == "BEATs_strong_1.pt")
    marker = {"engine": "PretrainedSED-BEATs", "upstreamCommit": manifest["upstreamCommit"],
              "checkpointSha256": checkpoint["sha256"], "device": "cpu",
              "checkedAt": datetime.now(timezone.utc).isoformat()}
    marker_path = args.assets / "ready.json"
    if linked(marker_path):
        raise ValueError("Model readiness marker cannot be a symbolic link")
    marker_temp = args.assets / ("ready-" + str(os.getpid()) + ".tmp")
    try:
        with marker_temp.open("x", encoding="utf-8") as stream:
            json.dump(marker, stream, indent=2)
        marker_temp.replace(marker_path)
    finally:
        marker_temp.unlink(missing_ok=True)
    print("Sound-event model check passed. This checks execution, not recognition accuracy.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("Sound-event setup failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
