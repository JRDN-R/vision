"""Optional real, offline CPU embeddings. Never downloads or calls a paid API.

Vectors are normalized float32 blobs in the engine's existing SQLite database.
For bounded personal projects exhaustive dot products avoid another service or a
native sqlite extension on Windows. Exact/FTS retrieval remains available without
the optional model; capability reports this honestly.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import struct
import sys
import threading


class LocalEmbeddings:
    def __init__(self, settings=None):
        self.settings = settings or {}
        self.lock = threading.Lock()
        self.model = None
        self.attempted = False
        self.error = None
        self.identity = 'none'

    def _load(self):
        if self.attempted:
            return self.model
        self.attempted = True
        path = Path(self.settings.get('embeddingModelPath') or '__not_installed__')
        if not path.is_dir():
            self.error = 'Local semantic model is not installed; exact and lexical retrieval are active.'
            return None
        try:
            # Only administrator-configured paths are accepted, never uploaded source paths.
            packages = self.settings.get('embeddingPackagesPath')
            if packages and Path(packages).is_dir() and packages not in sys.path:
                sys.path.insert(0, packages)
            os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
            import torch
            from sentence_transformers import SentenceTransformer
            torch.set_num_threads(max(1, min(4, int(self.settings.get('embeddingThreads', 2)))))
            self.model = SentenceTransformer(str(path.resolve()), device='cpu',
                                            local_files_only=True, trust_remote_code=False,
                                            model_kwargs={'use_safetensors': True})
            fingerprint = hashlib.sha256()
            for candidate in sorted(path.rglob('*')):
                if candidate.suffix not in ('.json','.txt','.safetensors') or candidate.name=='vision-context-model.json' or '.cache' in candidate.parts:
                    continue
                if candidate.is_file():
                    fingerprint.update(str(candidate.relative_to(path)).encode())
                    with candidate.open('rb') as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b''):
                            fingerprint.update(block)
            self.identity = 'local-multiwindow-v2:' + fingerprint.hexdigest()[:20]
        except Exception:
            # Avoid logging model paths/environment/credentials in ordinary diagnostics.
            self.error = 'Local embedding model could not load; exact and lexical retrieval remain active.'
            self.model = None
        return self.model

    def encode(self, texts):
        with self.lock:
            model = self._load()
            if model is None:
                return [None] * len(texts)
            try:
                if not texts:
                    return []
                results = []
                for text in texts:
                    # Preserve semantic access to long-record suffixes instead
                    # of silently embedding only the first 256 model tokens.
                    token_ids = model.tokenizer.encode(text, add_special_tokens=False, verbose=False)
                    size = max(32,min(480,int(model.max_seq_length)-16))
                    windows = [model.tokenizer.decode(token_ids[start:start+size],skip_special_tokens=True)
                               for start in range(0,max(1,len(token_ids)),size-32)]
                    vectors = model.encode(windows, batch_size=16, normalize_embeddings=True,
                                           convert_to_numpy=True, show_progress_bar=False)
                    dimension = len(vectors[0])
                    payload = b'CTXV1'+struct.pack('<II',dimension,len(vectors))
                    payload += b''.join(struct.pack('<'+'f'*dimension,*vector) for vector in vectors)
                    results.append(payload)
                return results
            except Exception:
                self.error = 'Local embedding generation failed; lexical evidence is retained.'
                return [None] * len(texts)

    def capability(self):
        return dict(ready=self.model is not None, model=self.identity,
                    status='ready' if self.model is not None else 'lexical-only', warning=self.error)

    @staticmethod
    def similarity(a, b):
        if not a or not b:
            return -1.0
        def unpack(value):
            if value.startswith(b'CTXV1') and len(value)>=13:
                dimension,count = struct.unpack('<II',value[5:13])
                if not dimension or len(value)!=13+count*dimension*4:
                    return []
                return [struct.unpack('<'+'f'*dimension,value[13+i*dimension*4:13+(i+1)*dimension*4]) for i in range(count)]
            return [struct.unpack('<'+'f'*(len(value)//4),value)] if len(value)%4==0 else []
        return max((sum(x*y for x,y in zip(left,right)) for left in unpack(a) for right in unpack(b) if len(left)==len(right)),default=-1.0)
