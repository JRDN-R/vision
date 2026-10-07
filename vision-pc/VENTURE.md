# Venture

Venture replaces the signed-in Run view with an independent conversation workspace.
It shares the hosted and portable application, existing Firebase sign-in, encrypted
API credential storage, document/media tools and durable Responses worker. Opening
a conversation does not replace the board. **Include board** deliberately adds the
current board to a request; it is off initially.

## Activate on the existing FUPCJ computer

Back up `C:\ProgramData\VisionPC\data` before updating, including the SQLite
files. In **Windows PowerShell as Administrator**, outside an important active job:

```powershell
$VentureUpdate = Join-Path $env:TEMP 'Update-Venture.ps1'
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Update-Venture.ps1' -OutFile $VentureUpdate
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VentureUpdate
```

The wrapper first downloads the current updater, then pins all server downloads
to one GitHub commit. Do not rely on a pre-Venture copy of `Setup-Vision-PC.ps1`:
its file manifest may omit the new Venture modules. The existing updater still
handles staged activation, rollback and preservation of data/configuration.

Refresh the hosted page, or download and reopen the updated local HTML. The page
checks the server's `ventureV1` capability and shows an update message instead of
silently sending Venture requests to an old processor. Existing server setup,
Google identity, account credentials and saved board projects are preserved.
Optional document tools and local Whisper remain opt-in installs; see
[DOCUMENTS.md](DOCUMENTS.md) and [README.md](README.md). Venture itself downloads
no new language model and requires no additional paid title/embedding service.

Keep FUPCJ Server **awake, running and online**. A browser must stay open until an
upload is accepted. After acceptance, work is server-owned: closing the browser
does not cancel it. An outage of the FUPCJ computer is different from closing a
browser; it can interrupt preparation or prevent timely retention of upstream
files. A saved response ID is resumed rather than submitting a duplicate request.
An interrupted submission without an upstream ID is surfaced for review, never
blindly retried as another paid request.

## Using Venture

Choose **Venture** from Vision. New Venture creates a separate conversation. The
history drawer supports search, pagination, reopening and renaming through its
menu or touch press-and-hold. Initial titles are generated locally from the first
exchange using an extractive text heuristic; this is free of model/API charges,
not an installed language model. A manual title is preserved.

The header shows the model. Settings expose an exact model ID, mode, reasoning,
verbosity, output-token limit, web search and code/files. Code Interpreter is on
by default. Sliders use discrete API values. The popover closes after approximately
two idle seconds but not while a field is focused or a control is being dragged.
Unsupported model/parameter combinations fail visibly, not with a silent downgrade.
Settings are saved per conversation and snapshotted for every accepted request.

Copy, retry, source links, registered file downloads, safe previews and high-level
processing status appear with responses. Retry creates another retained response
branch instead of deleting the original. The composer supports attachments,
stop and Gemini-backed microphone dictation on supported HTTPS browsers. Dictation is not a
full-duplex voice call. See the dictation, profile and memory sections below.

## Storage and manual recovery

Normal server metadata remains in SQLite. Venture additionally writes readable
recovery metadata and actual files under the configured data directory:

```text
data/
  users/
    <sha256-of-authenticated-user-id>/
      user.json
      conversations/
        <conversation-id>/
          conversation.json
          messages/<run-id>.json
          uploads/<run-id>/<number>-<original-name>
          generated/<run-id>/<artifact-id>-<original-name>
```

The identity file identifies the authenticated account without storing its API
key. Original names/extensions remain recognizable and repeated filenames cannot
overwrite earlier responses. The service may use additional working/cache folders
under `data/projects`. Existing project conversations can appear in history without
moving or overwriting board data. Old artifacts are copied as their conversation
is accessed; this is not a destructive migration.

The machine owner can recover files from disk even if the UI is unavailable.
These folders are not Git repositories and are **not uploaded to GitHub or Google
Drive**. Every remote read/download checks the authenticated owner. Windows
administrators still have physical access to all stored users' data; this is not
end-to-end encryption against the server owner. Protect the PC and its backups.

The application does not configure a backup service or guarantee unlimited disk
space. Preserve both the database and user/project directories in backups. A
failed retention/copy is shown as a storage warning rather than a working-looking
link. Only registered artifacts resolve `sandbox:` links; assistant text cannot
select arbitrary server paths. HTML/SVG previews reuse the opaque sandbox preview
with scripts off by default.

## Attachments, retrieval and limits

Venture uses the existing offline tools rather than treating every binary as text:
PDF/Office extraction and page previews, spreadsheet evidence, image input,
local audio transcription and bounded video frames, text/code and safe ZIP
contents. Originals remain downloadable. Prepared evidence has source names,
locations and preparation notes. Missing optional tools or unsupported files are
reported explicitly. Uploaded programs/macros are never executed locally.

