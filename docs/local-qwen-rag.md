# Qwen local answering and RAG compression preview

This branch continues the retention work in PR #46 on top of main, including the Moises handoff changes from PR #47.
It implements an optional **text and extracted-evidence** Qwen provider. It does
not establish a completed multimodal rollout, and it does not update FUPCJ.

## What works in the application code

- `qwen3.8-local` / `local_llama_cpp` is listed first among featured models. Model
  discovery works without an OpenAI key and survives cloud-catalog failures.
- The authenticated backend accepts local runs without a cloud key or funding
  binding. No cloud fallback is used. Existing cloud runs retain their provider,
  model, funding, and response identities when users change models.
- Local runs reuse `project_runs`, saved conversation ancestry, revision-pinned
  board retrieval, local document/Whisper preparation, and local memory. Files
  work with code execution disabled. Pixel-only files fail clearly if no text is
  available; prepared images are never represented as having been seen by Qwen.
- A shared OS file lock holds through preparation, inference, process shutdown,
  and final response persistence. The durable lease carries a monotonically
  increasing fencing value. Claiming rechecks server insertion order under that
  lock. A delayed head local request cannot be bypassed by another local request.
  Each account may have three outstanding requests; the existing global cap is
  25. No account identities or prompt contents enter public queue metadata.
- The managed Windows process binds only `127.0.0.1:8101`, has one slot and a fresh
  private API credential, disables the web UI and context shifting, and ignores
  HTTP proxy environment variables. It starts suspended, is placed in a Windows
  kill-on-close Job, then resumes. The adapter stops/reaps the process before
  releasing the lock. A restart marks interrupted answers honestly, retains
  partial output, and leaves queued jobs durable. It does not pretend to resume KV state.
- The initial profile uses 4,096 context tokens, at most 2,048 generated tokens,
  eight CPU threads, and **zero GPU layers for the first diagnostic**. Thinking
  uses the output budget. Private reasoning is not saved as response text or
  logs. The actual rendered prompt is measured with llama.cpp's template and
  tokenizer endpoints; oversized requests fail instead of silently truncating
  source records. Whole old history messages may be excluded and are counted in
  local metrics. Exact tokenizer/template correspondence still needs the real-model check.
- Available system memory is checked before loading and monitored during work,
  with at least an 8 GiB reserve (10 GiB initial setting). This is a safeguard,
  not a measured livestream-safe hardware profile. No GPU allocation budget has
  been certified. A processing deadline and resource failures stop the child.
- A real bundled Simple Icons Qwen SVG remains white; the name has a mostly-blue
  gradient with restrained yellow. Reduced motion is supported. The first-use
  disclosure and acknowledgement are account/version scoped. Local dictation
  uses PC Whisper and cannot silently send to Gemini.
- The response pane shows queue position, phase, elapsed waiting/processing time,
  and an indeterminate bar. ETA is explicitly uncalibrated; no completion
  percentage is inferred from the output-token ceiling.

## Default behavior and activation boundary

Ordinary installation **does not change anyone's default**. The migration is
implemented behind `localAI.defaultEpoch = "qwen3_8_local_v1"` but that epoch is
not enabled in shipped configuration. The `EnableDefault` helper deliberately
refuses activation because tested photo input and the full hardware/quality
acceptance gates are unfinished.

Once a future validated deployment enables the epoch, existing and new accounts
receive Qwen once, including accounts with no prior preference row. An explicit
later model choice becomes the account default. Revision/epoch checks reject
stale writes and old-tab submissions. Historical and accepted runs are unchanged.
Cloud settings are retained in separate per-model profiles. Merely loading an
old conversation does not override the account's newer explicit choice.

Explicit selection is usable independently after the operator installs,
benchmarks, and enables the optional runtime. Model absence/offline operation
does not select a paid alternative. Existing preferences, accounts and data stay
in the existing SQLite/Windows installation.

## RAG: what is smaller, what is bounded

RAG indexing does **not** require the answering model. Existing local extraction,
SQLite lexical search and optional CPU embeddings remain the indexing pipeline.
Unchanged sources reuse their prepared evidence.

New extraction-cache entries use lossless DEFLATE when it is smaller. Repeated
words and records compress without removing wording, decimals, negations,
identifiers, repeated events or locators. Older uncompressed cache rows still
load. Decompression checks the declared uncompressed length, end-of-stream, and
the same extraction budget before accepting the result. Rebuild derives the
cache again from retained originals. The FTS index and originals still consume
space; this is not a claim that total retained storage shrinks by the same ratio.

The export dialog offers an optional `.rag.txt.gz` download. Decompression
restores exactly the readable snapshot; unzip it before giving it to an AI that
does not accept gzip. It is a selected-evidence snapshot, not a complete backup.
Lossless compression reduces bytes on disk, not the tokens needed after decoding.
Retrieval is the step that reduces the evidence sent for an individual question.

