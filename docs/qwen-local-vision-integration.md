# Combined Qwen Local integration review

Status: implementation for review; **not deployed, activated, or accepted for default migration**.
The installed FUPCJ revision remains `4061c973dc2a738941778100b082ab4f09c97b93`.
This change is based on current main `cf723d7`, preserving the later Vortex fixes.
No installer, model download, service restart, or production account operation was run.

## User-facing behavior

There is one model, **Qwen Local**, retaining `qwen3.8-local`. After the running
backend confirms a successful integrated pixel-to-answer request, its subtitle
is **9B Distill + Vision**. Before confirmation the subtitle says
**9B Distill · vision not ready**. The server capability, not a frontend feature
flag or an installation receipt, controls image availability advertising.
Confirmation expires on backend restart, installation/profile changes, or a
failed inference. The updated, account-scoped disclosure explains both model
roles, sequential operation, source/crop/frame coverage, server privacy, possible
visual mistakes, and the absence of an automatic cloud fallback. Existing v1
acknowledgements do not bypass the combined-model notice.

## Request flow and evidence

The existing authenticated `project_runs` queue owns the whole operation:

1. Revalidate conversation/account ownership and the accepted board revision.
   Prepare existing local extracted text and retrieval evidence.
2. Select image sources or relevant frames. For retained original video, seek to
   timestamps in the question, or inspect three bounded overview frames. Record
   actual decoded presentation time separately from the requested time. Retained
   board-frame labels are preserved and explicitly marked as labels, not newly
   verified decoded times. A Vortex acquisition proxy is not claimed to be the
   platform's original stream.
3. Consult the conversation's durable evidence cache. Coverage requires the exact
   question/history/task digest, source bytes, selected view/crop, reference and
   pipeline/model identity. Changed questions or pixels cause focused reinspection;
   a new backend lifetime forces a fresh pixel request. An unavailable source is
   not silently substituted with an old finding.
4. The pinned Qwen3.5-0.8B and matching F16 projector inspect sanitized pixels.
   Readable-text questions can include four labeled detail crops in addition to
   the overview. Findings, uncertainties, hashes, crop boxes, references and
   timestamps are saved with the run.
5. Stop and reap the visual runtime before starting the 9B answering runtime.
   The 9B receives the findings as untrusted, source-linked evidence alongside
   extracted text. It must distinguish observed evidence from inference and
   cannot claim uninspected coverage. Token counting still uses its deployed
   template/tokenizer. Oversized evidence fails explicitly, without dropping
   source units or falling back to a paid model.
6. The response includes **Inspected images and frames**. Opening a view uses the
   authenticated server, validates its stored hash, and returns a metadata-free
   JPEG with `no-store`. Cross-account access is rejected.

This release uses answer-time inspection. It does not add a separate always-on
background visual daemon. Existing context indexing remains durable and local.
Visual findings and inspected views live beneath the account's conversation
root, so the existing conversation/account removal flow owns their cleanup.
Restart recovery preserves completed evidence, marks interrupted answering as
interrupted, and retains queued requests. Retrying is explicit; no generation is
silently replayed and no cloud fallback is introduced.

## Limits and installation

Both model stages use **CPU-only** when the combined profile is configured. The
visual worker uses four threads, one slot, 4,096 context tokens, at most 1,024
image tokens, a 600-second process deadline and a 4 GiB resident-memory ceiling.
The answering model uses the accepted main profile with GPU layers forced to
zero. The shared OS inference lock spans preparation, both processes and saved
terminal state. Windows kill-on-close jobs, below-normal priority, loopback-only
binding, per-process API credentials and proxy bypass remain in effect.

- At most six selected sources and eight inspected views per request. Omitted
  coverage is disclosed. Visual questions are capped at 1,600 characters.
- Images: at most 16 MiB / 32 megapixels, resized to 1,600 pixels per side; each
  sanitized JPEG is at most 2 MiB. Small-text crops preserve their coordinates.
- Video: at most two hours; local-only demuxer/protocol allowlists, two decoding
  threads, bounded FFmpeg logs/output, 90-second focused preparation calls and
  a 2 GiB process-tree memory cap. Question timestamps outside the source fail.
- At most 128 cached findings per conversation. Cache entries contain observations,
  not model state. Saved view files are retained with the conversation.
- The existing system RAM reserve is preserved (minimum 8 GiB, initial 10 GiB).
  Both runtime stages record peak RSS, minimum available RAM and CPU seconds;
  visual metrics also include cache counts, preparation time and evidence bytes.
  These are measurements, not a certification of safe OBS coexistence.