Long prepared text is indexed with local SQLite FTS5 and relevant passages are
selected for the prompt. This is **lexical full-text retrieval**, not a new paid
vector database or semantic embedding model. Prepared archives/original files
remain available to Code Interpreter where supported. The selected OpenAI model
still performs reasoning and code execution in OpenAI's environment.

Current bounds include 20 chat attachments, 128 MiB per upload, 100 MiB retained
per generated artifact, bounded ZIP expansion (2,000 entries/256 MiB/ratio 200),
32-megapixel image inspection, up to 120 video frames, two-hour videos and
eight-hour audio. Existing document extractor limits still apply. These are
practical safeguards, not a claim to understand every file format perfectly.
Password-protected, damaged, proprietary and oversized inputs can need conversion
or a smaller file.

Normal continuation uses the saved upstream response chain. An expired response
can be rebuilt from the current conversation’s retained text and files, subject
to model context and file-preparation limits. Saved history and downloads remain
available even when not all can fit in a new prompt. Memory-enabled chains use
fresh local reconstruction rather than replaying stale cross-conversation excerpts.

## Estimated funding meter

Click the account avatar to see a green/yellow/red bar with no dollar balance in
its normal display. **Add funding** opens OpenAI's organization billing page;
**Update balance** lets the user explicitly enter a current balance or an amount
added. It does not detect a purchase on the billing website automatically.

The estimate is scoped to the signed-in account and a fingerprint of its configured
API key. Actual returned token usage and a versioned server-side price catalog
produce an idempotent per-response ledger. Cached reads/cache writes are treated
as subsets of input; reasoning is already included in output and is not billed
again. Known web-search charges and observed code-container sessions are included.
A reconciliation step retries local accounting without resubmitting an AI request.
Changing keys requires a separate calibration. Identical top-up requests cannot
be applied twice after a lost connection.

This is **not the official OpenAI prepaid balance**, a project wallet, or a hard
spending limit. External usage, unobserved container activity, discounts, credits,
missing usage fields and unknown charge categories can cause drift. Unknown
pricing/partial coverage and overdue price review are shown explicitly. Only an
explicit credit-exhausted provider error forces the meter empty; general rate or
project-limit errors do not falsely mean that credits are gone. A balance reset
is deferred while relevant work/accounting is unsettled.

Prices are centrally maintained in `venture-pricing.json`, with a review date and
source URLs. Update them through a reviewed server update when OpenAI changes
prices. No billing scrape, browser session token, Admin API key, or automatic
bank/payment action is used. Calibration amounts and keys are excluded from
portable application downloads. Users should confirm actual funds on OpenAI's
billing dashboard before relying on an estimate.

Sources checked for the initial catalog on 2026-10-06:
- https://developers.openai.com/api/docs/pricing
- https://developers.openai.com/api/docs/models/gpt-6-astra
- https://developers.openai.com/api/docs/models/gpt-6.1-sol
- https://developers.openai.com/api/docs/models/gpt-6-luna
- https://developers.openai.com/api/docs/guides/reasoning
- https://developers.openai.com/api/docs/guides/prompt-caching

## Hosted dictation: Gemini, text only, three minutes

Every **signed-in** Venture account can use the dedicated dictation route without
requesting project Gemini approval. The server uses its existing encrypted Gemini
credential and the dedicated `gemini-3.5-transcribe` speech model. This permission
does not approve, modify or bypass the existing Gemini gate for Vision projects.
The request accepts only a bounded audio recording and a unique request ID. Client
prompts, tools, model names, URLs and project IDs are rejected. Keys stay on the
server. This is shared server-funded usage, not a promise of free Gemini inference.

The hosted browser requests microphone permission, records with MediaRecorder,
and displays a waveform derived from actual microphone samples. Tap again to
stop. At **180 seconds** recording stops without a warning, beep or confirmation.
A hidden/backgrounded page stops recording too. FFmpeg independently trims accepted
audio to a maximum of 180 seconds before Gemini receives it, including recordings
with a forged client duration. WebM/Opus and Safari-style MP4/AAC are supported.

When recording ends, Gemini returns a plain transcript string into the existing
draft. It is **not SRT**, has no requested subtitle timestamps or diarization,
and is not automatically sent as a chat message. The UI has no Web Speech API or
other provider fallback. A browser still needs HTTPS, a supported recorder and
explicit microphone permission; automatic Gemini permission cannot grant OS or
browser microphone access. FFmpeg and the shared Gemini credential must be ready
on the updated FUPCJ server.

Up to 12 MiB is accepted per recording, with four simultaneous server transcriptions,
one active transcription per account, and 60 recordings per account per hour.
These safeguards do not change the silent three-minute stop. A lost connection
can recover the same request ID without another paid model submission. Ambiguous
provider failures are not blindly replayed.

Audio exists only in a private temporary server directory during transcription
and is removed afterward (also cleaned on startup after interruption). Unsent
transcript receipts are cleared after approximately 15 minutes by cleanup; ID/hash
and status metadata last up to a day for retry protection and throttling. The normal
Gemini usage ledger records usage, not the recording or transcript. Google’s own
processing/data policies still apply; `store:false` is used for the interaction.

