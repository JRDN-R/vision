# Gemini processing and owner monitoring

Gemini is a provider in the existing PC transcription queue. The Whisper-only
provider, installed local sound detector, Google sign-in, and project receipts
are reused.

## Activate the update

On the existing Vision PC, open PowerShell as Administrator and run:

```powershell
$VisionInstaller = Join-Path $env:TEMP 'Setup-Vision-PC.ps1'
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Setup-Vision-PC.ps1' -OutFile $VisionInstaller
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VisionInstaller -Action Update
```

The update retains the existing Firebase configuration, installed models,
projects, owner allowlist, and API setup. It migrates the previously configured
Gemini credential into protected PC storage. The SYSTEM service performs the
one-time Windows DPAPI import, then deletes the staged legacy source. The new
browser build contains no Gemini credential or direct Gemini API client.

Refresh Vision and [Vision Status](https://jrdn-r.github.io/vision-status/) after
the PC update. GitHub Pages publication alone cannot update the installed PC
service. Existing `activity-admins.json` permissions still identify the owner;
no account receives Gemini access automatically.

## Approval and queue behavior

A Google user may submit Gemini work before approval. The accepted receipt is
stored as `approval_waiting`, displayed as **Waiting for Gemini approval**, and
automatically creates a pending account access request. It makes no Gemini calls.
Approval in Vision Status releases that account's waiting receipts in place.
Future jobs use the saved account permission. Denial or revocation prevents
subsequent requests; a request already sent before revocation cannot be recalled.

The owner can Approve, Deny, or Revoke access in the existing Status interface.
Authorization uses verified Firebase UIDs and the protected owner allowlist,
never display names, email matches, browser state, or the installation token.
Project results and ordinary queue responses do not expose usage or cost data.

## Processing

`gemini-3.5-transcribe` supplies word timestamps through the Interactions API;
the PC groups these into readable speech cues. When sound effects are enabled,
the existing local detector can run alongside speech transcription. Only after
local detection completes does `gemini-3.8-flash` describe detected audio clips.
Sound requests use low thinking, a 512-token output limit, and structured JSON.
False positives are discarded. Meaningful sound descriptions use
`**description**` in chronological TXT/SRT output.

Sound clips include about 1.5 seconds of surrounding context where available and
are capped at 20 seconds. Continuous events use representative short excerpts.
The Flash sound path never sends the entire recording. Completed and rejected
request results are checkpointed. A recorded request with an indeterminate result
is not silently sent again after restart. No failed Gemini request falls back to
a different paid model or switches the selected provider.

## Usage and pricing

Vision Status shows each sound/speech request, account, project/job, event timing,
audio duration, API status, reported input/output/thought/total token measurements,
and estimates. Aggregates cover jobs, accounts, and UTC days. Unavailable usage
or rates display as unavailable rather than a zero-dollar charge.

The protected `DATA_DIR/gemini-pricing.json` is initialized from the versioned
default file only when absent. The owner may update it through
`PUT /api/admin/gemini/pricing` using the existing Google authorization. Raw usage
is retained, so current estimates can be recalculated without rewriting request
records. Each request also retains its historical pricing version/rate snapshot.
Published paid standard rates are estimates, not invoices or free-tier accounting.

Model IDs can be adjusted centrally under the existing config's optional
`geminiProcessing.speechModel` and `geminiProcessing.soundModel`; no separate API
credential configuration is needed. Defaults are the requested models.

## Security and validation limits

Earlier public Vision builds embedded the reused Gemini key. Removing it from
new builds and enforcing PC authorization does not revoke copies from old files
or Git history. Rotating/restricting that exposed key is required to eliminate
direct use outside Vision; this update deliberately does not change the key.

Automated tests use mocked Google responses and identities and synthetic audio.
Windows SYSTEM/DPAPI operation, the installed sound model, and live Gemini
transcription must be verified on the updated PC. No live paid API requests are
needed for the repository test suite.

Official API references: [Transcribe](https://ai.google.dev/gemini-api/docs/transcribe),
[Interactions](https://ai.google.dev/api/interactions-api),
[pricing](https://ai.google.dev/gemini-api/docs/pricing).