Indexing reports real source counts and embedding batches, with indeterminate
reading/saving stages. Closing the export dialog or its two-minute wait timeout
does not cancel accepted backend indexing. Reopening reconnects to the saved
revision. No fabricated remaining-time estimate is shown.

**The existing large-file limits are not removed in this slice:** 64 MiB serialized
board snapshot, 50 MiB individual context source, 4,000,000-character text preview,
40,000 units, configured derived-text budget, and 128 MiB Venture upload limit.
Some parsers apply additional limits. A 400+ MB text upload is therefore **not
fully supported by the current board/RAG path**. Raising one constant would still
leave browser base64 copies, parsing truncation and memory pressure. The remaining
work is streamed server uploads, immutable asset references in board JSON, staged
chunk indexing, bounded retrieval and large-source export. That should raise
useful capacity while retaining administrator-configured resource limits.

## Pinned installation and operator commands

The installer now uses the requested **Empero Qwen3.8-9B-Distill**, in the same
publisher's Q4_K_M GGUF format. This is a community distillation into Alibaba
Qwen3.5-9B's hybrid Gated DeltaNet/full-attention architecture. It replaces the
earlier 27B preview; the stable application ID remains `qwen3.8-local` so saved
model choices continue to work. The model picker identifies it as 9B Distill.
The 5.78 GB download is about 2.71 GB (32%) smaller than the old 8.49 GB weights.
Speed and answer quality still need measurement on FUPCJ.

