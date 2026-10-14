"""Offline safety tests for optional Mac RPC. No Mac, tunnel or GGUF is needed."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import local_overdrive as overdrive
import local_runtime


ID = "a" * 32


def config(folder):
    return dict(enabled=True, workerId=ID, rpcPort=51052, healthPort=51053,
                macBudgetGiB=8, runtimeManifestPath=str(Path(folder) / "rpc-manifest.json"))


class FakeResponse:
    def __init__(self, data, status=200):
        self.data = data
        self.status_code = status
        self.content = json.dumps(data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def json(self):
        return self.data


class OverdriveSafetyTests(unittest.TestCase):
    def test_invalid_or_disabled_config_never_contacts_mac(self):
        with tempfile.TemporaryDirectory() as directory:
            value = config(directory)
            self.assertIsNone(overdrive.configuration({}))
            self.assertIsNone(overdrive.configuration({"overdrive": {**value, "enabled": False}}))
            for corrupt in ({"workerId": "unknown"}, {"rpcPort": "51052"},
                            {"rpcPort": 0}, {"macBudgetGiB": 16},
                            {"runtimeManifestPath": "relative.json"},
                            {"healthPort": 51052}):
                self.assertIsNone(overdrive.configuration({"overdrive": {**value, **corrupt}}))
            self.assertEqual(overdrive.configuration({"overdrive": value})["rpcPort"], 51052)

    def test_health_requires_real_rpc_listener_matching_mac_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = overdrive.configuration({"overdrive": config(directory)})
            state = dict(schema=overdrive.WORKER_SCHEMA, workerId=ID, metal=True,
                         rpcReady=True, macBudgetGiB=8)
            session = MagicMock()
            session.get.return_value = FakeResponse(state)
            with patch("local_overdrive.requests.Session", return_value=session), \
                 patch("local_overdrive.socket.create_connection") as connect:
                self.assertTrue(overdrive.probe(cfg))
                args = session.get.call_args.args[0]
                self.assertEqual(args, "http://127.0.0.1:51053/health")
                self.assertEqual(connect.call_args.args[0], ("127.0.0.1", 51052))
                self.assertFalse(session.trust_env)
                session.get.return_value = FakeResponse({**state, "workerId": "b" * 32})
                self.assertFalse(overdrive.probe(cfg))
                session.get.return_value = FakeResponse({**state, "rpcReady": False})
                self.assertFalse(overdrive.probe(cfg))
                session.get.return_value = FakeResponse(state, status=503)
                self.assertFalse(overdrive.probe(cfg))
                connect.side_effect = OSError("tunnel closed")
                session.get.return_value = FakeResponse(state)
                self.assertFalse(overdrive.probe(cfg))

    def test_verified_windows_runtime_checks_version_shas_and_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            exe = folder / "llama-server.exe"
            lib = folder / "ggml-rpc.dll"
            exe.write_bytes(b"fixture-rpc-executable")
            lib.write_bytes(b"fixture-library")
            contents = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (exe, lib)}
            manifest = folder / "rpc-manifest.json"
            manifest.write_text(json.dumps(dict(schema=overdrive.RUNTIME_SCHEMA,
                runtimeCommit=local_runtime.RUNTIME_COMMIT, entrypoint=exe.name, files=contents)))
            self.assertEqual(overdrive.verified_runtime({"runtimeManifestPath": str(manifest)}), exe)
            (folder / "unreviewed.dll").write_bytes(b"unexpected")
            with self.assertRaisesRegex(local_runtime.LocalFailure, "Unexpected files"):
                overdrive.verified_runtime({"runtimeManifestPath": str(manifest)})
            (folder / "unreviewed.dll").unlink()
            exe.write_bytes(b"changed")
            with self.assertRaisesRegex(local_runtime.LocalFailure, "SHA-256"):
                overdrive.verified_runtime({"runtimeManifestPath": str(manifest)})

    def test_offline_or_untuned_mac_preserves_original_cpu_gpu_model_context(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = config(directory)
            model = Path(directory) / "qwen.gguf"
            model.write_bytes(b"fixture")
            (Path(directory) / "rpc-manifest.json").write_text("{}")
            profile = dict(modelVariant="ultra", modelId="qwen-ultra-local",
                           modelPath=str(model), contextTokens=8192,
                           ultraReceipt="local-fixture", threads=8, gpuLayers=4)
            settings = {"overdrive": cfg}
            self.assertEqual(overdrive.route(settings, profile), (profile, "fupcj"))
            receipt = dict(schema=overdrive.SCHEMA, passed=True,
                           sourceReceiptId="local-fixture", workerId=ID,
                           macBudgetGiB=8,
                           runtimeManifestSha256=overdrive._sha256(cfg["runtimeManifestPath"]),
                           maxContextTokens=8192,
                           profile={"threads": 12, "gpuLayers": 24, "tensorSplit": "5,5"})
            overdrive.receipt_path(profile).write_text(json.dumps(receipt))
            with patch("local_overdrive.probe", return_value=False):
                self.assertEqual(overdrive.route(settings, profile), (profile, "fupcj"))
            self.assertNotIn("overdriveRPC", profile)
            with patch("local_overdrive.probe", return_value=True), \
                 patch("local_overdrive.verified_runtime"), \
                 patch("setup_qwen_ultra.quiet_machine"):
                remote, route = overdrive.route(settings, profile)
            self.assertEqual(route, "mac-rpc")
            self.assertEqual(remote["gpuLayers"], 24)
            self.assertEqual(remote["overdriveFallback"], {"threads": 8, "gpuLayers": 4})
            self.assertEqual(remote["modelPath"], profile["modelPath"])
            self.assertEqual(remote["contextTokens"], 8192)
            self.assertEqual(remote["overdriveRPC"]["rpcPort"], 51052)
            self.assertNotIn("overdriveRPC", profile)
            profile["contextTokens"] = 16384
            self.assertEqual(overdrive.route(settings, profile), (profile, "fupcj"))
            profile["contextTokens"] = 8192
            receipt["sourceReceiptId"] = "stale-after-retune"
            overdrive.receipt_path(profile).write_text(json.dumps(receipt))
            self.assertEqual(overdrive.route(settings, profile), (profile, "fupcj"))

    def test_rpc_flags_are_local_only_and_ultra_only(self):
        data = dict(modelVariant="ultra", modelId="qwen-ultra-local", threads=12, gpuLayers=24,
                    overdriveRPC=dict(rpcPort=51052, tensorSplit="5,5"))
        live = local_runtime.LlamaRuntime(data, lambda: False, lambda _: None)
        self.addCleanup(live.http.close)
        command = live.command(Path("llama-server.exe"), Path("qwen.gguf"))
        self.assertEqual(command[command.index("--rpc")+1], "127.0.0.1:51052")
        self.assertEqual(command[command.index("--tensor-split")+1], "5,5")
        live.settings["overdriveRPC"]["rpcPort"] = 0
        with self.assertRaisesRegex(local_runtime.LocalFailure, "Invalid pinned"):
            live.command("llama-server.exe", "qwen.gguf")
        live.settings["overdriveRPC"]["rpcPort"] = 51052
        live.settings["modelVariant"] = "standard"
        with self.assertRaisesRegex(local_runtime.LocalFailure, "Invalid pinned"):
            live.command("llama-server.exe", "qwen.gguf")


if __name__ == "__main__":
    unittest.main()
