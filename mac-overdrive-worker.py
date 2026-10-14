#!/usr/bin/env python3
"""macOS-only loopback Metal RPC worker and fast health responder for Venture.

The health responder and RPC server MUST remain bound to 127.0.0.1. On the
Windows host, forward both ports over authenticated SSH (for example, via
Tailscale); never expose the RPC protocol to a LAN or the public Internet.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import uuid


SCHEMA = "vision-mac-overdrive-worker-v1"
ROOT = Path.home() / ".config" / "venture-overdrive"


def worker_id():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = ROOT / "worker-id"
    if not path.exists():
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(uuid.uuid4().hex)
    result = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[a-f0-9]{32}", result):
        raise ValueError("Invalid saved worker identity.")
    return result


def rss_bytes(pid):
    """RSS is a soft warning, not a guaranteed cap on Apple Metal memory."""
    try:
        value = subprocess.check_output(["/bin/ps", "-o", "rss=", "-p", str(pid)],
                                        text=True, timeout=.3).strip()
        return int(value.split()[0]) * 1024
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        return None


def rpc_listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.12):
            return True
    except OSError:
        return False


def main():
    if sys.platform != "darwin":
        raise SystemExit("The Overdrive Metal worker runs only on macOS.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc-binary", required=True, help="Pinned RPC + Metal ggml-rpc-server executable")
    parser.add_argument("--rpc-port", type=int, default=50052)
    parser.add_argument("--health-port", type=int, default=50053)
    parser.add_argument("--budget-gib", type=int, choices=(8, 9, 10), default=8)
    parser.add_argument("--cache", action="store_true", help="Cache transferred model weights on the Mac")
    args = parser.parse_args()
    if not (1024 <= args.rpc_port <= 65535 and
            1024 <= args.health_port <= 65535 and args.rpc_port != args.health_port):
        parser.error("Ports must be different and between 1024 and 65535.")
    binary = Path(args.rpc_binary).expanduser().resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        parser.error("Supply an executable Metal/RPC-enabled ggml-rpc-server binary.")

    identity = worker_id()
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    log = ROOT / "rpc.log"
    # Keep only this worker lifetime's device inventory; old logs cannot fake Metal.
    log_stream = log.open("wb", buffering=0)
    command = [str(binary), "--host", "127.0.0.1", "--port", str(args.rpc_port)]
    if args.cache:
        command.append("--cache")
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log_stream,
                             stderr=subprocess.STDOUT, start_new_session=True)

    def metal_reported():
        try:
            inventory = log.read_text(encoding="utf-8", errors="replace")[-16384:]
            return bool(re.search(r"\b(?:Metal|MTL[0-9]+)\b", inventory, re.IGNORECASE))
        except OSError:
            return False

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/health":
                self.send_error(404)
                return
            resident = rss_bytes(child.pid) if child.poll() is None else None
            # Check process liveness and its actual listening port, not only ping.
            metal = metal_reported()
            ready = (child.poll() is None and metal and rpc_listening(args.rpc_port) and
                     resident is not None and resident < args.budget_gib * 1024**3)
            response = dict(schema=SCHEMA, workerId=identity, metal=bool(metal),
                            rpcReady=bool(ready), macBudgetGiB=args.budget_gib,
                            rpcResidentBytes=resident)
            body = json.dumps(response, separators=(",", ":")).encode("utf-8")
            self.send_response(200 if ready else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.health_port), Handler)
    except OSError:
        child.terminate()
        child.wait(timeout=10)
        log_stream.close()
        raise
    print("Venture Overdrive Mac worker ID: " + identity, flush=True)
    print("RPC / health bound to localhost only. Use authenticated SSH forwards.", flush=True)
    print("Memory target: %d GiB (advisory; not a hard Metal allocation limit)." %
          args.budget_gib, flush=True)
    print("RPC log: " + str(log), flush=True)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)
        log_stream.close()


if __name__ == "__main__":
    main()
