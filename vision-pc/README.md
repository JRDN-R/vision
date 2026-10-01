# Vision PC processor

Use an always-on Windows 10/11 Intel/AMD 64-bit PC to process Vision's YouTube links, save projects, and keep API conversations running when the browser closes. The website, downloaded HTML, and Vision copies hosted elsewhere can use this processor over the internet when public access is enabled. Firebase and Google Cloud are not needed. Gemini/OpenAI still use their online APIs when requested.

The setup script downloads the application and its own private runtime. You do not need to install Python or Node separately. This is an application background process, not a Windows Sandbox VM or an unrestricted remote shell.

## Enable web access on an already installed PC

On the Windows processor PC, open **Windows PowerShell > Run as administrator** and paste:

```powershell
$VisionSetup = Join-Path $env:TEMP 'Setup-Vision-PC.ps1'
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Setup-Vision-PC.ps1' -OutFile $VisionSetup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VisionSetup -Action EnablePublic
```

If Tailscale displays an approval link, open it and enable **Funnel** for this PC. If the script stops while waiting, complete approval and run the same command again.

This updates only the processor application files, reuses the installed runtime and tools, and keeps the existing address and access token. It enables public HTTPS with Tailscale Funnel and permits requests from local HTML, GitHub Pages, and other Vision hosts. **Devices using Vision do not need Tailscale. Only the Windows processor PC needs it.**

Vision HTML with the connection settings already embedded connects automatically; no connection file import or setup popup is required. Older copies without those settings can import the generated connection file once, or be replaced with a new download. Publishing the website alone cannot enable Funnel on the Windows PC; the command above performs that one-time change.

Public access is saved in `C:\ProgramData\VisionPC\config.json`. Future `Setup` and `Start` actions preserve it. The background Funnel connection and processor startup task resume after Windows restarts. Setup checks the local processor and its HTTPS endpoint before reporting success; the final public reachability check is to open Vision from another device with Tailscale turned off.

## Update an installed PC for saved projects and background conversations

Run this once from **administrator PowerShell** on the processor PC after downloading the updated Vision app:

```powershell
$VisionSetup = Join-Path $env:TEMP 'Setup-Vision-PC.ps1'
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Setup-Vision-PC.ps1' -OutFile $VisionSetup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VisionSetup -Action Update
```

`Update` downloads and checks the Python application components, restarts the processor, and restores its existing private/public HTTPS mode. It preserves configuration, the connection token, queued imports, saved projects, conversations, and retained files. It does not reinstall Python or video tools. The Windows update is required in addition to publishing the HTML.

### Saved projects and sessions

Each project has its own random project ID and secret, in addition to the publicly embedded processor connection token. The PC stores only a hash of the project secret. Projects cannot be listed or opened using the common connection token alone. The editable project file holds the secret so the same project can reopen its saves and conversations on another device; share that project file only with someone who should have access to its saved conversations.

Project autosaves use a revision check. A stale device receives a conflict instead of overwriting a newer save. Project JSON can be up to 150 MiB; a message's project ZIP and attachments together can be up to 25 MiB. The API rejects common non-empty API-key fields in project JSON. Keep the OpenAI billing key in the Run settings, separate from the project.

Accepted API turns are written to SQLite before responding to the browser. A PC worker submits the OpenAI **Responses API** request with background processing and consumes its stream independently. The model and Code Interpreter still run on OpenAI; the PC manages the conversation, network connection, saved files, and event history. Returning to the same project retrieves the saved transcript and reconnects to the active response. Only one turn can be active in a project at a time.

The PC encrypts the submitted OpenAI key with Windows DPAPI under the installed task's Windows identity. It never returns the key in a project or run snapshot. The encrypted key is retained only while the turn or file retrieval needs recovery, then cleared after completion. Each new turn supplies a key again. Running the service under a different Windows account will prevent it from decrypting pending keys; keep using the installed SYSTEM startup task.

After a PC restart, known response IDs are retrieved without creating another paid response. If a restart or network failure occurs before a response ID is safely saved, Vision marks that turn for review instead of guessing and submitting it twice. Check OpenAI usage before retrying that specific turn. Normal browser closure does not interrupt the worker.

Project files, inputs, messages, and retained deliverables remain in `C:\ProgramData\VisionPC\data` until explicitly removed; the existing temporary YouTube result cleanup does not remove them. Each generated file can be retained up to 100 MiB. OpenAI containers expire, so the worker downloads generated deliverables while the container is active. Later follow-ups reuse an active container or restore retained input/output files into a new one. If the provider's earlier response has expired, locally saved conversation text supplies the context. Python variables in an expired container cannot be restored automatically.

The PC must remain awake and online for new work and file retention. OpenAI can continue an accepted background response while the PC reconnects, but an extended outage can outlast provider file retention. A response can finish while a file download is unavailable; Vision reports unavailable files individually.

