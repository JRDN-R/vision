# Qwen Local worker validation

Implemented on local branch `codex/qwen-local-vision-integration` from main
`cf723d7`. The initial integration commit is `756cc4c`; the branch also includes
follow-up checks and fixes recorded in its history.

The implementation and operational boundaries are described in
[qwen-local-vision-integration.md](qwen-local-vision-integration.md).

## Implemented

- One Qwen Local entry retaining `qwen3.8-local`; combined subtitle and image
  capability depend on confirmation from the current backend.
- Account-authorized source selection, question-specific pixel/crop/video-frame
  inspection, exact-question evidence reuse, and source-linked findings for 9B.
- Sequential visual and answering runtimes under the shared inference lock,
  accepted CPU-only profile, durable run state, cancellation and restart handling.
- Authenticated inspected-view links, account-isolated cache/receipts and explicit
  coverage limits. No cloud fallback.
- Combined first-use disclosure, version-bound default-eligibility gates, and the
  PowerShell 5.1 atomic JSON-save correction in both maintained helper copies.

## Local checks

- Full Python server regression suite: **477 tests run, 471 passed, 6 skipped**.
  This covers the final application-code changes. The skips concern optional
  external tools/platform-specific fixtures; they are not recorded as passes.
- Final focused local-provider suite: **31 tests passed**, including one additional
  queued combined-request recovery test added after the full suite above.
- Browser-independent JavaScript suite: **61 tests passed**.
- Portable application build, version metadata check, Python compilation, and
  `git diff --check`: passed.
- Real decoded JPEG photo and generated screenshot pixels traverse the production
  preparation/authorization flow. Model observations and answering output are
  explicit protocol fixtures, not real model quality results.
- Real FFmpeg video test: a request at **1.05 s** resolves to a frame with verified
  decoded presentation time **1.10 s**, retained separately from the request.
- Tests exercise cache hit vs changed-question/changed-pixel reinspection,
  cancellation before answer generation, cross-account view denial, ownership
  changes, interrupted and queued restart recovery, memory-reserve stopping,
  source links, capability invalidation, and default-migration refusal.

## Unresolved verification

- Real combined Qwen responses on FUPCJ photos, screenshots, readable text and
  videos, including question-specific follow-ups.
- FUPCJ Windows process shutdown/restart behavior, combined runtime/RAM/CPU/latency
  measurements and an authorized livestream-coexistence acceptance session.
- The new PowerShell 5.1 writer regression tests are committed but have not run in
  this Linux environment. They are configured in the Windows CI workflow.
- Responsive Chromium tests are committed but have not run. Both available
  Playwright distributions received invalid/incomplete Chromium archives here.
- GitHub CI has not run for this branch: automatic approval review rejected the
  push because external source publication requires explicit user authorization,
  including after the connected repository identity and permissions were checked.
  No remote branch or draft pull request was created.

## Production state

No model installation was repeated. No verified model file was replaced.
No merge, deploy, FUPCJ restart, answering activation or account-default change
was performed. Backend availability and the broader hardware/quality acceptance
remain separate; the supplied red/blue smoke test does not authorize migration.