Publisher provenance: [source model](https://huggingface.co/empero-ai/Qwen3.8-9B-Distill/tree/0934f3d2327ff2df2197495278c4c46ae5a56bd9),
[quantization and checksums](https://huggingface.co/empero-ai/Qwen3.8-9B-Distill-GGUF/tree/760121cd70bb4c36b2b5ec58eb765e0df5987efe).
The source repository does not include a separate license file; its model card
declares Apache-2.0, and the installer preserves that card and the pinned
Qwen3.5-9B base license. No vision projector is included.

The pinned llama.cpp source contains `LLM_ARCH_QWEN35` support. Sampling uses the
publisher's temperature 0.6, top-p 0.95, top-k 20. The conservative 4,096/2,048
context/output profile is retained; thinking shares the output budget. A long
reasoning attempt can therefore finish incomplete rather than producing an
answer. It must pass the real model benchmark before Enable.


| Artifact | Pin |
| --- | --- |
| Repository | `empero-ai/Qwen3.8-9B-Distill-GGUF` |
| Model revision | `760121cd70bb4c36b2b5ec58eb765e0df5987efe` |
| Model file | `Qwen3.8-9B-Q4_K_M.gguf` |
| Bytes | `5780090176` |
| SHA-256 | `df13d66021cef676f82be74053220fd75af6bf2a6a7fb77f5222ab9e50744a7a` |
| llama.cpp | `b11559`, commit `f2cc7282ce0042641df55e0e563b75e824e2b647` |
| Windows Vulkan ZIP SHA-256 | `bf2fb2a7fc5d7669ccabbb6de3f6dd6b54058f213b32e731abc62b590f8566dc` |

`local-model-manifest.json` records these pins and the base-model revision.
The installer requires **16 GiB free** for staging/runtime/headroom, plus space
for any separately required backups and retained sources. Download preserves the
quantization/source cards, publisher checksums, base-model and runtime notices. Its resumable
`.part` download must match the exact length/hash. Runtime files, including DLLs,
are verified against the pinned ZIP and on use. No weights are committed.

Run from an **authorized Administrator Windows PowerShell** session during a
maintenance window. Download the reviewed `Local-AI.ps1` to the current folder.
Its `UpdateCode` action fetches the normal updater from the same immutable commit
before running it, so even older installations receive the new processor-file
inventory. The normal updater can stop/restart the application. Do not use a
moving branch for production installation.

```powershell
$Root = Join-Path $env:ProgramData 'VisionPC'
# Replace REVIEWED_40_CHARACTER_SHA with the reviewed PR head, after its checks.
& .\Local-AI.ps1 -Action UpdateCode -SourceRef REVIEWED_40_CHARACTER_SHA
& "$Root\Local-AI.ps1" -Action Inventory
# Download is separate and explicit: approximately 5.78 GB of model weights.
& "$Root\Local-AI.ps1" -Action Download
& "$Root\Local-AI.ps1" -Action Check
& "$Root\Local-AI.ps1" -Action Benchmark -GpuLayers 0 -ContextTokens 4096
# Then compare measured partial offload during an approved quiet window.
& "$Root\Local-AI.ps1" -Action Benchmark -GpuLayers 16 -ContextTokens 4096
# Enable ONLY the exact profile whose report and normal-load impact were reviewed.
& "$Root\Local-AI.ps1" -Action Enable -GpuLayers 16 -ContextTokens 4096
```

The `16` GPU layers above are a comparison candidate, **not a tuned recommendation**.
`Enable` checks a successful smoke report tied to the installed manifest. A smoke
answer does not certify full quality or resource safety. Restart the existing
`Vision Private PC` task during the approved maintenance window to load the
configuration. Download/Check/Benchmark/Enable themselves do not stop that task,
alter Tailscale, replace global drivers, change power profiles, or migrate DPAPI
identity. They share the local inference lock. The complete rollback backup
must include code, config, DATA_DIR and every configured external permanent path;
the prior Phase 1A backup helper does not cover all of that automatically.

`Local-AI.txt` is byte-identical to `Local-AI.ps1` for transfer. `UpdateCode` is a
wrapper around the existing updater and requires `-SourceRef` to be a full SHA.
Download always returns local answering to the CPU diagnostic profile, disabled,
with no default epoch. It updates all model/runtime/manifest paths, preserves
unrelated settings, any custom loopback port, and higher existing RAM reserves, and keeps earlier weights and
provenance files for recovery. An enabled installation must first be disabled and
restarted during maintenance. Existing benchmark reports cannot enable the new
model: both the manifest hash and GPU/context/thread/output profile must match.

To avoid multiline paste reordering in Windows PowerShell, bootstrap the updater
with this **single line**, after replacing the SHA placeholder with the reviewed
commit. This downloads the helper and updates PC application code; it can restart
Vision but does not download weights or enable answering:

```powershell
& { $ErrorActionPreference = 'Stop'; $visionRef = 'REVIEWED_40_CHARACTER_SHA'; if ($visionRef -notmatch '^[a-fA-F0-9]{40}$') { throw 'Enter the reviewed full commit SHA first.' }; $visionScript = Join-Path $env:TEMP 'Vision-Local-AI.ps1'; Invoke-WebRequest -UseBasicParsing -Uri ('https://raw.githubusercontent.com/JRDN-R/vision/' + $visionRef + '/vision-pc/Local-AI.ps1') -OutFile $visionScript; powershell.exe -NoProfile -ExecutionPolicy Bypass -File $visionScript -Action UpdateCode -SourceRef $visionRef; if ($LASTEXITCODE -ne 0) { throw 'Vision code update failed.' } }
```

Then run Download separately (this is the 5.78 GB model install):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:ProgramData\VisionPC\Local-AI.ps1" -Action Download
```

Do not run Download until an appropriate storage/maintenance window is approved.

## Rollback and remaining gates

Use `-Action Disable`, drain/cancel local work, then restart during maintenance.
The additive selection/run columns can remain. For old code, use the established
compatible updater recovery process; preserve all newer conversation/asset data.
Do **not** overwrite a live database with a pre-deployment backup. The helper's
`Rollback` action refuses an unsafe automatic restore and explains this boundary.
Derived compressed caches are new-format data: when reverting the context engine,
remove/rebuild only derived `context_cache` rows, never originals or user projects.

Operator inventory received October 11, 2026: Windows 10 Pro build 19045, Ryzen
9 5950X (16 cores/32 threads), Radeon RX 5700 XT driver 32.0.12019.1028, about
47.9 GiB usable RAM / 31.1 GiB available, and 82.7 GiB free on C:. The existing
Vision Private PC task was running. This meets the download-space check; it is
not a Vulkan allocation or safe livestream-load measurement.

Still pending: Vulkan allocation and VRAM headroom, model load/latency/quality tests, real workload coexistence, verified
photo input/projector or sequential visual worker, calibrated ETA, warm runtime
reuse, bounded additional-evidence tool loop, large streamed source ingestion,
complete durable server-side board attachment, >120-frame asset references,
comprehensive exports and full rollback automation. Code execution and external
web search remain unavailable in local mode.

## Requirement and test map

| Requirement | Implementation | Verification |
| --- | --- | --- |
| No-key/no-hidden-cloud local answering | `local_inference.py`, `sessions.py`, `venture_dictation.py` | `test_local_inference.py` |
| Durable FIFO, isolation, cancellation/recovery | existing runs + local OS lock/lease | `test_local_inference.py` |
| One-time default and later explicit choices | `local_preferences.py`, `venture-local.js`, `venture.js` | migration/stale-tab/history tests |
| Provider identity and no cloud charge | `sessions.py`, `venture_billing.py` | no-key integration and billing suite |
| Qwen branding/disclosure/progress | `venture-models.js`, `venture-local.js`, `venture.css` | `venture-local.test.cjs`; browser checks in CI |
| Lossless cache/export and honest index progress | `context_engine.py`, `rag-export.js` | `test_context_compression.py`, `rag-export.test.cjs` |
| Pinned Windows runtime/install checks | manifest, `Local-AI.ps1`, `setup_local_ai.py`, `local_runtime.py` | Windows parser CI; real model/hardware pending |
| Prior source retention | PR #46 ownership work retained | existing ownership and server regressions |

Offline tests use an explicitly simulated inference protocol and real local
SQLite/file preparation. They are not evidence of Qwen benchmark speed or photo
accuracy. No live activation, merge, model download to FUPCJ, or production
service interruption was performed by this implementation task.