Back up the data directory using a consistent snapshot or while the processor is stopped. A live SQLite database should not be placed directly in a Google Drive sync folder; use a backup copy if cloud backup is added later. Google Drive integration is not required or installed by this update.

## Install on a new PC

Download [Setup-Vision-PC.ps1](https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Setup-Vision-PC.ps1) into **Downloads**. Open **Windows PowerShell > Run as administrator** and paste:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\Setup-Vision-PC.ps1"
```

The execution-policy setting applies only to this process. Setup installs the processor in `C:\ProgramData\VisionPC`, adds the startup task, and installs Tailscale if needed. Keep the window open while the initial components download. When Tailscale displays a sign-in or HTTPS approval link, open it and complete that step. You can rerun setup; it preserves the existing token.

A new installation initially uses **private Tailscale Serve**. Run the **Enable web access** command above to allow internet access from any Vision HTML. If you keep the initial private mode, each client device needs Tailscale connected to the same private network; browsers may also require local-network permission.

## Keep the processor available

In **Settings > System > Power & sleep**, set sleep to **Never while plugged in**. The screen may turn off. The processor starts automatically with Windows; no open terminal or logged-in desktop is required. A powered-off or sleeping PC must be turned on or woken. After an ordinary internet interruption, Tailscale reconnects when the PC is back online.

To start the processor and restore its saved HTTPS mode manually, run from administrator PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:ProgramData\VisionPC\Setup-Vision-PC.ps1" -Action Start
```

## Connection files and access

The script writes two files to the Windows Desktop:

- **Vision-Connection.txt:** the address, access token, and public/private access setting. Vision can embed these settings in its HTML. Anyone holding that token can use the available processor.
- **Vision-Connection-public.txt:** connection mode and check results without the token, for troubleshooting.

To import settings manually, click the **Vision logo > Processor connection > Import connection file** and select `Vision-Connection.txt`. This is optional when the HTML already contains the settings.

No router port forwarding is needed. The application listens only on the PC's loopback address. Tailscale provides HTTPS through private Serve or public Funnel; the application checks the access token in both modes. The installer refuses to replace unrelated Tailscale routes and does not run a global Serve reset.

## What runs where

The PC retrieves YouTube media, collects available captions, makes timestamped JPEG snapshots, and prepares audio when captions are unavailable. Vision receives the results and uses its existing Gemini queue if transcription is needed. Source video is temporary. Accepted jobs are stored on the PC so interrupted jobs can be retried after a restart; completed results are temporary and cleaned up. Vision retains waiting imports when the server is unavailable.

YouTube can restrict videos or ask for verification. Public, unrestricted videos are the intended input; the processor does not guarantee every link can be retrieved.

## Stop, update, or export settings

Run these from **administrator PowerShell** on the processor PC:

```powershell
# Stop the processor; its startup task remains enabled:
& "$env:ProgramData\VisionPC\Setup-Vision-PC.ps1" -Action Stop

# Recheck HTTPS and rewrite the Desktop connection files:
& "$env:ProgramData\VisionPC\Setup-Vision-PC.ps1" -Action ExportConnection

# Refresh the application and dependencies, retaining token and public/private mode:
& "$env:ProgramData\VisionPC\Setup-Vision-PC.ps1"
```

If local script execution is disabled, use `powershell.exe -NoProfile -ExecutionPolicy Bypass -File` before the quoted script path, as in the start command.

To keep the processor stopped across restarts, disable **Task Scheduler > Vision Private PC**. Logs are in `C:\ProgramData\VisionPC\data\server.log`. Configuration and working files are restricted to Windows administrators and SYSTEM.

To remove the processor, stop it, delete its **Vision Private PC** scheduled task, and delete `C:\ProgramData\VisionPC`. This deletes queued/completed processor data. If no other service has since taken over its route, remove only Vision's HTTPS route using the command matching its mode:

```powershell
# Public mode:
& "$env:ProgramFiles\Tailscale\tailscale.exe" funnel --https=443 off

# Private mode:
& "$env:ProgramFiles\Tailscale\tailscale.exe" serve --https=443 off
```

Leave Tailscale installed if you use it for anything else.

## Installed components and official references

- [Python embedded distribution](https://docs.python.org/3/using/windows.html#the-embeddable-package), contained inside Vision's installation.
- [Tailscale Windows installation](https://tailscale.com/docs/install/windows/msi), [unattended mode](https://tailscale.com/docs/how-to/run-unattended), [private Serve](https://tailscale.com/docs/reference/tailscale-cli/serve), and [public Funnel](https://tailscale.com/docs/reference/tailscale-cli/funnel).
- [FFmpeg Windows builds](https://ffmpeg.org/download.html#build-windows), from the linked Gyan provider with its SHA-256 check.
- [Deno](https://docs.deno.com/runtime/getting_started/installation/), bundled for media-site processing.

The installer verifies the Tailscale MSI checksum/signature and Python executable signature before running them. Application files come from this repository over HTTPS. It does not request your Windows password, Firebase credentials, or OpenAI key during setup.
