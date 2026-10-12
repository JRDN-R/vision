"""Private managed llama.cpp process. No remote endpoints or arbitrary tools.

One process is started under the application-wide OS lock per answering job.
It is terminated and reaped before that lock is released, including cancellation.
This conservative first profile pays model-load cost on each turn.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time

import requests

MODEL_BYTES = 8491269152
MODEL_SHA = 'f1e75d0145ee32c3f216b336c2efa4ac8b2ff9249449159b1414187f966cc9cd'
MODEL_REVISION = '6374af6aaa473b4f3b5387db526ee533c709c6af'
RUNTIME_COMMIT = 'f2cc7282ce0042641df55e0e563b75e824e2b647'


class LocalFailure(ValueError):
    pass


def digest_file(path, cancelled=lambda: False):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            if cancelled():
                raise InterruptedError()
            digest.update(chunk)
    return digest.hexdigest()


class LlamaRuntime:
    def __init__(self, settings, cancelled, stage):
        self.settings, self.cancelled, self.stage = settings, cancelled, stage
        self.process = None
        self.guard = None
        self.done = threading.Event()
        self.stop_reason = None
        self.peak_rss = 0
        self.http = requests.Session()
        self.http.trust_env = False  # A proxy must never receive prompts/internal credentials.
        self.token = secrets.token_urlsafe(32)
        self.http.headers['Authorization'] = 'Bearer ' + self.token
        self.port = int(settings.get('port', 8101))
        if not 1024 <= self.port <= 65535:
            raise LocalFailure('Invalid local inference port.')
        self.base = 'http://127.0.0.1:' + str(self.port)

    def request(self, path, payload=None, stream=False):
        if path not in ('/health', '/apply-template', '/tokenize', '/v1/chat/completions'):
            raise LocalFailure('Unsupported local inference operation.')
        response = self.http.request('GET' if payload is None else 'POST', self.base + path,
                                     json=payload, timeout=(3, 120), stream=stream, allow_redirects=False)
        if response.status_code != 200:
            response.close()
            raise LocalFailure('The local runtime rejected this operation. Check its installed profile.')
        return response

    def json(self, path, payload=None):
        with self.request(path, payload) as response:
            if len(response.content) > 16 * 1024 * 1024:
                raise LocalFailure('Local runtime response exceeded its bound.')
            return response.json()

    def verify(self):
        runtime, model = Path(self.settings['runtimePath']), Path(self.settings['modelPath'])
        manifest = json.loads(Path(self.settings['manifestPath']).read_text(encoding='utf-8'))
        if (not runtime.is_absolute() or not model.is_absolute() or runtime.is_symlink() or model.is_symlink() or
                model.stat().st_size != MODEL_BYTES or manifest.get('modelSha256') != MODEL_SHA or
                manifest.get('modelRevision') != MODEL_REVISION or manifest.get('runtimeCommit') != RUNTIME_COMMIT):
            raise LocalFailure('Local model provenance does not match the reviewed installation.')
        self.stage('Verifying installed model')
        if digest_file(model, self.cancelled) != MODEL_SHA or digest_file(runtime, self.cancelled) != manifest.get('runtimeSha256'):
            raise LocalFailure('Local runtime/model integrity check failed. Reinstall the verified package.')
        root = Path(manifest['runtimeRoot']).resolve()
        expected = manifest['runtimeFiles']
        if not expected or not runtime.resolve().is_relative_to(root):
            raise LocalFailure('Invalid private runtime inventory.')
        for name, digest in expected.items():
            part = root / name
            if part.is_symlink() or not part.resolve().is_relative_to(root) or digest_file(part, self.cancelled) != digest:
                raise LocalFailure('A runtime library failed its integrity check.')
        if {str(p.relative_to(root)).replace('\\','/') for p in root.rglob('*') if p.is_file()} != set(expected):
            raise LocalFailure('Unexpected files in the private runtime directory.')
        return runtime, model

    @staticmethod
    def stop_child(process):
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)

    def monitor(self):
        import psutil
        deadline = time.monotonic() + max(60, min(7200, int(self.settings.get('maxRunSeconds', 1800))))
        while not self.done.wait(.25):
            try:
                self.peak_rss = max(self.peak_rss, psutil.Process(self.process.pid).memory_info().rss)
                if psutil.virtual_memory().available < max(8,float(self.settings.get('reserveRamGiB',10)))*1024**3:
                    self.stop_reason = 'Qwen stopped to preserve the configured system memory reserve.'
            except psutil.NoSuchProcess:
                return
            if time.monotonic() > deadline:
                self.stop_reason = 'Qwen reached the local processing time limit. Narrow the question or retry later.'
            if self.cancelled() or self.stop_reason:
                self.stop_child(self.process)
                return

    def __enter__(self):
        if os.name != 'nt':
            raise LocalFailure('This managed Qwen runtime requires the reviewed Windows installation.')
        runtime, model = self.verify()
        import psutil
        reserve = max(8, float(self.settings.get('reserveRamGiB', 10))) * 1024**3
        if psutil.virtual_memory().available < reserve + MODEL_BYTES:
            raise LocalFailure('Insufficient available RAM for Qwen plus the reserved system/livestream headroom.')
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', self.port))
            except OSError:
                raise LocalFailure('The local inference port is already in use. No second model was started.') from None
        args = [str(runtime), '--model', str(model), '--alias', 'qwen3.8-local',
                '--host', '127.0.0.1', '--port', str(self.port), '--parallel', '1',
                '--ctx-size', str(int(self.settings.get('contextTokens', 4096))),
                '--threads', str(max(1, min(16, int(self.settings.get('threads', 8))))),
                '--batch-size', '256', '--ubatch-size', '64',
                '--n-gpu-layers', str(max(0, min(99, int(self.settings.get('gpuLayers', 0))))),
                '--api-key', self.token, '--jinja', '--reasoning-format', 'deepseek',
                '--no-webui', '--no-context-shift']
        self.stage('Loading Qwen on FUPCJ')
        # Suspend first: assign the kill-on-close Job before any model can load.
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS | 0x00000004}
        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, **flags)
        try:
            if os.name == 'nt':
                from uploaded_media import WindowsJob
                self.guard = WindowsJob(self.process)
                psutil.Process(self.process.pid).resume()
            self.watch = threading.Thread(target=self.monitor, daemon=True)
            self.watch.start()
            end = time.monotonic() + 240
            while time.monotonic() < end and self.process.poll() is None:
                if self.cancelled():
                    raise InterruptedError()
                try:
                    if self.json('/health').get('status') == 'ok':
                        return self
                except (requests.RequestException, ValueError):
                    pass
                self.done.wait(.5)
            raise LocalFailure(self.stop_reason or 'Qwen did not become ready. Check the pinned runtime, model and memory/offload profile.')
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        self.done.set()
        try:
            self.stop_child(self.process)
        finally:
            # Closing the Job is a second shutdown guarantee even if terminate/wait failed.
            if self.guard:
                self.guard.close()
            if self.process:
                self.process.wait(timeout=10)
            self.http.close()

    def count(self, messages):
        rendered = self.json('/apply-template', {'messages': messages, 'chat_template_kwargs': {'enable_thinking': True}})
        if not isinstance(rendered.get('prompt'), str):
            raise LocalFailure('The installed runtime cannot render this chat template.')
        tokens = self.json('/tokenize', {'content': rendered['prompt'], 'add_special': True, 'parse_special': True})
        if not isinstance(tokens.get('tokens'), list):
            raise LocalFailure('The installed tokenizer did not return a token count.')
        return len(tokens['tokens'])

    def generate(self, messages, max_tokens):
        payload = dict(model='qwen3.8-local', messages=messages, stream=True,
                       stream_options={'include_usage': True}, max_tokens=max_tokens,
                       temperature=1.0, top_p=.95, top_k=20, min_p=0.0,
                       presence_penalty=0.0, repeat_penalty=1.0,
                       chat_template_kwargs={'enable_thinking': True}, cache_prompt=False, sse_ping_interval=5)
        with self.request('/v1/chat/completions', payload, stream=True) as response:
            for line in response.iter_lines(chunk_size=512):
                if self.cancelled():
                    raise InterruptedError()
                if len(line) > 1024 * 1024:
                    raise LocalFailure('The local token stream exceeded its bound.')
                if not line.startswith(b'data: '):
                    continue
                if line == b'data: [DONE]':
                    return
                yield json.loads(line[6:])
