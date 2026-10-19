# Qwen Ultra on Venture

Qwen Ultra is an additional `qwen-ultra-local` model. It keeps the existing
Qwen3.8 Local and its optional 9B Heretic variant, histories, attachments and
account preferences. Its gold label and logo reuse both existing Qwen animation
clips, with a gold shimmer and reduced-motion fallback.

## Weekly warm standby

After updating the Windows processor, installed and tuned Ultra stays loaded
on **Monday, Tuesday, Thursday, Friday and Saturday** when memory is available.
**Sunday and Wednesday are on demand**: requests still work, and Ultra unloads
afterward. Both schedules use the Windows server's local clock; keep that PC's
time zone set to Eastern Time for the Jacksonville schedule, including DST.
The server must remain running and the PC awake for standby to be available.

Standby selects the **highest verified context** and the measured `auto`
performance profile. It never raises the context beyond the readiness receipt.
Compatible Venture requests reuse the loaded process. Different context,
performance or model selections cause a reload using the accepted request's
settings. Sampling/thinking controls are applied separately to each request.
Selecting Local for a request releases Ultra; after Local finishes, Ultra
standby returns automatically. Account model selections are not changed.

MediaShare/title jobs get the next free model lease. Idle Ultra unloads before
they load the lightweight visual model, then returns after the title queue
drains. An active Venture answer finishes first, unless the user cancels it or
an existing resource/deadline safeguard stops it. Existing MediaShare Sunday/
Wednesday standby stays in effect. Video, naming and Venture visual evidence
also release Ultra before loading another model. The single OS inference lock
is held continuously while any process is resident, including idle standby.
Mac Overdrive retains its per-request routing and fallback behavior; distributed
processes are not kept resident.

The **30 GiB Ultra RSS ceiling**, **at least 10 GiB system reserve**, and existing
per-job processing limit remain in force. Idle time does not consume a later
request's processing budget. The full model/runtime hashes are checked at load;
receipt, file identities, timestamps and runtime inventory are checked before
reuse. Errors/cancellation discard that process. Idle memory guards run all day.
Failed preloads back off for a minute and never download models automatically.

Authenticated `GET /api/venture/ultra/standby` reports the schedule, current
phase, loaded state, actual resident bytes, loaded context and load count.
The model catalog also includes this under Ultra's `standby` field. These
measurements come from the Windows runtime, not model file-size estimates.
Set `localAI.ultraStandby` to `false` in the server configuration and restart
to disable only preloading; on-demand Ultra remains available.

To update and validate the largest context that fits, run the existing installed
helper in **Administrator PowerShell** while no jobs or livestream are active:

```powershell
& "$env:ProgramData\VisionPC\Qwen-Ultra.ps1" -Action Install -MaxContext
```

`Install` first fetches one current server commit and restarts the processor,
then checks the existing model and runs maximum-context validation. The helpers
ask idle standbys to yield for maintenance and wait up to ten seconds for the
inference lease. They exit if active work still owns it, without killing that
work. Standby resumes when maintenance releases its locks. A failed larger
context retains the last passing result. Choose **Maximum verified** in Venture
if an older explicit numeric context was saved. Updating code alone enables
standby at the current verified size; it does not run hours of tuning silently.

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

Pass `-SourceRef <40-character-reviewed-commit>` to pin application code. Use
`-SkipCodeUpdate` when the server already runs that code. Resumable model bytes
are retained after interrupted downloads; original models are never overwritten.

After the code update, these commands operate without a server restart:

```powershell
.\Qwen-Ultra.ps1 -Action Download
.\Qwen-Ultra.ps1 -Action Check
.\Qwen-Ultra.ps1 -Action Tune
.\Qwen-Ultra.ps1 -Action Tune -ContextTokens 16384
.\Qwen-Ultra.ps1 -Action Tune -ContextTokens 32768
.\Qwen-Ultra.ps1 -Action Disable
```

Explicit `-ContextTokens 65536`, `-ContextTokens 131072`, and
`-ContextTokens 262144` are supported only when the actual Windows PC passes
the larger-context checks. To find the largest verified context automatically,
run the updated installer during a quiet maintenance window:

```powershell
.\Qwen-Ultra.ps1 -Action Tune -MaxContext
```

`-MaxContext` tests progressively larger windows (8,192 through the model's
262,144-token architectural ceiling), commits each passing context, then stops
at the first failed or resource-limited test. The last passing context remains
in service. Failures and results are recorded locally in
`context-ceiling-report.json`. Large tests can take considerable time.
RAM, clock, VRAM and runtime guards are unchanged.

