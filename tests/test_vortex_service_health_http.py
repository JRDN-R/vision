"""Exercise the PowerShell Cobalt host probe against real local HTTP responses.

No Docker, external network access, software installation, or account is needed.
The port is intentionally the production probe's fixed loopback port.
"""

import base64
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import unittest


PINNED_COMMIT = "a636575b09de1fc55d9b8cd98cac88f5f2f16b42"
SERVICES_SCRIPT = Path(__file__).resolve().parents[1] / "vision-pc" / "Vortex-Services.ps1"
SENSITIVE_MARKER = "PRIVATE_FIXTURE_RESPONSE_TOKEN"


class HealthHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executable = "powershell.exe" if os.name == "nt" else os.environ.get("VORTEX_POWERSHELL", "pwsh")
        cls.powershell = shutil.which(executable)
        if not cls.powershell:
            raise RuntimeError(f"PowerShell is required for HTTP health regression tests: {executable}")
        cls.lock = threading.Lock()
        cls.requests = []
        cls.response = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            # HTTP/1.0 closes the connection when no Content-Length is supplied,
            # exercising a bounded response read without trusting a size header.
            protocol_version = "HTTP/1.0"

            def do_GET(self):
                with cls.lock:
                    cls.requests.append((self.path, {name.lower(): value for name, value in self.headers.items()}))
                    response = dict(cls.response)
                self.send_response(response.get("status", 200))
                self.send_header("Content-Type", "application/json")
                body = response.get("body", b"")
                if response.get("include_length", True):
                    self.send_header("Content-Length", str(len(body)))
                for name, value in response.get("headers", {}).items():
                    self.send_header(name, value)
                self.end_headers()
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    # Correct size/redirect rejection may close the connection
                    # before the fixture has finished writing its body.
                    pass

            def log_message(self, _format, *args):
                pass

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 9000), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        with self.lock:
            self.requests.clear()
            self.response.clear()

    @staticmethod
    def valid_body(commit=PINNED_COMMIT):
        return json.dumps({"cobalt": {"version": "11.7.1", "services": ["youtube", "twitter"]}, "git": {"commit": commit}}).encode("utf-8")

    def probe(self, **response):
        with self.lock:
            self.response.update(response)
        # An explicit IWebProxy avoids .NET Framework's automatic loopback
        # exemption, which differs from .NET Core's WebProxy behavior. Port 1
        # is deliberately unreachable; the request must set Proxy to null.
        path = str(SERVICES_SCRIPT).replace("'", "''")
        script = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -TypeDefinition @'
public sealed class VortexRequiredProxy : System.Net.IWebProxy
{{
    public System.Net.ICredentials Credentials {{ get; set; }}
    public System.Uri GetProxy(System.Uri destination)
    {{
        return new System.Uri("http://127.0.0.1:1/");
    }}
    public bool IsBypassed(System.Uri destination) {{ return false; }}
}}
'@
$Proxy = New-Object VortexRequiredProxy
if ($Proxy.IsBypassed([Uri]'http://127.0.0.1:9000/')) {{ throw 'Fixture proxy unexpectedly bypasses the target.' }}
[System.Net.WebRequest]::DefaultWebProxy = $Proxy
. '{path}'
try {{
    $Health = Get-VortexServiceHealth -Attempts 1 -DelaySeconds 0
    Write-Output ('VORTEX_HTTP_OK:' + $Health.git.commit)
    exit 0
}} catch {{
    Write-Output ('VORTEX_HTTP_ERROR:' + $_.Exception.Message)
    exit 9
}}
"""
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        result = subprocess.run(
            [self.powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        with self.lock:
            observed = list(self.requests)
        diagnostics = (result.stdout + result.stderr).replace(SENSITIVE_MARKER, "[redacted]")[:4000]
        self.assertEqual(
            [path for path, _headers in observed],
            ["/"],
            f"Probe must make exactly one direct loopback request; child exit={result.returncode}\n{diagnostics}",
        )
        for _path, headers in observed:
            for header in ("authorization", "proxy-authorization", "cookie"):
                self.assertNotIn(header, headers, "Health probe must not forward credentials")
        self.assertNotIn(SENSITIVE_MARKER, result.stdout + result.stderr)
        return result

    def assert_rejected(self, **response):
        result = self.probe(**response)
        self.assertEqual(result.returncode, 9, result.stdout + result.stderr)
        self.assertIn("VORTEX_HTTP_ERROR:", result.stdout)
        self.assertNotIn("VORTEX_HTTP_OK:", result.stdout)

    def test_valid_json_bypasses_default_proxy(self):
        result = self.probe(body=self.valid_body())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("VORTEX_HTTP_OK:" + PINNED_COMMIT, result.stdout)

    def test_redirect_is_not_followed(self):
        self.assert_rejected(status=302, body=self.valid_body(), headers={"Location": "http://127.0.0.1:9000/redirect-target?token=" + SENSITIVE_MARKER})

    def test_oversize_content_length_is_rejected(self):
        body = self.valid_body()
        # The whole document remains valid JSON; accepting an oversized body
        # would succeed rather than merely failing JSON parsing by accident.
        body += b" " * (65537 - len(body))
        self.assert_rejected(body=body)

    def test_oversize_response_without_content_length_is_rejected(self):
        body = self.valid_body()
        body += b" " * (65537 - len(body))
        self.assert_rejected(body=body, include_length=False)

    def test_invalid_json_is_sanitized(self):
        self.assert_rejected(body=("not-json:" + SENSITIVE_MARKER).encode("ascii"))

    def test_wrong_pin_is_rejected_and_sanitized(self):
        self.assert_rejected(body=self.valid_body(commit=SENSITIVE_MARKER))

    def test_empty_body_is_rejected(self):
        self.assert_rejected(body=b"")


if __name__ == "__main__":
    unittest.main()
