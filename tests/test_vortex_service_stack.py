"""Exercise the production Cobalt stack, host ingress, and enforced egress.

Requires a Linux Docker daemon and Compose 2.24.4+ (ports !override). Builds the
real pinned upstream service, but never submits a public media URL. Windows
Docker Desktop still requires a separate deployment check on the target PC.
"""
import http.client
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
COBALT_COMMIT = "a636575b09de1fc55d9b8cd98cac88f5f2f16b42"
FILES = (
    "vortex-services.compose.yml", "vortex_egress.py", "vortex_network.py",
    "vortex_urls.py", "vortex-egress.Dockerfile", "vortex-egress-policy.Dockerfile",
    "vortex_egress_policy.sh", "vortex-ingress.Dockerfile",
)


class ServiceStackTest(unittest.TestCase):
    def test_real_stack_is_host_reachable_and_keeps_egress_restricted(self):
        project = "vortex-stack-test-" + uuid.uuid4().hex[:12]
        images = [project + "-" + service for service in ("cobalt", "network-policy", "egress", "ingress")]
        with tempfile.TemporaryDirectory(prefix="Vortex complete stack ") as directory:
            source = Path(directory)
            for name in FILES:
                shutil.copyfile(ROOT / "vision-pc" / name, source / name)
                # Build context readable by the host, no runtime host mounts.
                (source / name).chmod(0o600)

            # Keep production networks, firewall, service commands and limits.
            # Only the image names and host port change. The test-only canary
            # has a real listening port and no host publication or media access.
            override = source / "test.compose.yml"
            override.write_text(f"""services:
  cobalt:
    image: {images[0]}
  network-policy:
    image: {images[1]}
  ingress:
    image: {images[3]}
    ports: !override
      - '127.0.0.1:0:9000'
  egress:
    image: {images[2]}
  canary:
    image: {images[2]}
    pull_policy: never
    user: '65534:65534'
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    mem_limit: 32m
    cpus: 0.25
    pids_limit: 16
    networks: [engines]
    entrypoint: ['python', '-B', '-c']
    command: ["import socketserver; socketserver.TCPServer(('0.0.0.0',8081),socketserver.BaseRequestHandler).serve_forever()"]
    healthcheck:
      test: ['CMD', 'python', '-B', '-c', "import socket; socket.create_connection(('127.0.0.1',8081),2).close()"]
      interval: 2s
      timeout: 3s
      retries: 10
    logging:
      driver: none
""", encoding="utf-8")
            compose = ["docker", "compose", "--project-name", project,
                       "--file", str(source / FILES[0]), "--file", str(override)]

            def run_command(command, timeout=120):
                result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
                self.assertEqual(result.returncode, 0, "Command failed: " + repr(command[:6]) +
                                 "\n" + result.stdout + result.stderr)
                return result.stdout

            def run(*args, timeout=120):
                return run_command([*compose, *args], timeout=timeout)

            def inspect(service):
                container = run("ps", "--quiet", service).strip()
                self.assertTrue(container, service + " container must be running")
                return json.loads(run_command(["docker", "inspect", container], timeout=30))[0]

            def dropped_packets():
                rules = run("exec", "-T", "network-policy", "iptables", "-nvx", "-L", "OUTPUT")
                match = re.search(r"Chain OUTPUT \(policy DROP (\d+) packets", rules)
                self.assertIsNotNone(match, rules)
                return int(match.group(1))

            def host_health(port):
                # http.client never inherits HTTP(S)_PROXY from the CI host.
                deadline = time.monotonic() + 20
                while True:
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                    try:
                        connection.request("GET", "/")
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        return json.loads(response.read(1024 * 1024))
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.5)
                    finally:
                        connection.close()

            try:
                run_command(["git", "clone", "--no-checkout", "--filter=blob:none",
                             "https://github.com/imputnet/cobalt.git", str(source / "cobalt")])
                run_command(["git", "-C", str(source / "cobalt"), "checkout", "--detach", COBALT_COMMIT])
                config = json.loads(run("config", "--format", "json"))
                for service in config["services"].values():
                    self.assertFalse(service.get("volumes"), "No runtime Windows source mounts")
                run("build", "cobalt", "network-policy", "egress", "ingress", timeout=720)
                run("up", "--detach", "--no-build", "--pull", "never", "--wait",
                    "--wait-timeout", "120", timeout=180)
                containers = {name: inspect(name) for name in ("cobalt", "network-policy", "egress", "ingress", "canary")}
                limits = {
                    "cobalt": (1536 * 1024**2, 2_000_000_000, 128),
                    "network-policy": (32 * 1024**2, 250_000_000, 16),
                    "egress": (128 * 1024**2, 1_000_000_000, 32),
                    "ingress": (32 * 1024**2, 250_000_000, 32),
                }
                for name, (memory, cpus, pids) in limits.items():
                    item = containers[name]
                    self.assertEqual(item["State"]["Health"]["Status"], "healthy", name)
                    self.assertTrue(all(mount["Type"] == "tmpfs" for mount in item["Mounts"]), name)
                    host = item["HostConfig"]
                    self.assertTrue(host["ReadonlyRootfs"], name)
                    self.assertEqual(host["RestartPolicy"]["Name"], "unless-stopped", name)
                    self.assertEqual((host["Memory"], host["NanoCpus"], host["PidsLimit"]),
                                     (memory, cpus, pids), name)
                    self.assertIn("ALL", [value.upper() for value in host["CapDrop"]], name)
                    self.assertTrue(any(value.startswith("no-new-privileges") for value in host["SecurityOpt"]), name)
                    if name != "network-policy":
                        self.assertFalse(host["CapAdd"], name)
                        self.assertNotEqual(item["Config"]["User"], "0", name)
                        self.assertNotEqual(item["Config"]["User"], "", name)
                policy = containers["network-policy"]
                self.assertEqual([value.removeprefix("CAP_") for value in policy["HostConfig"]["CapAdd"]], ["NET_ADMIN"])
                self.assertEqual(containers["cobalt"]["HostConfig"]["NetworkMode"], "container:" + policy["Id"])
                self.assertFalse(containers["egress"]["NetworkSettings"]["Ports"])
                self.assertFalse(containers["canary"]["NetworkSettings"]["Ports"])
                self.assertFalse(policy["NetworkSettings"]["Ports"])

                # HostConfig.PortBindings only records a request. Internal-only
                # networks can silently omit the actual NetworkSettings mapping.
                published = containers["ingress"]["NetworkSettings"]["Ports"].get("9000/tcp")
                self.assertTrue(published, "Docker must actually publish the Cobalt host port")
                self.assertEqual(len(published), 1)
                self.assertEqual(published[0]["HostIp"], "127.0.0.1")
                health = host_health(int(published[0]["HostPort"]))
                self.assertEqual(health["git"]["commit"], COBALT_COMMIT)
                self.assertTrue(health["cobalt"]["services"])

                # Inspect the real namespace rules, not a mocked policy or YAML.
                for command in ("iptables", "ip6tables"):
                    rules = run("exec", "-T", "network-policy", command, "-S", "OUTPUT")
                    self.assertIn("-P OUTPUT DROP", rules)
                engines = project + "_engines"
                self.assertEqual(set(policy["NetworkSettings"]["Networks"]), {engines})
                self.assertEqual(run("exec", "-T", "network-policy", "ip", "-4", "route", "show", "default").strip(), "")
                self.assertEqual(run("exec", "-T", "network-policy", "ip", "-6", "route", "show", "default").strip(), "")
                canary_ip = containers["canary"]["NetworkSettings"]["Networks"][engines]["IPAddress"]
                network = json.loads(run_command(["docker", "network", "inspect", engines], timeout=30))[0]
                self.assertTrue(network["Internal"])
                gateway = next(item["Gateway"] for item in network["IPAM"]["Config"] if item.get("Gateway"))
                self.assertTrue(canary_ip)
                self.assertTrue(gateway)

                # Positive controls establish that the private targets really
                # listen. An unreachable/nonexistent target cannot prove isolation.
                with socket.socket() as host_canary:
                    host_canary.bind(("0.0.0.0", 0))
                    host_canary.listen(16)
                    host_port = host_canary.getsockname()[1]
                    positive = ("import socket; "
                                f"socket.create_connection(({canary_ip!r},8081),3).close(); "
                                f"socket.create_connection(({gateway!r},{host_port}),3).close()")
                    run("exec", "-T", "egress", "python", "-B", "-c", positive)
                    def denied_tcp(targets, allow_no_route=False):
                        return f"""
const net = require('net');
async function blocked(host, port) {{
  await new Promise((resolve, reject) => {{
    const s = net.connect({{host, port}});
    s.setTimeout(1800, () => {{ s.destroy(); resolve(); }});
    s.once('error', error => {{
      s.destroy();
      if ({json.dumps(allow_no_route)} && ['ENETUNREACH', 'EHOSTUNREACH'].includes(error.code)) resolve();
      else reject(error);
    }});
    s.once('connect', () => {{ s.destroy(); reject(new Error('Unauthorized direct connection succeeded')); }});
  }});
}}
(async () => {{
  for (const [host, port] of {json.dumps(targets)}) {{ await blocked(host, port); }}
}})().catch(error => {{ console.error(error.message); process.exit(1); }});
"""
                    before = dropped_packets()
                    run("exec", "-T", "cobalt", "node", "-e",
                        denied_tcp([(canary_ip, 8081), (gateway, host_port)]), timeout=15)
                    self.assertGreater(dropped_packets(), before, "Denied SYN packets must hit OUTPUT DROP")

                # No public route exists even during daemon restarts before the
                # namespace firewall initializes. ENETUNREACH can precede OUTPUT,
                # so use the inspected internal-only network/no-route guarantees;
                # the reachable private targets above prove real firewall drops.
                run("exec", "-T", "cobalt", "node", "-e",
                    denied_tcp([("1.1.1.1", 443)], allow_no_route=True), timeout=10)

                # The approved proxy is reachable from the same restricted
                # namespace and refuses every private destination before dialing.
                private_proxy = f"""
const net = require('net');
async function rejected(authority) {{
  return new Promise((resolve, reject) => {{
    let data = '';
    const s = net.connect({{host: 'egress', port: 8080}}, () => s.write('CONNECT ' + authority + ' HTTP/1.1\\r\\n\\r\\n'));
    s.setTimeout(3000, () => {{ s.destroy(); reject(new Error('Proxy timed out')); }});
    s.on('data', chunk => {{ data += chunk.toString(); }});
    s.once('error', reject);
    s.once('end', () => data.startsWith('HTTP/1.1 403 Forbidden') ? resolve() : reject(new Error('Private request not rejected')));
  }});
}}
(async () => {{
  for (const target of ['127.0.0.1:443', '169.254.169.254:80', '10.0.0.1:443', '[::1]:443', {json.dumps(canary_ip + ':80')}]) {{
    await rejected(target);
  }}
}})().catch(error => {{ console.error(error.message); process.exit(1); }});
"""
                run("exec", "-T", "cobalt", "node", "-e", private_proxy, timeout=30)
                self.assertEqual(host_health(int(published[0]["HostPort"]))["git"]["commit"], COBALT_COMMIT)
            finally:
                subprocess.run([*compose, "down", "--remove-orphans"], check=False,
                               capture_output=True, timeout=60)
                subprocess.run(["docker", "image", "rm", *images], check=False,
                               capture_output=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
