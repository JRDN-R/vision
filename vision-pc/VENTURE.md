# Vision Venture

Venture replaces the signed-in Run view with an independent conversation workspace.
It shares the hosted and portable application, existing Firebase sign-in, encrypted
API credential storage, document/media tools and durable Responses worker. Opening
a conversation does not replace the board. **Include board** deliberately adds the
current board to a request; it is off initially.

## Activate on the existing FUPCJ computer

Back up `C:\ProgramData\VisionPC\data` before updating, including the SQLite
files. Run the existing updater in **Windows PowerShell as Administrator**, outside
an important active job:

```powershell
& "$env:ProgramData\VisionPC\Setup-Vision-PC.ps1" -Action Update
```

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
stop and browser dictation where the browser supports it. Dictation is not a
full-duplex voice call.

## Storage and manual recovery

Normal server metadata remains in SQLite. Venture additionally writes readable
recovery metadata and actual files under the configured data directory:

```text
data/
  users/
    u-<stable-user-id-hash>/
      identity.json
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
32-megapixel image inspection, up to 120 video frames and two hours of media.
Existing document extractor limits still apply. These are practical safeguards,
not a claim to understand every file format perfectly. Password-protected,
damaged, proprietary and oversized inputs can need conversion or a smaller file.

Normal continuation uses the saved upstream response chain. After an expired
response, local rehydration is bounded to 100 prior responses and 64 original
file references, with preparation/history limits reported to the model. Saved
history and downloads remain available even when not all can fit in a new prompt.
No content is silently summarized as if the whole archive had been read.

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

## Verification

```sh
python -m unittest discover -s vision-pc -p 'test_*.py' -v
python web/build.py
for test in tests/*.test.cjs; do node "$test"; done
python tests/run-local-smoke.py
VISION_TEST_FILE=1 python tests/run-local-smoke.py
python tests/venture-smoke.py
```

`test_venture.py` covers ownership, stable IDs, board separation, title preservation,
readable recovery, retained files, retries, funding calibration/ledger idempotency,
account separation and unsafe ZIP paths. The browser fixture exercises actual
Venture UI code at 390/1280px, settings timers, account reset, retained downloads
and sandbox isolation. These tests use stubs instead of billable OpenAI requests.
Live sign-in, Windows DPAPI, installed optional converters and a real generated
file still require an end-to-end check on the owner's FUPCJ computer.