The reviewed visual assets are reused at their existing paths. The manifest is
`local-ai/visual/visual-worker-manifest.json`, with optional explicit
`localAI.visualManifestPath`. Exact sizes, model/projector SHA-256, repository
revision and the shared runtime inventory are checked before loading. Nothing
rewrites or redownloads existing weights.

`localAI.visualEnabled` must be explicitly set to `true` in a later authorized
integration session. This worker did not set it, enable `localAI.enabled`, change
`defaultEpoch`, or restart a service. Ordinary code updates include the new
modules but do not enable the feature. Existing `localAI.enabled` remains the
separate answering switch. A first authorized image request can exercise the
path while capability is still awaiting backend confirmation.

The maintained `Local-AI.ps1` and byte-identical `.txt` now use
`[System.Management.Automation.Language.NullString]::Value` for the atomic JSON
replacement backup argument. This fixes Windows PowerShell 5.1's conversion of
`$null` to an empty backup filename. The writer cleans staging files on failure,
propagates the error, and retains existing checksum/stop-on-failure behavior.
Tests exercise new files, repeated replacements, nested/Unicode data, and a
locked destination that must remain unchanged. No update was run on FUPCJ.

## Acceptance boundary

Runtime availability and default-migration eligibility are different gates.
Merely passing an image request, or the existing red/blue smoke test, never makes
Qwen eligible for default migration. The backend requires a separately reviewed
acceptance receipt matching the code hashes, manifests and exact CPU profile.
The receipt must include evidence for **every** item below, with observed
resource measurements above the configured memory reserve. Automatic migration
also still requires the explicitly configured default epoch. `EnableDefault`
continues to refuse activation in this release; an authorized rollout is separate.

In a later authorized session, a read-only template can be made with:

```powershell
& 'C:\ProgramData\VisionPC\runtime\python.exe' `
  'C:\ProgramData\VisionPC\local_acceptance.py' `
  --config 'C:\ProgramData\VisionPC\config.json' `
  --output 'C:\ProgramData\VisionPC\local-ai\combined-acceptance.json'
```

This only writes a new incomplete receipt; it never loads models or changes
configuration. It refuses to overwrite an existing report. All checks and
`approvedForDefault` start false. Point `localAI.acceptancePath` at a completed,
reviewed report only during a separately authorized rollout.

| Required real-host check | Evidence required before marking passed |
| --- | --- |
| Real photo | Accurate, source-linked observations on a non-synthetic photo, plus uncertainty where warranted. |
| Screenshot | Correct UI details from the actual screenshot; inspect the saved view. |
| Readable text | Verify identifiers, decimals, negation and small labels against the pixels; no guessed unreadable text. |
| Video timestamps | Compare the inspected frame, decoded timestamp and requested timestamp; distinguish existing frame labels. |
| Question reinspection | A second question about a previously unmentioned detail causes a new visual call; identical covered requests can reuse evidence. |
| Restart recovery | In an authorized test environment, interrupted visual/answer work is marked interrupted, pending work survives, and saved views remain accessible. |
| Account isolation | Another account cannot access the run, source view, cache or retained asset; changed/deleted ownership fails. |
| Cancellation | Cancel during decoding, visual loading/generation and 9B generation; verify owned children stop and the next queued request can start. |
| Resource limits | Observe peak RSS, available RAM, CPU use, temperatures, queue latency, time limits and lock exclusion for both stages; no two llama processes overlap. |
| Livestream coexistence | Obtain an authorized OBS workload window and agreed pass thresholds; measure dropped/render/encoding frames and stream stability. A quiet-window benchmark is insufficient. |

The supplied hardware reports establish verified installed model artifacts,
40.82-second CPU and 69.762-second 16-GPU-layer main-model smoke benchmarks,
red/blue visual ingestion, and local RAG/embedding checks. They do **not** satisfy
this acceptance table. None of these supplied measurements was rerun here.

## Automated validation and limits

The Python protocol tests use real signed Firebase-style test identities,
SQLite queues, retained files, decoded photo/screenshot pixels, real FFmpeg
seeking and production resource-stop logic. Model responses are explicit test
fixtures. They verify integration behavior, not Qwen perception quality or
production network/hardware performance. The image fixture comes from Pillow's
test suite; no user photo is committed.

The Windows CI job is configured to run the production JSON writer under
PowerShell 5.1. Browser tests are provided for responsive identity/disclosure,
capability gating and source view rendering. See [validation status](qwen-local-validation.md)
for what was actually run; Windows and browser verification are still pending.
Real FUPCJ end-to-end quality and OBS acceptance remain pending. This change must
stay unmerged and unactivated until the user authorizes subsequent work.
