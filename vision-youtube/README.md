# Vision YouTube setup

This free helper runs on your own computer. No Firebase account, cloud server, subscription, or billing setup is needed. YouTube imports require internet. Gemini is used only when usable captions are unavailable, under your existing Gemini account and limits.

The computer must be on and this helper must be running while YouTube imports are processing. Open Vision using the helper's local link rather than the GitHub Pages link for these imports. Your regular local files, saved projects, and exports still work.

## 1. Download Vision

Download [the Vision project ZIP](https://github.com/JRDN-R/vision/archive/refs/heads/main.zip) and extract it. The extracted `vision-main` folder contains `Vision.html` and this `vision-youtube` folder. Keep them together.

If you already have a board open, use **Save project** first. Open that saved project in the local helper's Vision page after setup.

## 2. One-time setup on Windows

Open **PowerShell** and run these separately:

```powershell
winget install --exact --id Python.Python.3.12
winget install --exact --id DenoLand.Deno
```

Close and reopen PowerShell so the new programs are available. Then double-click **vision-youtube/start-windows.cmd** inside your extracted folder. On the first run it installs the helper dependencies; subsequent runs open Vision immediately.

If you prefer the manual commands, assuming you extracted the download to your Downloads folder:

```powershell
cd "$env:USERPROFILE\Downloads\vision-main"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\vision-youtube\requirements.txt
.\.venv\Scripts\python.exe .\vision-youtube\server.py
```

If your extracted folder is somewhere else, change the `cd` path. No PowerShell activation script is required.

## 2. One-time setup on Mac

If you have Homebrew, open **Terminal** and run:

```sh
brew install python@3.12 deno
```

Then double-click **vision-youtube/start-mac.command** inside the extracted folder. If macOS does not allow it to open, run `bash ~/Downloads/vision-main/vision-youtube/start-mac.command` in Terminal. These are the equivalent manual commands:

```sh
cd ~/Downloads/vision-main
python3.12 -m venv .venv
.venv/bin/python -m pip install -r vision-youtube/requirements.txt
.venv/bin/python vision-youtube/server.py
```

If you do not have Homebrew, follow the installation instructions at [brew.sh](https://brew.sh/), then run the commands above. Change the `cd` path if you extracted the folder somewhere else.

## 3. Import a video

1. Open **http://localhost:8765/** in your browser. Leave the Terminal/PowerShell window running.
2. Open your saved project if you have one.
3. Use **YouTube** in Vision and paste a normal YouTube URL, a `youtu.be` link, or a Shorts link.
4. Watch the queue from the top banner. Available captions become a timestamped transcript. If captions cannot be retrieved, compressed speech audio goes into Vision's normal Gemini queue.
5. Save the project while transcription is pending if you need to close the browser. Export when it finishes.

To start again another day, open Terminal/PowerShell, `cd` into `vision-main`, and run only the final server command for your operating system.

## Optional: iPhone or iPad on the same Wi-Fi

Start with the home-network option instead:

Windows:

```powershell
.\.venv\Scripts\python.exe .\vision-youtube\server.py --bind 0.0.0.0
```

Mac:

```sh
.venv/bin/python vision-youtube/server.py --bind 0.0.0.0
```

The window prints a **Same Wi-Fi phone/tablet** link containing a private access token. Open that exact link in Safari while both devices are on the same Wi-Fi. Allow local-network access if asked. If Windows Firewall asks, allow **private networks** only. Keep the link private and do not forward the port on your router.

Browsers restrict some transcription features on unsecured home-network pages. If captions are unavailable and transcription cannot start on your phone, save the project and open it on the computer at `http://localhost:8765/` to finish.

## What gets kept

- Screenshots are JPEGs with visible timestamps. Up to 5 seconds: every second; under 5 minutes: every 5 seconds; under 10 minutes: every 30 seconds; 10 minutes or longer: every 60 seconds.
- Human-written captions are preferred, then automatic captions, then Gemini audio transcription. The original language is preferred when available, followed by English.
- The source video is temporary and is deleted after processing. It never enters the saved project or AI export.
- If needed, compressed audio remains in Vision only while its transcript is pending. Vision removes it after successful transcription.
- The helper discards delivered results when Vision acknowledges them, otherwise after one hour. Closing the helper clears remaining in-memory results. Interrupting or crashing the computer mid-import may leave an operating-system temporary folder named `vision-youtube-*`, which can be deleted after stopping the helper.
- Export offers individual screenshots or contact sheets with up to nine frames. Individual screenshots keep the best detail. A grid reduces file count but does not guarantee lower model token use.

Imports support finished public videos up to two hours long. Some YouTube videos request sign-in or block automated retrieval; those will need to be uploaded as a local video instead. The helper does not bypass access restrictions.

## Update or troubleshoot

From the same folder, update the media helper dependencies:

Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade -r .\vision-youtube\requirements.txt
```

Mac:

```sh
.venv/bin/python -m pip install --upgrade -r vision-youtube/requirements.txt
```

Then stop and restart the helper. Check `deno --version` if it says Deno is missing. If port 8765 is busy, add `--port 8766` and open the new printed link. Gemini's existing approximately 15-minute speech-aware chunks and 60-second gaps are handled by Vision after import, not by this helper.

## API for the Vision client

All requests are same-origin. LAN requests use `X-Vision-Token`, read from `#helper-token=...` in the initial link. No cloud CORS bridge is needed.

- `GET /api/health` → `{service, version, authRequired}`.
- `POST /api/youtube` with JSON `{url}` → `{id, status:"queued"}`.
- `GET /api/jobs/{id}` → `{id,status,phase,progress,title?,error?}`. Progress covers local processing only, not subsequent Gemini transcription.
- `GET /api/jobs/{id}/result` → `{title,url,duration,snapshotInterval,thumbnail,frames,transcript,audio}`. Frame data and thumbnail are JPEG data URLs. Transcript is `{text,language,source}` or `null`. Audio is `{name,mime,data}` or `null`.
- `DELETE /api/jobs/{id}` releases a completed/failed job.

Queue length is capped at three active imports, processed one at a time to keep computer resource use reasonable.
