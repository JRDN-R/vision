# Qwen Ultra on Venture

Qwen Ultra is an additional `qwen-ultra-local` model. It keeps the existing
Qwen3.8 Local and its optional 9B Heretic variant, histories, attachments and
account preferences. Its gold label and logo reuse both existing Qwen animation
clips, with a gold shimmer and reduced-motion fallback.

## Install on the Windows processor

Download `vision-pc/Qwen-Ultra.ps1` from this repository. In Administrator
Windows PowerShell, from the directory containing the script:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Qwen-Ultra.ps1
```

Run this outside livestream work and close OBS/Streamlabs first. `Install`
updates/restarts the existing Vision processor using one resolved GitHub commit,
downloads the model, checks SHA-256 and size, benchmarks it, tests conversation
continuation with thinking and a near-capacity context, then enables Ultra.
It requires the existing pinned Qwen Local runtime and an enabled localAI
installation. It does not change any account's default model. Refresh the
updated Vision app and explicitly select **Qwen Ultra**.

Pass `-SourceRef <40-character-reviewed-commit>` to pin application code. The
standalone download supplied with the change is already pinned. Use
`-SkipCodeUpdate` when the server already runs that code. Resumable model bytes
are retained after interrupted downloads; original models are never overwritten.

After the code update, these commands operate without a server restart:

```powershell
.\Qwen-Ultra.ps1 -Action Download
.\Qwen-Ultra.ps1 -Action Check
.\Qwen-Ultra.ps1 -Action Tune
.\Qwen-Ultra.ps1 -Action Tune -ContextTokens 16384
.\Qwen-Ultra.ps1 -Action Disable
```

Tune uses the same OS inference lock as Venture and exits if local work is
active. Requests accepted while tuning remain queued. Disable removes only the
Ultra readiness receipt. Removing/changing the receipt prevents queued Ultra
requests from silently using another model. It never falls back to a cloud API.

## Model and disk requirements

- Upstream: [llmfan46/Qwen3.5-27B-ultra-uncensored-heretic-v1](https://huggingface.co/llmfan46/Qwen3.5-27B-ultra-uncensored-heretic-v1),
  source revision `0b134ea2c3e0fef5f4f78194d1c86258c1c81b8f`.
- The former upstream name `llmfan46/Qwen3.5-27B-heretic-v2` redirects to that model.
  The quantizer explicitly identifies that former name as its source.
- Quantizer: [JunLee88999/Qwen3.5-27B-heretic-v2-GGUF](https://huggingface.co/JunLee88999/Qwen3.5-27B-heretic-v2-GGUF/tree/85183955df1ec9eb613a816e0a6afc74e2807abb).
- Revision: `85183955df1ec9eb613a816e0a6afc74e2807abb`.
- File: `Qwen3.5-27B-heretic-v2-Q4_K_M.gguf`.
- Bytes: **16,540,271,744**: 16.54 GB / 15.40 GiB additional model storage.
- SHA-256: `56275d30c9e49a02584f5fcd2569275ac8c2c9e524d42203755bd4436cb38b7a`.
- Allow at least the remaining download size plus 2 GiB disk headroom
  (18.7 GB free for a fresh model download); application-update backups use
  additional space. Existing model/runtime files and backups remain on disk.
- Uses the existing pinned llama.cpp b11559 Windows Vulkan runtime, commit
  `f2cc7282ce0042641df55e0e563b75e824e2b647`. No extra runtime is downloaded.
- Apache-2.0 is declared by the upstream model. Metadata/pins checked 2026-10-13.

The publisher's “Ultra-Uncensored” name does not certify accuracy or zero
refusals. Installation does not run a refusal benchmark.

## Software performance tuning

This tunes inference rather than clocks: no BIOS, voltage, overclock, power-plan
or permanent Windows-priority changes. The existing runtime uses below-normal
priority, a kill-on-close Job, resource monitoring and a loopback authenticated
server. On a 16-core 5950X the candidates are **8, 12 and 14 CPU threads**.

The Vulkan device list must report free VRAM before GPU trials are attempted.
Trials use 4, 8 or 12 layers only when their conservative estimated allocation
leaves at least 2 GiB and half the reported free VRAM. These are **layer counts,
not CPU/GPU utilization percentages**. An unsupported/unknown device report
retains the CPU profile. Failed candidates are recorded and not selected.

The score compares model verification/loading plus 1,024 prompt tokens and a
2,048-token output, using measured prompt and generation rates. Auto uses the
fastest successful candidate only if it beats the best CPU candidate by over 5%.
Both selected profiles must pass conversation and near-capacity context checks.
The installer saves a local `tuning-report.json` and a readiness receipt beside
the model under `local-ai/ultra/<revision>` (or beside the existing custom model
path). No prompts or machine inventory are uploaded.

The normal default remains an 8,192-token deployed context, with a selectable
4,096-token setting. A successful explicit 16,384-token Tune unlocks that option.
The upstream 262,144-token architectural limit is not offered as an untested
48 GB PC setting. Output is 512–4,096 tokens and must leave at least 576 context
tokens for the request/template. Thinking consumes the same output budget.
Temperature, top P, top K, min P, repeat/presence penalties and seed map directly
to runtime parameters. Hardware profiles are server-resolved, never client paths
or arbitrary command arguments. Each queued job snapshots its effective profile.

The 27B profile reserves at least 10 GiB system RAM (or the existing higher
reserve), stops over 30 GiB process RSS, and has a two-hour job time limit.
These checks and a short benchmark do not certify livestream coexistence.

## Capabilities and waiting time

Ultra uses Venture's existing authenticated file preparation, board retrieval,
history and memory. Image/frame analysis uses the **existing separate visual
worker**, running before the 27B answering model. Its original model/projector
paths remain anchored; Ultra does not download a second projector or claim native
27B pixel processing. Vision is advertised only after this backend confirms
the integrated path. Web search, code execution and image generation remain
unavailable; code writing/review and reading supported files work as before.

The composer shows a “grab a coffee” range and approximate local-clock return
window from measured loading, prompt and generation speed. Before submission it
uses the full available prompt/output budget, including thinking. Its uncertainty
range is 0.65–1.6 times that budget estimate, not a statistical confidence interval.
Answers may finish sooner. Queueing, attachments and other PC work can add time.
In-progress estimates prefer this account's comparable completed requests;
isolated text turns can use the benchmark until enough history exists. Unknown
visual/queue duration and overruns remain explicit rather than counting to zero.

## Verification boundary

Offline tests exercise parameter rejection, profile pinning, CPU/GPU candidate
selection, benchmark estimation, template/sampling forwarding, authenticated
no-key routing, no billing, account isolation, conversation continuation and
receipt invalidation. Browser tests cover mobile/desktop layout, original Qwen
animation, Ultra gold shimmer, reduced motion, settings restoration and timing.
Windows CI parses the PowerShell script; it does not install large model weights.

The real 27B model, its Windows driver and actual CPU/GPU throughput cannot be
verified by these fixtures. The installer performs those local checks before
enabling Ultra. Exact speed or a percentage performance improvement is not
promised in advance.
