# Vision

An advanced prompt generator: arrange images, documents, notes, and processed
media into a board, connect the modules, and export the instructions and evidence.

[Open Vision](https://jrdn-r.github.io/vision/), or download the portable HTML from
the app. Board editing works offline. Video imports from YouTube, PC saves, and
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

Editable application code is in `web/`. `Vision.html` also holds the bundled media
runtimes and artwork; `web/build.py` preserves those assets while replacing the
application code and styles. Run:

```sh
python web/build.py
```

The builder updates the portable HTML and the small GitHub Pages loader's cache
version. Both use the same application. PC service source and tests are in
`vision-pc/`. Tests use mocked OpenAI responses and do not make billable API calls.