In Venture, choose **Context window → Maximum verified**. New Ultra choices
use this automatically. Previously saved explicit numeric context selections
remain unchanged until the user changes them. Every accepted job snapshots the
verified numeric context from the receipt, so later tuning cannot silently
change queued work. A larger window makes room for more supporting material;
it does **not guarantee** that the model is more accurate. Large-context validation
can take hours or fail because of memory, drivers, or speed; it is not a promise
that a 48 GB machine can run every setting. A failed attempt to upgrade a still-
valid smaller context keeps that smaller readiness receipt. Stale or mismatched
receipts remain disabled. Select an explicitly tested context in Venture or choose **Maximum verified**
to follow the highest passing size. The RAM limit is not increased automatically.

Tune uses the same OS inference lock as Venture and exits if local work is
active. Requests accepted while tuning remain queued. Disable removes only the
Ultra readiness receipt. Removing/changing the receipt prevents queued Ultra
requests from silently using another model. It never falls back to a cloud API.

For an already installed model, update the server with the existing
`Update-Venture.ps1`, then refresh Venture (or rebuild/download the portable
application). Updating application code restarts the processor: allow existing
jobs to finish first. A merge in GitHub alone does not update the Windows PC.

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

## Long responses: reasoning space plus answer space

**Long responses** is enabled by default for newly accepted Ultra jobs. Turn it
off in Ultra settings to retain the old single-pass output limit. Already queued
jobs without this setting retain their original profile snapshot. Other models
and paid API requests are unchanged.

In long-response mode, the existing 512–4,096 output slider is an **answer-space
reserve per pass**, not a whole-answer or whole-reasoning cap. The server counts
the actual rendered prompt with the installed tokenizer, uses the remaining
tested context for generation, and sends a separate native thinking budget:

```
generation allowance = tested context - rendered prompt - 256
thinking allowance   = max(0, generation allowance - answer reserve - 64)
```

