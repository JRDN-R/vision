# Vision

An advanced prompt generator: arrange images, documents, notes, and processed
media into a board, connect the modules, and export the instructions and evidence.

[Open Vision](https://jrdn-r.github.io/vision/), or download the portable HTML from
the app. Google sign-in is required to use the application. Video imports, YouTube
imports, account saves, and OpenAI conversations use the connected **FUPCJ Server**.

## Vision Venture (1.0.0.15)

**Venture** is the new account-owned conversation workspace: a near-black interface
with Vision green, a history drawer, automatic local titles and renaming, persistent
files, copy/retry actions, model/settings controls and an estimated funding meter.
The board stays separate. Accepted jobs continue on FUPCJ Server after the browser
closes, and files are retained in per-user/per-conversation recovery folders.

Update the existing Windows processor before opening Venture. See
[activation, storage, funding estimates and verification](vision-pc/VENTURE.md).
The meter needs manual calibration; it does not read OpenAI's prepaid balance.
Document/media preparation reuses installed local tools, and automatic titles use
a free local text heuristic rather than downloading another language model.

## Projects and conversations

The connected Windows FUPCJ Server can save projects and run the OpenAI Responses
connection independently of a browser tab. A project keeps its own connection
identity, conversation, and file references. Reopen that project to retrieve
progress or completed results. OpenAI still performs the model processing and
code-interpreter execution; FUPCJ Server handles the connection, saved work, and files.

Sign in with Google on the hosted page, then use **Projects → My saved projects**
to open the same work on your phone or computer. Firebase handles identity;
projects, files, and processing stay on FUPCJ Server. Enable Google in Firebase
Authentication, authorize `jrdn-r.github.io`, and run the one-time
[`EnableGoogleSignIn` server update](vision-pc/README.md#enable-google-sign-in-with-projects-stored-on-this-fupcj-server).
No Firebase database, Storage bucket, service-account key, or Blaze upgrade is
needed for this setup.

Run automatically saves an entered OpenAI API key to the signed-in account,
encrypted on FUPCJ Server with Windows DPAPI. The same account restores it on another
device. **Save key** retries a failed save; **Remove saved key** deletes that
account's saved credential. Keys are excluded from projects and app downloads,
and the field clears when signing out or changing accounts.

Existing projects can be imported from a file or this device's previous projects,
then explicitly added to the Google account. Account projects are private to that
account. Conflicting edits from two devices are shown for review rather than
silently overwriting the server copy. Download an editable project for an additional
copy. The downloaded HTML signs in locally using the bundled Firebase SDK. Google authorization uses a separate secure popup; the workspace does not navigate to the hosted app.

The server desktop's **Vision Logs** folder contains **Users.txt** and one usage
log per Google account. It records names, Google email addresses, activity times,
request sizes, and processing outcomes without passwords, API keys, or content.
Processing durations are elapsed time, not a measurement of CPU utilization.

Run offers a continuing conversation, attachments, copy controls, and one current
progress line. Dictation appears when the browser supports speech recognition;
the transcribed text stays editable before sending. Completion appears on the
board while Vision is open. Optional system notifications depend on browser
support and permission.

Update an existing FUPCJ Server installation using the instructions in
[vision-pc/README.md](vision-pc/README.md). Keep FUPCJ Server awake and online. Files and
project history are stored on FUPCJ Server; Google Drive backup is not configured by
the app.

Board background controls save with each project: slow diagonal dots, sparse
twinkling stars, or still dots, in Sage, Dusty rose, or Redshift mix. Reduced-motion
preferences stop the animation. Use the compact undo/redo controls to step back
through board changes.

## Board inputs and controls

**Add to board** accepts images, videos, audio, documents, and clipboard content.
Pasted text becomes an editable text module. Drop files onto empty board space
to create one module per file, or onto an existing module to attach them there.
File modules keep the original filename and show their type, with a text preview
where available or a document icon. Imported audio starts transcription and puts
the completed transcript into its module's text field.

The board microphone records a voice note and lets you choose FUPCJ Server Whisper
or Gemini for new recordings and audio imports. Stop to create its module;
cancel to discard the recording. Browser microphone permission is required.
Recording stops if the page goes into the background. This board recorder is
separate from the browser dictation button in Run.

Drag either end of a wire to another module to reconnect it. Releasing over empty
space restores the original connection. New branching paths are unconditional;
enable **Use an IF condition** in Paths when needed. Tapping an IF label turns the
condition off while keeping its text available to enable again.

Use the left toolbar's Undo/Redo, `Ctrl/Cmd+Z`, or `Ctrl/Cmd+Shift+Z`. With the
board or a module focused, `Tab` selects the next module in sequence and
`Shift+Tab` selects the previous one. Text fields retain normal editing keys.
Run settings have a Show/Hide API key control, and each response's **Files**
button expands its downloads when needed.

Video files added as new modules use FUPCJ Server to create timestamped screenshots,
audio sections for the selected transcription provider, and a 480-pixel, 15 fps
playable preview with mono audio. A poster and current processing step appear
on the module. Accepted video jobs continue on FUPCJ Server after the browser closes;
reopen the saved project to retrieve the result. Playback needs FUPCJ Server connection.
The original upload is removed after processing; the compact preview remains
on FUPCJ Server and is not embedded in project downloads. Initial uploads support
5 GB and up to two hours; update FUPCJ Server processor before using this feature.
Videos attached inside an existing module retain the browser-processing path.
Use **Projects → FUPCJ Server video storage** to remove saved previews, including videos
whose modules you deleted. Removing a module alone keeps its server files available
for undo and older saved projects. Explicit server deletion preserves snapshots and
transcripts already saved in the board, but removes playback for those copies.

## Local speech transcription

The optional **FUPCJ Server** transcription provider runs an open-source English
Whisper model on the connected FUPCJ Server, using four CPU threads and one worker. It
produces timestamped text without Gemini or OpenAI transcription API charges.
FUPCJ Server must stay awake and online for remote use; electricity and storage still
apply. Local transcription does not silently fall back to a paid provider.

Install the model explicitly with `-Action InstallLocalTranscription` using the
[Server instructions](vision-pc/README.md#optional-local-transcription-without-api-charges).
Normal updates preserve an installed model and do not download one automatically.
The same instructions include disable/enable commands for church services.
Gemini transcription and OpenAI conversations remain separate online options;
their provider's billing terms apply when selected.

Gemini transcription now uses the same durable PC queue, with account approval
and private usage monitoring in [Vision Status](https://jrdn-r.github.io/vision-status/).
See [Gemini processing and the existing PC update](vision-pc/GEMINI.md) for activation,
bounded sound enhancement, pricing, and credential migration details.

## Export to ChatGPT

Prepare an AI package in Export. Supported devices can share the prepared ZIP
through their native share sheet. Available apps and ZIP support depend on the
device. Otherwise, download the ZIP, open ChatGPT, and attach it there. Vision
does not claim to attach a local file automatically to another website.

## Development

Editable application code and document structure are in `web/`. `Vision.html` also holds the bundled media
runtimes and artwork; `web/build.py` preserves those assets while replacing the
application code and styles. Run:

```sh
python web/build.py
```

The builder updates the portable HTML and the small GitHub Pages loader's cache
version. Both use the same application. Server service source and tests are in
`vision-pc/`. Tests use mocked OpenAI responses and do not make billable API calls.

## Background document preparation

The new Standard document installer prepares PDFs, Office files, spreadsheet data,
image OCR and common text/archive formats on FUPCJ Server while you build a board.
See [installation, storage and format coverage](vision-pc/DOCUMENTS.md). The larger Docling profile is optional.

## Run settings and local application (1.0.0.14)

Run → Settings accepts an exact OpenAI API model ID, reasoning mode (model default, standard, pro), thinking effort (model default through max), verbosity, web search, and code/files toggles. Settings are saved with the project and each accepted run. Unsupported combinations fail visibly; they are never silently retried with different settings. Model default omits that API parameter. Current GPT-5.6/GPT-6 mode and effort behavior follows the [OpenAI reasoning documentation](https://developers.openai.com/api/docs/guides/reasoning). Output limits include reasoning tokens. API billing is separate from a ChatGPT subscription.

The PC requires this version of `sessions.py` and `server.py`. On the existing FUPCJ Server, open Windows PowerShell as Administrator and run:

```powershell
& "$env:ProgramData\VisionPC\Setup-Vision-PC.ps1" -Action Update
```

The existing updater stages files and preserves projects, account credentials, configuration and installed models. The page checks `runParametersV1` before submitting non-default settings to an older PC, so they cannot be silently ignored. Boards/attachments require Code & files; turn off Include board for a text-only request without that tool.

### File workspace

Generated files remain on the authorized project's PC storage. Run's File bag and inline sandbox links open previews in the same page and offer a real download. Only registered files from that response can resolve an inline file link; ambiguous or fabricated paths do not become arbitrary network or filesystem requests. Text and common images preview directly. HTML/SVG previews use an opaque sandbox frame; scripts are off initially and can be enabled explicitly inside the restricted frame. Office/PDF/ZIP and other binary formats are downloadable rather than rendered as untrusted HTML. Web-search citations appear as clickable Sources.

### One-file local app

Download Vision saves `Vision-Local.html`. It includes the UI, CSS, images, media runtimes and Firebase app/auth runtime (`web/vendor/firebase.js`, Firebase 12.19.0, bundled with esbuild 0.25.10). No sibling files, local server or client-side installation is needed. Opening and rendering the interface does not fetch GitHub JavaScript or styles. Authentication, Google/YouTube services, AI, remote PC work and synchronization still need internet. A first sign-in is not an offline operation. Google and the Drive picker use the hosted `local-signin.html` service with a nonce-bound MessageChannel, while the workspace stays local. Email/password works directly in the local app; use a Vision account password, not a Google password.

Local updates check the fixed repository manifest on startup, reconnect and every six hours while open. Downloads are SHA-256/size verified before use. In browsers supporting file access permission, choose the current HTML and enable automatic file updates for that session. Otherwise download and replace the HTML manually. No unauthorized filesystem writes or automatic workspace reloads occur. Reopen the updated HTML after saving to run its new code. Project/cloud synchronization is separate and remains automatic. Downloading the application never packages an API key, login token, password or the current board. Save project exports the board separately.

### Verification

`node tests/run-local.test.cjs` covers settings normalization and registered-file routing. `python tests/run-local-smoke.py` uses Playwright with network stubs to exercise bundled startup, settings, previews, downloads, and sandbox isolation at 390px/1280px widths; `VISION_TEST_FILE=1` checks the literal local file URL. `RunParameterTests` in `vision-pc/test_sessions.py` checks persisted options, outbound Responses fields, opt-out behavior, invalid settings and source citations, with no paid API requests. Live sign-in and the installed Windows PC still require an end-to-end check in the owner's environment.
