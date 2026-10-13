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

MODEL_BYTES = 5780090176
MODEL_SHA = 'df13d66021cef676f82be74053220fd75af6bf2a6a7fb77f5222ab9e50744a7a'
MODEL_REVISION = '760121cd70bb4c36b2b5ec58eb765e0df5987efe'
MODEL_REPOSITORY = 'empero-ai/Qwen3.8-9B-Distill-GGUF'
MODEL_FILE = 'Qwen3.8-9B-Q4_K_M.gguf'
RUNTIME_COMMIT = 'f2cc7282ce0042641df55e0e563b75e824e2b647'
# Independent community conversion of the exact requested rohit267 model.
# Pins checked against its Hugging Face commit and Git LFS SHA-256 pointer.
HERETIC_SPEC = dict(
    modelRepository='mradermacher/Qwen3.8-9B-heretic-uncensored-GGUF',
    modelRevision='6fa1707e4f5a664800cff1012e991dc258ccdc86',
    modelFile='Qwen3.8-9B-heretic-uncensored.Q4_K_M.gguf',
    modelBytes=5629109728,
    modelSha256='8eb51ad276c3b529266b800a2bd866ba03337709db6a1b4758243ffc5c96510f',
    runtimeCommit=RUNTIME_COMMIT)


def model_spec(settings):
    variant = settings.get('modelVariant', 'standard')
    if variant == 'ultra':
        from local_ultra import SPEC
        return dict(SPEC)
    if variant == 'heretic':
        return dict(HERETIC_SPEC)
    if variant != 'standard':
        raise LocalFailure('Unknown local model variant. No replacement was selected.')
    return dict(modelRepository=MODEL_REPOSITORY, modelRevision=MODEL_REVISION,
                modelFile=MODEL_FILE, modelBytes=MODEL_BYTES,
                modelSha256=MODEL_SHA, runtimeCommit=RUNTIME_COMMIT)


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
    alias = 'qwen3.8-local'
    thinking = True

    def __init__(self, settings, cancelled, stage):
        self.settings, self.cancelled, self.stage = settings, cancelled, stage
        self.alias = settings.get('modelId', self.alias)
        self.thinking = settings.get('thinking', self.thinking)
        self.process = None
        self.guard = None
        self.done = threading.Event()
        self.stop_reason = None
        self.peak_rss = 0
        self.minimum_available_ram = None
        self.cpu_seconds = 0
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
        manifest = json.loads(Path(self.settings['manifestPath']).read_text(encoding='utf-8-sig'))
        spec = model_spec(self.settings)
        if (not runtime.is_absolute() or not model.is_absolute() or runtime.is_symlink() or model.is_symlink() or
                model.stat().st_size != spec['modelBytes'] or
                any(manifest.get(key) != value for key, value in spec.items())):
            raise LocalFailure('Local model provenance does not match the reviewed installation.')
        self.stage('Verifying installed model')
        if digest_file(model, self.cancelled) != spec['modelSha256'] or digest_file(runtime, self.cancelled) != manifest.get('runtimeSha256'):
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

    def model_bytes(self):
        return model_spec(self.settings)['modelBytes']

    def command(self, runtime, model):
        return [str(runtime), '--model', str(model), '--alias', self.alias,
                '--host', '127.0.0.1', '--port', str(self.port), '--parallel', '1',
                '--ctx-size', str(int(self.settings.get('contextTokens', 4096))),
                '--threads', str(max(1, min(16, int(self.settings.get('threads', 8))))),
                '--batch-size', str(int(self.settings.get('batchSize',256))),
                '--ubatch-size', str(int(self.settings.get('ubatchSize',64))),
                '--n-gpu-layers', str(max(0, min(99, int(self.settings.get('gpuLayers', 0))))),
                '--api-key', self.token, '--jinja', '--reasoning-format', 'deepseek',
                '--no-webui', '--no-context-shift']

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
                process = psutil.Process(self.process.pid)
                self.peak_rss = max(self.peak_rss, process.memory_info().rss)
                cpu = process.cpu_times()
                self.cpu_seconds = cpu.user + cpu.system
                available = psutil.virtual_memory().available
                self.minimum_available_ram = min(available, self.minimum_available_ram if self.minimum_available_ram is not None else available)
                if self.peak_rss > float(self.settings.get('maxResidentGiB', 16)) * 1024**3:
                    self.stop_reason = 'Qwen exceeded the configured resident-memory limit.'
                if available < max(8,float(self.settings.get('reserveRamGiB',10)))*1024**3:
                    self.stop_reason = 'Qwen stopped to preserve the configured system memory reserve.'
            except psutil.NoSuchProcess:
                return
            except psutil.Error:
                self.stop_reason = 'Local resource monitoring became unavailable.'
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
        if psutil.virtual_memory().available < reserve + self.model_bytes():
            raise LocalFailure('Insufficient available RAM for Qwen plus the reserved system/livestream headroom.')
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', self.port))
            except OSError:
                raise LocalFailure('The local inference port is already in use. No second model was started.') from None
        args = self.command(runtime, model)
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
            end = time.monotonic() + (600 if self.settings.get('modelVariant') == 'ultra' else 240)
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
        rendered = self.json('/apply-template', {'messages': messages, 'chat_template_kwargs': {'enable_thinking': self.thinking}})
        if not isinstance(rendered.get('prompt'), str):
            raise LocalFailure('The installed runtime cannot render this chat template.')
        tokens = self.json('/tokenize', {'content': rendered['prompt'], 'add_special': True, 'parse_special': True})
        if not isinstance(tokens.get('tokens'), list):
            raise LocalFailure('The installed tokenizer did not return a token count.')
        return len(tokens['tokens'])

    def generate(self, messages, max_tokens):
        sampling = self.settings.get('sampling') or {}
        payload = dict(model=self.alias, messages=messages, stream=True,
                       stream_options={'include_usage': True}, max_tokens=max_tokens,
                       temperature=sampling.get('temperature',.6), top_p=sampling.get('topP',.95),
                       top_k=sampling.get('topK',20), min_p=sampling.get('minP',0.),
                       presence_penalty=sampling.get('presencePenalty',0.), repeat_penalty=sampling.get('repeatPenalty',1.),
                       seed=sampling.get('seed',-1),
                       chat_template_kwargs={'enable_thinking': self.thinking}, cache_prompt=False, sse_ping_interval=5)
        if self.settings.get('benchmark'):
            payload['timings_per_token'] = True
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
