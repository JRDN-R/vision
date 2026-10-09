"""Exercise the real egress container without runtime access to its source files.

Run on a Linux Docker host; no public extraction provider or media URL is used.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
MODULES = ("vortex_egress.py", "vortex_network.py", "vortex_urls.py")


class EgressImageTest(unittest.TestCase):
    def test_service_starts_without_windows_host_files(self):
        project = "vortex-test-" + uuid.uuid4().hex[:12]
        with tempfile.TemporaryDirectory(prefix="Vortex service source ") as directory:
            source = Path(directory)
            files = (*MODULES, "vortex-egress.Dockerfile", "vortex-services.compose.yml")
            for name in files:
                shutil.copyfile(ROOT / "vision-pc" / name, source / name)
                # Match files readable only by the elevated build client. The
                # image must explicitly grant its unprivileged runtime read access.
                (source / name).chmod(0o600)

            compose = ["docker", "compose", "--project-name", project,
                       "--file", str(source / "vortex-services.compose.yml")]

            def run(*args, timeout=120):
                result = subprocess.run([*compose, *args], text=True, capture_output=True,
                                        timeout=timeout)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout

            try:
                config = json.loads(run("config", "--format", "json"))
                for service in config["services"].values():
                    self.assertFalse(service.get("volumes"), "No Windows bind mounts")
                run("build", "egress", timeout=300)
                # A hidden dependency on a host mount cannot pass: the source
                # files are gone before the container is even created.
                for name in MODULES:
                    (source / name).unlink()
                run("up", "--detach", "--no-build", "--pull", "never", "--wait",
                    "--wait-timeout", "45", "egress")
                container = run("ps", "--quiet", "egress").strip()
                inspected = subprocess.run(["docker", "inspect", container], check=True,
                                           capture_output=True, text=True, timeout=30)
                info = json.loads(inspected.stdout)[0]
                self.assertEqual(info["Mounts"], [])
                self.assertEqual(info["State"]["Health"]["Status"], "healthy")
                self.assertTrue(info["HostConfig"]["ReadonlyRootfs"])
                self.assertEqual(info["HostConfig"]["RestartPolicy"]["Name"], "unless-stopped")
                self.assertEqual(info["HostConfig"]["Memory"], 128 * 1024**2)
                self.assertFalse(info["NetworkSettings"]["Ports"])

                probe = """
import os, socket
from vortex_network import validate_input
assert os.geteuid() == 65534
assert validate_input('youtu.be/abcdefghijk', resolve=False) == 'https://www.youtube.com/watch?v=abcdefghijk'
try:
    open('/app/write-test', 'w')
    raise AssertionError('The root filesystem must be read-only')
except OSError:
    pass
for destination in ('127.0.0.1:443', '169.254.169.254:80', '10.0.0.1:443', '[::1]:443'):
    with socket.create_connection(('127.0.0.1', 8080), 3) as connection:
        connection.sendall(('CONNECT ' + destination + ' HTTP/1.1\\r\\n\\r\\n').encode())
        assert connection.recv(256).startswith(b'HTTP/1.1 403 Forbidden'), destination
print('Isolated container is healthy and rejects private destinations.')
"""
                self.assertIn("rejects private destinations", run("exec", "-T", "egress",
                                                                  "python", "-B", "-c", probe))
            finally:
                subprocess.run([*compose, "down", "--remove-orphans"], check=False,
                               capture_output=True, timeout=60)
                subprocess.run(["docker", "image", "rm", project + "-egress"], check=False,
                               capture_output=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