The additional 64 tokens leave room for ending the thinking section. The pinned
runtime's `thinking_budget_tokens` field is handled by the sampling bridge in
[server-common.cpp](https://github.com/ggml-org/llama.cpp/blob/f2cc7282ce0042641df55e0e563b75e824e2b647/tools/server/server-common.cpp).
It depends on the model template exposing thinking end tags. No model weights,
safety behavior, system instructions, or memory protections are changed.

For example, with a **validated 32,768-token context**, a 2,000-token rendered
prompt and a 2,048-token answer reserve, the generation allowance is 30,512 and
the thinking allowance is 28,400. This is an illustration, not a measurement of
a specific user's request. A bigger input leaves less room for generation. The
server rejects an input that cannot fit without the required answer space; it
does not silently cut the current question or selected evidence.

When the runtime ends with `finish_reason=length`, Venture keeps the visible
answer and continues it in the same durable job. Later passes write the answer
without starting another private thinking phase. An empty, length-limited first
pass gets at most one direct-response recovery. A normal completed response,
including a refusal, is not retried. Filter results, errors and disconnected
streams are not treated as permission to retry or bypass a stop.

All visible text is saved while it arrives and at pass boundaries, under the
same account and conversation. Private reasoning text is neither saved nor
exposed. Total token usage is aggregated over passes. Long-response progress
shows work/saved passes rather than a fabricated whole-job percentage or ETA.

If the previous visible answer becomes too large to include in the next context,
Venture uses its ending and labels the earlier sections as omitted from that
continuation's context. The original request, instructions and source evidence
remain present. Earlier generated sections stay saved in the conversation.
This is not an infinite context or exact hidden-state continuation: coherence
across a very large document still needs review. If even a short ending cannot
fit alongside the original evidence, the partial answer is kept with a clear
context message. Exact repeated large sections are removed and the loop stops.

## Time, memory and retention limits

The 27B profile reserves at least 10 GiB system RAM (or the existing higher
reserve), stops over 30 GiB process RSS, and retains the **five-hour processing
limit** for each accepted run, excluding time waiting in the FIFO queue. Every
continuation shares that same job deadline and cancellation callback; it does
not reset a five-hour timer on every pass. Long-response mode is additionally
bounded to **64 generation passes** and **1,000,000 visible characters**. It does
not run forever, promise a useful answer to every request, or increase RAM
allocations automatically.

Browser navigation, Safari suspension and client polling timeouts do not cancel
accepted work; the server owns it. The local stream/tokenization read deadline
allows slow CPU processing. Clicking Stop, memory pressure, a runtime failure,
or reaching a genuine job/retention limit can end work. Saved visible partial
output is retained and the reason is reported. Server/Windows restarts still
interrupt inference; saved text is retained but no hidden-state job is silently
replayed after restart. These protections and short benchmarks do not certify
livestream coexistence.

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

Context options are 4,096 and 8,192, with 16,384, 32,768 and 65,536 unlocked only
by successful explicit tests. The upstream 262,144-token architectural limit
is not offered as an untested 48 GB PC setting. Hardware profiles are server-
resolved, never client paths or arbitrary command arguments. Each queued job
snapshots its effective profile. Temperature, top P, top K, min P, repeat/presence
penalties and seed map directly to runtime parameters.

## Capabilities and verification boundary

Ultra uses Venture's existing authenticated file preparation, board retrieval,
history and memory. Image/frame analysis uses the **existing separate visual
worker**, running before the 27B answering model. Its original model/projector
paths remain anchored; Ultra does not download a second projector or claim native
27B pixel processing. Vision is advertised only after this backend confirms
the integrated path. Web search, code execution and image generation remain
unavailable; code writing/review and reading supported files work as before.

Single-pass timing calibration remains separate from long-response profiles;
multi-pass jobs are not admitted as single-prompt throughput samples. The number
of long-response passes is not known in advance, so the UI shows saved work and
an indeterminate indicator rather than counting down to a guessed finish time.

Offline tests exercise parameter rejection, profile pinning, native-budget
forwarding, context accounting, retained partial answers, automatic continuation,
bounded empty recovery, refusal/filter stops, cancellation, duplicate detection,
account isolation, larger-context gating and old-server UI compatibility.
Windows CI parses the PowerShell script; it does not install large model weights.

The real 27B model, its Windows driver, native thinking-boundary behavior and
actual long-run CPU/GPU throughput require on-device validation. Test with benign
requests, such as a long code explanation and a multi-part mathematical problem,
before relying on it for a lengthy job. Neither successful fixtures nor a longer
budget certify answer accuracy, complete source coverage, or guaranteed completion.


### Detailed Qwen activity and public explanations

The activity panel now separates three real sources of information:

- **Processor events:** the file preparation, selected FUPCJ/Mac runtime, prompt
  count, Deep-stage transitions, answer streaming, and saving milestones as each
  actually occurs. Each saved event includes seconds since processing began.
- **Model-written explanations:** optional `<progress_summary>` notes from Qwen.
  When the thinking channel is enabled, Qwen is asked to describe evidence under
  inspection, assumption checks, alternative approaches and unresolved limitations
  at meaningful milestones. Each note may contain **3–120 words**, up to 1,600
  characters. The latest model-authored explanation is kept visible instead of
  being displaced by a generic server event. Compliant notes are not guaranteed.
- **Non-content telemetry:** genuine prompt/context token counts, selected
  compute route, Deep stage, processing duration, and number of native reasoning
  stream **characters** observed over the observed stream duration. These are
  not tokenizer counts of hidden reasoning and do not indicate correctness.

Raw private reasoning text remains discarded. Deep draft and critique answers
also remain ephemeral, and are not written into the visible conversation. Only
explicit public notes, stage events and the numbers above are saved. Qwen notes
describe high-level activity; they are not a verbatim or guaranteed accurate
record of internal reasoning and cannot certify that a claimed check succeeded.

The activity timeline shows model notes and processor events as distinct labeled
entries rather than a nested series of collapsed steps. It opens expanded by
default on mobile and desktop, can be collapsed manually, and remains readable
after stopping, completion or a page reload. Accepted-run polling preserves an
existing user's expansion choices. History remains bounded to the latest 128
entries per run. If Qwen produces no valid public note, Venture says so rather
than manufacturing one. The existing stream is used; no extra inference or cloud
summarization request is started. The model may spend some generation tokens on
its public updates.

The GitHub Pages build publishes updated UI after the code is merged to
`main`. The actual Windows processor must be updated separately using
`Update-Venture.ps1` (or the installed setup updater) when no inference is
running. Refresh the browser after deployment. Runs started on older processors
continue to display their old, shorter saved notes.

## Deep reasoning

New Ultra requests now default to **Deep**: draft, critique, then final answer.
Standard remains available in Reasoning mode. See [Deep reasoning](qwen-deep-reasoning.md)
for the bounded budgets, update instructions and accuracy limitations.