## Profile pictures

Account menu → **Upload picture** accepts a photo. The browser center-crops and
compresses it before upload; the server validates, re-crops to 256×256, strips
metadata and re-encodes WebP. Only that small final image is retained. The image
belongs to the authenticated account, survives signing in elsewhere and does not
change the user’s Google account picture. **Use account picture** removes the
custom image and restores the Google image/initials fallback. Invalid, oversized
or non-raster uploads are rejected. The visible Venture logo uses the existing
Vision monocle-head SVG/image asset, not a replacement illustration.

## Optional memory across conversations

Settings → **Use past conversations** is off by default and can be enabled per
conversation. Each new prompt searches this account’s other nondeleted conversation
text, including conversations older than the first sidebar page. The complete
retained message text is indexed locally with SQLite FTS5; a bounded selection
(up to 10 excerpts / approximately 12,000 excerpt characters) supplies relevant
background. It does not send every entire thread on every request or use a shared
cross-user memory. Source titles appear with the response. Memory uses additional
OpenAI context tokens. Titles are reference labels, not instructions.

Only source IDs are attached to run metadata; selected source text is not duplicated
into a permanent standalone memory file. Searches enforce the authenticated owner
and the nondeleted state at query time. Once a response chain has used this
feature, continuation rebuilds from its own saved conversation and fresh retrieval
rather than reusing an upstream chain containing stale cross-chat excerpts.

Deleting a source excludes it from subsequent retrieval. This cannot retract an
already submitted provider request, erase facts already quoted in another saved
conversation or remove independent backups. Existing copied text remains part of
those other conversations until they too are deleted. This is retrieval, not
training, autonomous profiling or a guarantee that every old fact is remembered.

## Delete conversations

Open the conversation’s **…** menu (or press and hold on touch), then choose
**Delete conversation** and confirm. Stop an active response first. Deletion removes
that conversation from history and memory, deletes its stored messages, uploads,
generated files and indexes, and leaves a minimal tombstone to prevent an old tab
or migration from recreating it. Paid usage records are retained; deletion is not
a refund. Deleting a conversation originally attached to a board does not delete
the board project itself. Windows-locked files produce an explicit cleanup warning
and are retried after a server restart.

## Exact default file locations

With the standard installation, the data directory is:

```text
C:\ProgramData\VisionPC\data\
  vision.sqlite3
  users\<user-hash>\
    user.json
    profile\avatar.webp
    conversations\<conversation-id>\
      conversation.json
      messages\<run-id>.json
      uploads\<run-id>\<number>-<original-name>
      generated\<run-id>\<artifact-id>-<original-name>
```

`<user-hash>` is the full SHA-256 of the authenticated `firebase:<uid>` string;
`user.json` identifies the account for manual recovery. Some older working files
can also be under `data\projects\<conversation-id>`. Dictation audio is temporarily
under `data\temporary\venture-dictation-*`, not retained in a chat as an audio file.
A transcript becomes a saved chat message only after Send.

Account menu → **Where are my files saved?** asks the running server for the actual
configured conversation directory for that user, so it remains correct when a
custom data drive is configured. Its authenticated API also reports the database,
identity and avatar paths. These paths are not exposed for other accounts. This
repository update does not itself prove where a particular live installation is
configured until that server returns the path.

## Verification

```sh
python -m unittest discover -s vision-pc -p 'test_*.py' -v
python web/build.py
for test in tests/*.test.cjs; do node "$test"; done
python tests/run-local-smoke.py
VISION_TEST_FILE=1 python tests/run-local-smoke.py
python tests/venture-smoke.py
python tests/venture-integration.py
```

`test_venture.py` covers ownership, stable IDs, board separation, title preservation,
readable recovery, retained files, retries, funding calibration/ledger idempotency,
account separation and unsafe ZIP paths. The browser fixture exercises actual
Venture UI code at 390/1280px, settings timers, account reset, retained downloads
and sandbox isolation. These tests use stubs instead of billable OpenAI requests.
Live sign-in, Windows DPAPI, installed optional converters and a real generated
file still require an end-to-end check on the owner's FUPCJ computer.

`test_venture_features.py` adds signed-account checks for narrow Gemini permission,
plain-text/SRT handling, idempotent dictation receipts, actual FFmpeg duration caps
and WebM/MP4 decoding, profile processing, deletion and account-isolated memory.
`venture-integration.py` runs a real browser MediaRecorder/fake microphone into the
real Flask/FFmpeg/profile endpoints with a **mocked** Gemini HTTP response. It covers
waveform samples, silent three-minute shutdown, retained draft/no auto-send,
profile upload/reload, storage paths, settings and deletion. No real user's microphone,
production credential or billable model call is exercised by these tests.

Provider and browser references checked for this update (2026-10-06):
- https://ai.google.dev/gemini-api/docs/transcribe
- https://ai.google.dev/gemini-api/docs/interactions
- https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia
- https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder
