# Vision

An advanced prompt generator: arrange images, documents, notes, and processed
media into a board, connect the modules, and export the instructions and evidence.

[Open Vision](https://jrdn-r.github.io/vision/), or download the portable HTML from
the app. Board editing works offline. PC video imports, YouTube imports, PC saves, and
OpenAI conversations need the connected processor and an internet connection.

## Projects and conversations

The connected Windows PC can save projects and run the OpenAI Responses
connection independently of a browser tab. A project keeps its own connection
identity, conversation, and file references. Reopen that project to retrieve
progress or completed results. OpenAI still performs the model processing and
code-interpreter execution; the PC handles the connection, saved work, and files.

Use the project control to check save status and open recent projects. Download
an editable project when moving to a different device. Its project connection
key travels with the editable project, but is excluded from AI export packages.
Conflicting edits from two devices are shown for review rather than silently
overwriting the PC copy. Browser recovery depends on browser storage remaining
available, so keep a downloaded project copy for access from another browser.

Run offers a continuing conversation, attachments, copy controls, and one current
progress line. Dictation appears when the browser supports speech recognition;
the transcribed text stays editable before sending. Completion appears on the
board while Vision is open. Optional system notifications depend on browser
support and permission.

Update an existing PC installation using the instructions in
[vision-pc/README.md](vision-pc/README.md). Keep the PC awake and online. Files and
project history are stored on that PC; Google Drive backup is not configured by
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

The board microphone records a voice note and lets you choose Local PC Whisper
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

Video files added as new modules use the PC to create timestamped screenshots,
audio sections for the selected transcription provider, and a 480-pixel, 15 fps
playable preview with mono audio. A poster and current processing step appear
on the module. Accepted video jobs continue on the PC after the browser closes;
reopen the saved project to retrieve the result. Playback needs the PC connection.
The original upload is removed after processing; the compact preview remains
on the PC and is not embedded in project downloads. Initial uploads support
100 MB and up to two hours; update the PC processor before using this feature.
Videos attached inside an existing module retain the browser-processing path.
Use **Projects → PC video storage** to remove saved previews, including videos
whose modules you deleted. Removing a module alone keeps its PC files available
for undo and older saved projects. Explicit PC deletion preserves snapshots and
transcripts already saved in the board, but removes playback for those copies.

## Local speech transcription

The optional **Local PC** transcription provider runs an open-source English
Whisper model on the connected PC, using four CPU threads and one worker. It
produces timestamped text without Gemini or OpenAI transcription API charges.
The PC must stay awake and online for remote use; electricity and storage still
apply. Local transcription does not silently fall back to a paid provider.

Install the model explicitly with `-Action InstallLocalTranscription` using the
[PC instructions](vision-pc/README.md#optional-local-transcription-without-api-charges).
Normal updates preserve an installed model and do not download one automatically.
The same instructions include disable/enable commands for church services.
Gemini transcription and OpenAI conversations remain separate online options;
their provider's billing terms apply when selected.

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
version. Both use the same application. PC service source and tests are in
`vision-pc/`. Tests use mocked OpenAI responses and do not make billable API calls.
