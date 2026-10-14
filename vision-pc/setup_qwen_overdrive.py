"""Operator-only Overdrive tuning for the existing Qwen Ultra model.

Requires a verified, RPC-enabled Windows llama.cpp b11559 build, an SSH-
forwarded Mac Metal worker, and an already enabled and tuned local Ultra model.
Never changes the PC's existing Ultra readiness receipt or selected defaults.
"""
import argparse
import json
from pathlib import Path
import time

import requests

import local_overdrive as overdrive
import local_ultra as ultra
from local_runtime import LlamaRuntime, LocalFailure
from local_variants import read_json
from owned_assets import exclusive_file
from setup_local_variant import atomic_json, smoke_test
from setup_qwen_ultra import benchmark, context_check, quiet_machine, score


def tune(settings, context_tokens=8192):
    config = overdrive.configuration(settings)
    if not config:
        raise LocalFailure("Enable localAI.overdrive with valid localhost SSH ports, Mac worker ID and RPC runtime manifest.")
    if not overdrive.probe(config):
        raise LocalFailure("Mac Metal worker unavailable; check its SSH forwards, battery/awake state, and RPC process.")
    overdrive.verified_runtime(config)
    base = ultra.select_profile(settings, dict(contextTokens=context_tokens))
    local_receipt = ultra.readiness(base)
    if context_tokens > local_receipt["maxContextTokens"]:
        raise LocalFailure("Run Qwen-Ultra.ps1 -Action Tune for this context on the PC first.")
    try:
        records = [result for result in local_receipt["profiles"].values()
                   if isinstance(result, dict) and result.get("benchmark")]
        best_local = min(records, key=score)
    except (ValueError, KeyError, TypeError) as error:
        raise LocalFailure("The existing FUPCJ tuning receipt has no usable benchmark.") from error

    quiet_machine("Overdrive tuning")
    candidates, errors = [], []
    deadline = time.monotonic() + 3600
    # Two verified GPUs (Windows Vulkan + Mac Metal), with a conservative start.
    # Tensor proportions are device proportions, NOT GB or a hard RAM limit.
    for layers in (12, 16, 20, 24, 28):
        for split in ("5,5", "4,6"):
            if time.monotonic() >= deadline:
                break
            quiet_machine("Overdrive tuning")
            if not overdrive.probe(config):
                raise LocalFailure("Mac worker disconnected during tuning. Previous local profile is unchanged.")
            knobs = dict(threads=base["threads"], gpuLayers=layers, tensorSplit=split)
            candidate = dict(base, contextTokens=context_tokens, threads=knobs["threads"],
                             gpuLayers=layers,
                             overdriveRPC=dict(rpcPort=config["rpcPort"], tensorSplit=split,
                                               runtimeManifestPath=config["runtimeManifestPath"]))
            print("Overdrive trial: %d GPU layers, tensor split %s" % (layers, split), flush=True)
            try:
                result = benchmark(candidate)
                if not overdrive.probe(config):
                    raise LocalFailure("Mac worker failed its post-trial memory/availability check.")
                candidates.append(dict(profile=knobs, benchmark=result))
                print("Measured %.2f generated tokens/sec." %
                      result["generationTokensPerSecond"], flush=True)
            except (OSError, ValueError, requests.RequestException) as error:
                errors.append(dict(profile=knobs, error=str(error)[:500]))
                print("Remote trial rejected: " + str(error)[:500], flush=True)

    target = overdrive.receipt_path(base)
    atomic_json(target.with_name("overdrive-tuning-report.json"),
                dict(candidates=candidates, errors=errors, localBenchmark=best_local["benchmark"]))
    if not candidates:
        raise LocalFailure("No Mac GPU configuration passed. The local PC remains unchanged.")
    selected = min(candidates, key=score)
    if score(selected) >= score(best_local) * .95:
        raise LocalFailure("Mac acceleration did not beat the PC profile by 5%; Overdrive was not enabled.")
    tested = dict(base, contextTokens=context_tokens, threads=selected["profile"]["threads"],
                  gpuLayers=selected["profile"]["gpuLayers"],
                  overdriveRPC=dict(rpcPort=config["rpcPort"], tensorSplit=selected["profile"]["tensorSplit"],
                                    runtimeManifestPath=config["runtimeManifestPath"]))
    print("Validating real conversation and near-capacity context over both computers…", flush=True)
    smoke_test(tested)
    context_check(tested)
    if not overdrive.probe(config):
        raise LocalFailure("Mac is no longer ready; Overdrive was not enabled.")
    receipt = dict(schema=overdrive.SCHEMA, passed=True, enabledAt=time.time(),
                   sourceReceiptId=base["ultraReceipt"], workerId=config["workerId"],
                   macBudgetGiB=config["macBudgetGiB"],
                   runtimeManifestSha256=overdrive._sha256(config["runtimeManifestPath"]),
                   maxContextTokens=context_tokens, profile=selected["profile"],
                   benchmark=selected["benchmark"])
    atomic_json(target, receipt)
    print("Overdrive ready: %.2f tokens/sec; Windows stays primary. Offline Mac => local PC." %
          selected["benchmark"]["generationTokensPerSecond"], flush=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--action", choices=("tune", "check"), default="check")
    parser.add_argument("--context", type=int, choices=(4096, 8192, 16384, 32768, 65536), default=8192)
    args = parser.parse_args()
    config_file = Path(args.config)
    if not config_file.is_absolute() or config_file.is_symlink():
        parser.error("Use the normal absolute Vision PC configuration path.")
    settings = (read_json(config_file).get("localAI") or {})
    config = overdrive.configuration(settings)
    if args.action == "check":
        base = ultra.select_profile(settings, dict(contextTokens=args.context))
        good = bool(config and overdrive.approved(config, base) and overdrive.probe(config))
        print("Overdrive ready" if good else "Overdrive offline, unconfigured or not tuned; FUPCJ stays local.")
        return
    data = read_json(config_file)
    directory = Path(data.get("dataDir") or "data")
    if not directory.is_absolute():
        directory = config_file.parent / directory
    with exclusive_file(directory / "local-inference.lock"):
        tune(settings, args.context)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, requests.RequestException) as error:
        raise SystemExit("Overdrive setup stopped: " + str(error)) from None
