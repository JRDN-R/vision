"""Local context-engine installation checks and cold backup support.

No API key is accepted and no inference service is contacted. Model downloads are
possible only with the explicit --download action; ordinary checks are offline.
The PowerShell updater stops the existing task before calling --backup-data.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import stat
import sys
import tempfile
import time

MODEL_REPO = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "c9745ed1d9f207416be6d2e6f8de32d1f16199bf"
MODEL_FILES = (
    "config.json", "config_sentence_transformers.json", "modules.json",
    "sentence_bert_config.json", "special_tokens_map.json", "tokenizer.json",
    "tokenizer_config.json", "vocab.txt", "model.safetensors",
    "1_Pooling/config.json",
)
MODEL_MODULES = {
    "sentence_transformers.models.Transformer",
    "sentence_transformers.models.Pooling",
    "sentence_transformers.models.Normalize",
}


def _json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular_path(path: Path) -> None:
    """Reject symlinks and Windows junctions before traversal or file access."""
    for candidate in (path, *path.parents):
        if not candidate.exists() and not candidate.is_symlink():
            continue
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked/reparse paths are not accepted by the context installer.")


def hardware_report(root: Path) -> dict:
    """Actual local observations; never substitute the anticipated server specs."""
    cpu = platform.processor() or None
    physical_memory = None
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("totalPhys", ctypes.c_ulonglong), ("availPhys", ctypes.c_ulonglong),
                        ("totalPage", ctypes.c_ulonglong), ("availPage", ctypes.c_ulonglong),
                        ("totalVirtual", ctypes.c_ulonglong), ("availVirtual", ctypes.c_ulonglong),
                        ("availExtended", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            physical_memory = int(status.totalPhys)
    else:
        try:
            physical_memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError, AttributeError):
            pass
    with closing(sqlite3.connect(":memory:")) as connection, connection:
        connection.execute("CREATE VIRTUAL TABLE check_fts USING fts5(text)")
    disk_root = root if root.exists() else root.parent
    return {"platform": platform.platform(), "python": platform.python_version(),
            "architecture": platform.machine(), "cpu": cpu, "logicalProcessors": os.cpu_count(),
            "physicalMemoryBytes": physical_memory,
            "physicalMemoryGiB": round(physical_memory / 2**30, 2) if physical_memory else None,
            "diskFreeBytes": shutil.disk_usage(disk_root).free, "sqliteVersion": sqlite3.sqlite_version,
            "fts5": True, "gpu": "not required; not probed", "defaultWorkers": 2,
            "defaultEmbeddingThreads": 2, "paidApiCalls": 0}


def backup_data(source: Path, destination: Path) -> dict:
    """Copy a stopped service's complete data directory, including WAL sidecars.

    The caller must stop the service first. This intentionally fails on linked
    paths, unexpected files, insufficient space, or files changing during copy.
    A manifest is written last so an interrupted copy is never a valid backup.
    """
    if not source.is_absolute() or not destination.is_absolute():
        raise ValueError("Backup paths must be absolute.")
    _regular_path(source)
    _regular_path(destination)
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Backup source and destination must be separate trees.")
    if destination.exists():
        raise ValueError("Backup destination must be a new directory.")
    files = []
    total = 0
    if source.exists():
        if not source.is_dir():
            raise ValueError("Data source must be a directory.")
        for directory, subdirs, names in os.walk(source, followlinks=False):
            for name in subdirs + names:
                path = Path(directory) / name
                _regular_path(path)
                if path.is_dir():
                    continue
                info = path.stat()
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("Data backup accepts regular files only.")
                files.append((path, path.relative_to(source), info.st_size, info.st_mtime_ns))
                total += info.st_size
    destination.parent.mkdir(parents=True, exist_ok=True)
    required = total + max(64 * 1024**2, total // 10)
    if shutil.disk_usage(destination.parent).free < required:
        raise ValueError("Insufficient disk space for a complete data backup plus safety margin.")
    destination.mkdir()
    started = time.monotonic()
    copied = []
    for path, relative, size, modified in files:
        _regular_path(path)
        target = destination / "data" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        current = path.stat()
        if current.st_size != size or current.st_mtime_ns != modified or target.stat().st_size != size:
            raise RuntimeError("Data changed during backup. Stop the processor and retry.")
        source_hash = _sha256(path)
        if _sha256(target) != source_hash:
            raise RuntimeError("Data backup failed checksum verification.")
        copied.append({"path": relative.as_posix(), "bytes": size, "sha256": source_hash})
    result = {"schemaVersion": 1, "complete": True, "type": "cold-data-backup",
              "createdAt": datetime.now(timezone.utc).isoformat(), "fileCount": len(copied),
              "bytes": total, "elapsedSeconds": round(time.monotonic() - started, 3), "files": copied}
    _json(destination / "backup-manifest.json", result)
    return {key: value for key, value in result.items() if key != "files"}


def validate_model_files(model_path: Path) -> None:
    _regular_path(model_path)
    for name in MODEL_FILES:
        path = model_path / name
        _regular_path(path)
        if not path.is_file():
            raise ValueError("The local embedding model is incomplete: " + name)
    modules = json.loads((model_path / "modules.json").read_text(encoding="utf-8"))
    if not isinstance(modules, list) or not modules:
        raise ValueError("Invalid local embedding module manifest.")
    for module in modules:
        if not isinstance(module, dict) or module.get("type") not in MODEL_MODULES:
            raise ValueError("Embedding model requests an unsupported/custom code module.")
        child = str(module.get("path", ""))
        if child and child not in ("1_Pooling", "2_Normalize"):
            raise ValueError("Embedding model module path is not allowed.")
    for path in model_path.rglob("*.json"):
        _regular_path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "auto_map" in data:
            raise ValueError("Embedding model custom code mappings are not permitted.")


def prepare_model(packages_path: Path, model_path: Path, download: bool, threads: int = 2) -> dict:
    if not packages_path.is_absolute() or not packages_path.is_dir() or not model_path.is_absolute():
        raise ValueError("Private absolute packages and model directories are required.")
    _regular_path(packages_path)
    _regular_path(model_path)
    sys.path.insert(0, str(packages_path))
    for name in ("HF_HUB_DISABLE_IMPLICIT_TOKEN", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"):
        os.environ[name] = "1"
    os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "120"
    os.environ["OMP_NUM_THREADS"] = str(threads)
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["HF_HUB_OFFLINE"] = "0" if download else "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "0" if download else "1"
    if download:
        from huggingface_hub import snapshot_download
        snapshot_download(repo_id=MODEL_REPO, revision=MODEL_REVISION,
                          allow_patterns=list(MODEL_FILES), local_dir=str(model_path),
                          token=False, max_workers=2)
    validate_model_files(model_path)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(threads)
    model = SentenceTransformer(str(model_path), device="cpu", local_files_only=True,
                                trust_remote_code=False, model_kwargs={"use_safetensors": True})
    started = time.monotonic()
    vectors = model.encode(["The mechanic repaired the aircraft wing.",
                            "An aviation technician fixed the plane.",
                            "Bananas are grown in tropical climates."],
                           normalize_embeddings=True, show_progress_bar=False, batch_size=3)
    if vectors.shape != (3, 384) or not bool((vectors == vectors).all()):
        raise RuntimeError("Embedding model returned invalid vectors.")
    related = float(vectors[0] @ vectors[1])
    unrelated = float(vectors[0] @ vectors[2])
    if related <= unrelated:
        raise RuntimeError("Local semantic smoke test failed.")
    result = {"repository": MODEL_REPO, "revision": MODEL_REVISION, "device": "cpu",
              "dimensions": 384, "embeddingThreads": threads, "offlineInference": True,
              "smokeSeconds": round(time.monotonic() - started, 3),
              "relatedSimilarity": related, "unrelatedSimilarity": unrelated,
              "files": {name: _sha256(model_path / name) for name in MODEL_FILES}}
    if download:
        _json(model_path / "vision-context-model.json", result)
    return {key: value for key, value in result.items() if key != "files"}


def self_test(config_path: Path | None = None) -> dict:
    """Exercise only a disposable project; no live database or paid API is used."""
    # Windows' embedded Python uses an isolated ._pth, so the staged script's
    # directory is not necessarily on sys.path. Test staged code, never silently
    # import the older installed module through the runtime's parent entry.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from context_engine import ContextEngine
    settings = {}
    if config_path:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        # Do not copy installation credentials into the disposable fixture.
        configured = config.get("intelligentContext") or {}
        settings = {key: configured[key] for key in
                    ("embeddingModelPath", "embeddingPackagesPath", "embeddingThreads") if key in configured}
    codes = [f"{number:04d}" for number in range(30, 400, 30)]
    csv = "OPN,WC,Description,Run Hrs,Notes\n" + "\n".join(
        f'{code},2CU0SA,Fixture operation {code},0.380,Preserve exact RH 133-430065-2 value {code}'
        for code in codes)
    project = {"title": "Disposable installer fixture", "mainPrompt": "Preserve all operation fields and identifiers.",
               "nodes": [{"id": "instructions", "title": "Instructions", "prompt": "Include every operation and its notes."},
                         {"id": "records", "title": "Records", "attachments": [{"id": "operations", "name": "operations.csv",
                           "mime": "text/csv", "data": "data:text/csv;base64," + base64.b64encode(csv.encode()).decode()}]}],
               "edges": [{"from": "instructions", "to": "records"}]}
    with tempfile.TemporaryDirectory(prefix="vision-context-check-") as temp:
        engine = ContextEngine(Path(temp), settings)
        try:
            status = engine.index_project("installer-check", "disposable-check", 1, project)
            if status.get("status") != "ready":
                raise RuntimeError("Disposable context index did not become ready.")
            evidence = engine.prepare("installer-check", "disposable-check", 1,
                                      "Reproduce every operation, work center, run hours and notes.", budget=100000)
            if not evidence.get("complete") or any(code not in evidence["text"] for code in codes):
                raise RuntimeError("Disposable context completeness check failed.")
            if "0.380" not in evidence["text"] or "133-430065-2" not in evidence["text"]:
                raise RuntimeError("Disposable exact-value preservation check failed.")
            foreign = engine.prepare("different-account", "disposable-check", 1, "operations")
            if foreign.get("items") or foreign.get("ready"):
                raise RuntimeError("Disposable account isolation check failed.")
            capability = engine.embeddings.capability()
            if settings.get("embeddingModelPath") and not capability.get("ready"):
                raise RuntimeError("Configured embedding model failed the offline engine check.")
            result = {"ok": True, "recordCount": len(codes), "exactValues": True,
                      "accountIsolation": True, "indexMetrics": status["manifest"].get("metrics", {}),
                      "embeddings": capability, "paidApiCalls": 0}
        finally:
            engine.close()
    result["hardware"] = hardware_report(Path(__file__).parent)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--report", action="store_true")
    action.add_argument("--backup-data", type=Path)
    action.add_argument("--download", action="store_true")
    action.add_argument("--check-model", action="store_true")
    action.add_argument("--self-test", action="store_true")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--packages-path", type=Path)
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--threads", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    if args.report:
        result = hardware_report(Path(__file__).parent)
    elif args.backup_data:
        if not args.destination:
            parser.error("--destination is required with --backup-data")
        result = backup_data(args.backup_data, args.destination)
    elif args.self_test:
        result = self_test(args.config)
    else:
        if not args.packages_path or not args.model_path:
            parser.error("--packages-path and --model-path are required for model actions")
        result = prepare_model(args.packages_path, args.model_path, args.download, args.threads)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
