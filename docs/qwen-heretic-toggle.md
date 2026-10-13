# Qwen Local: optional Heretic switch

Qwen keeps its existing `qwen3.8-local` identity, model files, default selection,
history, attachments and local visual worker. In its existing settings, **Uncensored**
is off by default. Off selects the original Empero 9B Distill; on selects the
separate Qwen3.8-9B-heretic-uncensored Q4_K_M weights. This is model selection,
not a system-prompt trick or removal of authentication/resource protections.
The publisher's “uncensored” name is not a guarantee of zero refusals.

## Pinned additional model

- Quantizer: `mradermacher/Qwen3.8-9B-heretic-uncensored-GGUF`.
- Upstream named by the quantizer: `rohit267/Qwen3.8-9B-heretic-uncensored`.
- Revision: `6fa1707e4f5a664800cff1012e991dc258ccdc86`.
- File: `Qwen3.8-9B-heretic-uncensored.Q4_K_M.gguf`.
- Bytes: `5629109728` (about 5.63 GB additional disk space).
- SHA-256: `8eb51ad276c3b529266b800a2bd866ba03337709db6a1b4758243ffc5c96510f`.
- Runtime: existing pinned llama.cpp `f2cc7282ce0042641df55e0e563b75e824e2b647`.
- Quantizer declares Apache-2.0 and Qwen3.5 architecture. The original upstream
  model card returned 401 during this review; no independent refusal benchmark
  or source-weights provenance beyond the quantizer's declaration is claimed.

Sources checked 2026-10-12:
https://huggingface.co/mradermacher/Qwen3.8-9B-heretic-uncensored-GGUF
https://huggingface.co/mradermacher/Qwen3.8-9B-heretic-uncensored-GGUF/commit/6fa1707e4f5a664800cff1012e991dc258ccdc86
https://huggingface.co/mradermacher/Qwen3.8-9B-heretic-uncensored-GGUF/raw/main/Qwen3.8-9B-heretic-uncensored.Q4_K_M.gguf

## Install on FUPCJ after the code update

First update the Windows processor to the reviewed commit containing this change
using its normal pinned updater during an authorized maintenance window. The
ordinary update ships the two helper modules but **does not download or enable
Heretic**, change model defaults, or migrate accounts. Refresh the hosted app
following its normal portable build. An old backend shows a disabled switch;
a saved on-state cannot silently send to Standard on an older server.

In Administrator PowerShell on the Windows processor:

```powershell
$Root = Join-Path $env:ProgramData 'VisionPC'
$Python = Join-Path $Root 'runtime\python.exe'
$Helper = Join-Path $Root 'setup_local_variant.py'
$Config = Join-Path $Root 'config.json'
& $Python $Helper --config $Config --action download
if ($LASTEXITCODE -ne 0) { throw 'Heretic download failed; stop here.' }
& $Python $Helper --config $Config --action check
if ($LASTEXITCODE -ne 0) { throw 'Heretic verification failed; stop here.' }
```

The download is resumable and SHA/size verified. It never overwrites existing
model bytes or `config.json`. Files are stored alongside the original model at
`variants\heretic\<revision>`. Both variants reuse the verified runtime. The
existing OS inference lock prevents overlap with active local answering work;
when busy, the helper exits rather than interrupting it.

At a quiet time, with no livestream workload, explicitly run:

```powershell
& $Python $Helper --config $Config --action enable --approved-quiet-window
if ($LASTEXITCODE -ne 0) { throw 'Heretic failed its smoke test; it remains unavailable.' }
```

Enable checks the pinned original model/runtime and Heretic, loads only Heretic
in CPU mode, and runs two benign conversation turns. Only success publishes a
readiness receipt for the exact profile/manifest/files. This tests runtime,
template, final-answer separation and short conversation continuation. It is
**not** a refusal-quality test, a long-context benchmark, or OBS coexistence
acceptance. RAM/time guards remain active. No service restart is performed by
this helper. Refresh Venture and enable **Uncensored** on Qwen Local. Turning it
off immediately selects Standard for the next request, preserving the chat.

To make Heretic unavailable without deleting either model or any conversations:

```powershell
& $Python $Helper --config $Config --action disable
```

Disable requires the same idle inference lock. It removes only the readiness
receipt. The browser always permits switching off a saved on-state when the
variant is missing. Unavailable, changed or disabled Heretic never falls back
to Standard or to a cloud provider.

## Persistence and isolation

`qwenUncensored` is a validated boolean in local account/conversation settings.
Each accepted job stores a server-resolved immutable profile in the existing
`local_profile_json`; the client never chooses paths, URLs, checksums or runtime
arguments. A queued job is not changed by a later toggle or profile edit.
Historical replies retain their actual variant in response information. Older
jobs without the field are Standard. Stale tabs use the existing preference
revision conflict handling. Idempotent retries return their original job.

The two variants use distinct model paths and timing comparisons. The visual
worker stays pinned at its original location and remains sequential. Its
backend-lifetime confirmation is tracked separately per answering variant;
Standard's visual confirmation does not falsely advertise Heretic as tested.
Full SHA verification still runs before inference. Tool permissions, account
ownership, RAM reserve, local-only routing and cloud billing are unchanged.

## Validation boundary

`test_local_variants.py` tests pins, strict selection, receipts, invalidation,
no-overwrite download verification, CPU profile and original visual paths using
small explicit fixtures. `test_local_variant_integration.py` exercises real
Firebase-style authenticated Flask/SQLite routing and persistence with an
explicitly simulated inference protocol. `venture-local.test.cjs` covers the
switch, missing/older-backend handling and separate visual capability.

No real Heretic weights were downloaded or run during code preparation. FUPCJ
installation, iPhone interaction, actual model quality and OBS impact remain
unverified until the operator performs the above checks.
