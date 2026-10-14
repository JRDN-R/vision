"""Optional on-demand Metal RPC routing for Qwen Ultra.

No prompt or browser-supplied URL is used for discovery. The Windows host only
talks to preconfigured loopback SSH forwards; an absent/unready Mac is an
ordinary local-PC run. A separate, benchmarked receipt pins the remote profile.
"""
import hashlib
import json
from pathlib import Path
import re
import socket

import requests

SCHEMA = "vision-overdrive-ready-v1"
WORKER_SCHEMA = "vision-mac-overdrive-worker-v1"
RUNTIME_SCHEMA = "vision-overdrive-rpc-runtime-v1"
TIMEOUT = 0.45


def configuration(settings):
    """Read administrator-owned config; never accept a remote hostname or URL."""
    value = settings.get("overdrive") if isinstance(settings, dict) else None
    if not isinstance(value, dict) or value.get("enabled") is not True:
        return None
    worker = value.get("workerId")
    ports = (value.get("rpcPort"), value.get("healthPort"))
    budget = value.get("macBudgetGiB", 8)
    manifest = value.get("runtimeManifestPath")
    if (not isinstance(worker, str) or not re.fullmatch(r"[a-f0-9]{32}", worker) or
            any(type(port) is not int or port < 1024 or port > 65535 for port in ports) or
            ports[0] == ports[1] or type(budget) is not int or budget not in (8, 9, 10) or
            not isinstance(manifest, str) or not Path(manifest).is_absolute()):
        return None
    return dict(workerId=worker, rpcPort=ports[0], healthPort=ports[1],
                macBudgetGiB=budget, runtimeManifestPath=manifest)


def probe(config, timeout=TIMEOUT):
    """One short, real RPC-worker health check per answer, not an ICMP-only ping."""
    session = requests.Session()
    session.trust_env = False
    try:
        with session.get("http://127.0.0.1:%d/health" % config["healthPort"],
                         timeout=(min(timeout, .25), timeout), allow_redirects=False) as response:
            if response.status_code != 200 or len(response.content) > 4096:
                return False
            state = response.json()
        if not isinstance(state, dict) or any((
                state.get("schema") != WORKER_SCHEMA,
                state.get("workerId") != config["workerId"],
                state.get("metal") is not True,
                state.get("rpcReady") is not True,
                state.get("macBudgetGiB") != config["macBudgetGiB"])):
            return False
        # The health forward and RPC forward must both be listening.
        with socket.create_connection(("127.0.0.1", config["rpcPort"]), timeout=min(timeout, .25)):
            return True
    except (OSError, ValueError, TypeError, requests.RequestException):
        return False
    finally:
        session.close()


def _sha256(path):
    from local_runtime import digest_file
    return digest_file(path)


def verified_runtime(config):
    """Integrity-check the separately built, pinned RPC-enabled Windows runtime."""
    from local_runtime import LocalFailure, RUNTIME_COMMIT
    path = Path(config["runtimeManifestPath"])
    if path.is_symlink() or not path.is_file():
        raise LocalFailure("The Overdrive RPC runtime manifest is missing or linked.")
    root = path.parent.resolve()
    try:
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        files = manifest["files"]
        entry = manifest["entrypoint"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise LocalFailure("The Overdrive RPC runtime manifest is invalid.") from error
    if (manifest.get("schema") != RUNTIME_SCHEMA or
            manifest.get("runtimeCommit") != RUNTIME_COMMIT or
            not isinstance(files, dict) or not 1 <= len(files) <= 200 or
            not isinstance(entry, str) or entry not in files):
        raise LocalFailure("The RPC runtime is not the reviewed llama.cpp build.")
    expected = set()
    for name, digest in files.items():
        if (not isinstance(name, str) or "\\" in name or name.startswith("/") or
                not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest)):
            raise LocalFailure("Unsafe Overdrive RPC runtime inventory.")
        part = root / name
        if part.is_symlink() or not part.is_file() or not part.resolve().is_relative_to(root):
            raise LocalFailure("An Overdrive runtime file is missing or linked.")
        if _sha256(part) != digest:
            raise LocalFailure("An Overdrive RPC runtime file failed SHA-256 verification.")
        expected.add(part.relative_to(root).as_posix())
    actual = {item.relative_to(root).as_posix() for item in root.rglob("*") if item.is_file()}
    if actual != expected | {path.name}:
        raise LocalFailure("Unexpected files in the Overdrive RPC runtime directory.")
    runtime = root / entry
    if runtime.name.lower() != "llama-server.exe":
        raise LocalFailure("Invalid Overdrive server executable.")
    return runtime


def receipt_path(profile):
    return Path(profile["modelPath"]).parent / "overdrive-ready.json"


def approved(config, profile):
    """A remote setup is opt-in and must have passed on-device inference tests."""
    try:
        path = receipt_path(profile)
        if path.is_symlink():
            return None
        receipt = json.loads(path.read_text(encoding="utf-8-sig"))
        tested = receipt["profile"]
        manifest = Path(config["runtimeManifestPath"])
        if (receipt.get("schema") != SCHEMA or receipt.get("passed") is not True or
                receipt.get("sourceReceiptId") != profile.get("ultraReceipt") or
                receipt.get("workerId") != config["workerId"] or
                receipt.get("macBudgetGiB") != config["macBudgetGiB"] or
                receipt.get("runtimeManifestSha256") != _sha256(manifest) or
                type(receipt.get("maxContextTokens")) is not int or
                profile["contextTokens"] > receipt["maxContextTokens"] or
                not isinstance(tested, dict) or
                type(tested.get("gpuLayers")) is not int or not 1 <= tested["gpuLayers"] <= 99 or
                type(tested.get("threads")) is not int or not 1 <= tested["threads"] <= 16 or
                not isinstance(tested.get("tensorSplit"), str) or
                not re.fullmatch(r"[1-9]\d{0,3},[1-9]\d{0,3}", tested["tensorSplit"])):
            return None
        return dict(tested)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def route(settings, profile):
    """Select remote acceleration per job. Any failed preflight stays PC-only."""
    original = dict(profile)
    config = configuration(settings)
    if not config or profile.get("modelVariant") != "ultra":
        return original, "fupcj"
    tested = approved(config, profile)
    if not tested or not probe(config):
        return original, "fupcj"
    try:
        verified_runtime(config)
    except (OSError, ValueError):
        return original, "fupcj"
    # Preserve livestream headroom: only activate optional remote work when
    # OBS/Streamlabs is not running. The local Auto/CPU profile remains available.
    try:
        from setup_qwen_ultra import quiet_machine
        quiet_machine("starting Ultra Overdrive")
    except (OSError, ValueError):
        return original, "fupcj"
    original.update(threads=tested["threads"], gpuLayers=tested["gpuLayers"],
                    overdriveFallback=dict(threads=profile["threads"], gpuLayers=profile["gpuLayers"]),
                    overdriveRPC=dict(rpcPort=config["rpcPort"],
                                      tensorSplit=tested["tensorSplit"],
                                      runtimeManifestPath=config["runtimeManifestPath"],
                                      workerConfig=config))
    return original, "mac-rpc"
