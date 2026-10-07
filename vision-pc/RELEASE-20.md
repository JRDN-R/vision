# Vision / Venture 1.0.0.20

## Dictation

Venture dictation makes one Gemini transcription attempt. If it fails, the retained recording is sent to the PC's installed Whisper worker. PC attempt 1 starts immediately; attempt 2 starts 30 seconds after the first fails; attempt 3 starts 60 seconds after the second fails. A successful transcript is inserted once in the current draft, without sending it. There is no manual transcription retry button and no automatic second Gemini request.

A lost response is checked using its account-scoped receipt before falling back. Checks do not create new provider requests. Cancellation, account changes and conversation changes stop future browser retries and prevent stale text insertion. A server request already accepted can finish after browser cancellation; its result is not inserted into a different account or conversation.

After all attempts fail, a red message offers audio export. Supported recording browsers use M4A. Other recordings can be converted to actual MP3 using the app's bundled offline decoder, without the PC, a network request or provider credits. MP3 preparation is followed by a fresh Save click. The unchanged original recording remains available if conversion fails. Recordings are retained in the current tab, not backed up across closing or refreshing the page. A too-full draft retains a successful transcript for insertion when space becomes available, with an additional transcript download.

Whisper must already be installed and enabled on the PC. This release does not download model weights or change general Vision project Gemini permissions. The cost-saving model selector is visible, disabled and labeled **Coming soon**. It does not route prompts or spend tokens.

## Other v20 changes

The previously reviewed navigation icons, mobile top-region swipes and one-time account notice, conversation-wide Sources inventory, launch preference / `?view=venture`, persistent settings panel, and shared model-parameter profiles are included. The broader automatic model router and arbitrary image/video model handoffs are not implemented.

## Activate the PC half after publication

On the FUPCJ PC, use Windows PowerShell as Administrator. Download the updated installer first: an already-installed v19 installer has an older file list and will not fetch all new modules merely by running its old Update action.

```powershell
$update = Join-Path $env:TEMP 'Setup-Vision-PC-v20.ps1'
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Setup-Vision-PC.ps1' -OutFile $update
& $update -Action Update
```

The existing Update path stages the new files, preserves saved configuration/projects/credentials and existing installed models, and rolls back if activation fails. It does not install Whisper for a PC where Whisper was never installed. Run the existing `-Action CheckLocalTranscription` action to test an installed worker.

## Verification

Local verification: 184 server regression tests and 16 workspace service-logic tests passed; all existing `*.test.cjs` scripts passed. Chromium checks at 390px and 1280px passed for the workspace, automatic fallback and cancellation, audio download, Sources, settings, and account reset. The MP3 export test used the actual bundled FFmpeg worker and verified codec, duration and sample rate with FFprobe. Provider inference in application tests is mocked and does not establish live Gemini billing or Windows Whisper quality. Hosted microphone integration runs in CI because navigation to a local test server is blocked in the local test environment. No physical-iPhone test is claimed.
