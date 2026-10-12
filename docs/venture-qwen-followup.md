# Venture image display, timing and default rollout

The owner reported a successful combined image answer on 2026-10-12. The UI
showed `9B Distill + Vision` and a 141-second processing time. That demonstrates
one completed visual request. It is not a thread benchmark or the complete
hardware/livestream acceptance record.

## Changes

- Images appear as square, center-cropped thumbnails above the user's message.
  Multiple images scroll horizontally. Originals are retained without cropping.
- The image viewer centers the uncropped preview over a blurred background,
  supports zoom buttons, wheel, pinch, dragging, keyboard zoom, Escape, outside
  click and an X. The original download remains available.
- Preview requests use existing account-authorized attachment routes. JPEG
  previews have no public URL or persistent browser cache. Account changes abort
  requests, close the viewer and revoke object URLs. Thumbnail requests are lazy,
  limited to two concurrent requests and 48 cached images. Server preview work is
  limited to two concurrent decodes, 16 MiB source files and 32 megapixels.
- Loading has a subtle animated indicator, actual server phase, queue count,
  elapsed time and a remaining-time range after three comparable completed
  requests from the same account. Resource profile, attachment mix, board/history
  use, output budget and known prompt size must match. Failed/cancelled work does
  not calibrate the estimate. Estimates are approximate and never claim a
  completion percentage; overruns say they are taking longer. Reduced motion is
  honored. No private data from another account is used to calibrate timings.
- Completed answers have no separate timing card. A small info/chevron icon on
  the model subtitle line opens a compact, temporary popover. Outside click,
  Escape, scrolling, switching conversation or signing out dismisses it without
  moving the transcript. Active answers keep their animated loading display.
- Restored uncertain submissions offer a new-conversation action. Checking status
  searches saved runs by the original request ID, including runs omitted from the
  conversation page. Empty results never silently clear or replay pending work.

## All-user default

The owner has requested Qwen as the default for all users. `EnableDefault` now
performs the maintained, gated configuration operation instead of always throwing
an obsolete release-blocked error. It requires the complete reviewed acceptance
receipt matching the installed code and resource profile, enabled answering and
vision, and a fresh model/runtime integrity check. It preserves model files and
uses the corrected PowerShell 5.1 atomic JSON writer. Failure leaves the config
unchanged. It does not stop/restart a service or directly edit account records.

The already implemented versioned account migration applies once per account
after the configured epoch and running backend visual confirmation both qualify.
Users can explicitly choose another model afterward. No cloud fallback is added.

After completing the real-host checks in `docs/local-qwen-rag.md`, an administrator
can use the updated installed helper:

```powershell
& 'C:\ProgramData\VisionPC\Local-AI.ps1' -Action EnableDefault -AcceptancePath 'C:\ProgramData\VisionPC\local-ai\acceptance.json'
```

This command is not a substitute for filling the receipt with actual evidence.
Do not create passing fields or measurements from mock tests. The remaining real
photos, screenshots, video timestamps, question reinspection, restart, isolation,
cancellation, resource use and livestream checks must be documented. The worker
session did not have control of FUPCJ and did not activate these defaults.

## CPU speed comparison

The maintained initial profile is CPU, 8 threads, 4096 context tokens, with a RAM
reserve. The visual stage has its separate bounded CPU profile. The earlier
CPU/GPU comparison favored CPU for that one workload. More threads can help or
hurt; changing a memory limit alone does not accelerate generation.

The benchmark action now accepts a thread override without changing active
configuration. Run during an authorized quiet window, with no livestream load:

```powershell
& 'C:\ProgramData\VisionPC\Local-AI.ps1' -Action Benchmark -GpuLayers 0 -ContextTokens 4096 -Threads 8
& 'C:\ProgramData\VisionPC\Local-AI.ps1' -Action Benchmark -GpuLayers 0 -ContextTokens 4096 -Threads 12
& 'C:\ProgramData\VisionPC\Local-AI.ps1' -Action Benchmark -GpuLayers 0 -ContextTokens 4096 -Threads 16
```

Results have distinct `-threads-N.json` names. Compare repeated timings and then
test combined image/video requests and livestream coexistence. `Enable -Threads N`
requires the matching successful benchmark and exact profile. Selecting a new
resource profile invalidates the old acceptance identity; keep the shared lock
and RAM limits. No speed increase has been measured by this worker.
