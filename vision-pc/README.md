# Vision PC processor

Use an always-on Windows 10/11 Intel/AMD 64-bit PC to process Vision's YouTube links, save projects, transcribe speech with an optional local model, and keep API conversations running when the browser closes. The website, downloaded HTML, and Vision copies hosted elsewhere can use this processor over the internet when public access is enabled. Firebase and Google Cloud are not needed. Gemini/OpenAI still use their online APIs when requested.

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

`Update` downloads and checks the Python application components, restarts the processor, and restores its existing private/public HTTPS mode. It preserves configuration, the connection token, queued imports, saved projects, conversations, retained files, and any installed local transcription model. It does not reinstall Python or video tools, install optional model dependencies, or download model weights. The Windows update is required in addition to publishing the HTML.

<a id="local-transcription"></a>

## Optional local transcription without API charges

The **Local PC** transcription option runs the open-source Whisper small English model on this PC. It uses CPU int8 inference, four CPU threads, and one transcription worker. It does not call Gemini or OpenAI for transcription, does not require an API key, and does not automatically switch to a paid provider when unavailable. Electricity, disk space, CPU time, and the existing internet connection still apply. Other features that explicitly use Gemini or OpenAI have their own provider charges.

Run this once from **administrator PowerShell** on an already installed processor PC. Do it outside a service or an important active run because activation briefly restarts the processor:

```powershell
$VisionSetup = Join-Path $env:TEMP 'Setup-Vision-PC.ps1'
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Setup-Vision-PC.ps1' -OutFile $VisionSetup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VisionSetup -Action InstallLocalTranscription
```

This action also stages the current processor application, so a separate `Update` is not needed first. It downloads about 486 MB of model files plus optional Python packages from PyPI. Allow several minutes and at least a few GB of temporary free space. The model is the fixed [Systran/faster-whisper-small.en](https://huggingface.co/Systran/faster-whisper-small.en/tree/d1d751a5f8271d482d14ca55d9e2deeebbae577f) revision `d1d751a5f8271d482d14ca55d9e2deeebbae577f`.

The existing processor keeps running during installation and model validation. Optional packages and model files go into their own directories under `C:\ProgramData\VisionPC\plugins` and `models`; the base runtime packages are not replaced. The installer checks offline model loading, short CPU inference, WAV decoding and the speech filter before enabling anything. Activation restores the previous application and configuration if the restarted processor fails its health check. It retains the connection token, projects, conversations, startup task, and Tailscale route. A failed download or validation leaves the previous processor running; diagnostic logs and unused download directories are retained for troubleshooting.

After the command reports success, refresh Vision and select **Local PC** for transcription. Use a fresh HTML download for an older local copy. The PC must be awake and reachable while transferring audio and processing work. Model inference uses the already downloaded files, with online model downloads disabled at runtime. The model does English transcription with timestamps; timestamps can be approximate, and speaker labels are not provided. Review names, numbers, overlapping voices, music, and noisy speech before relying on the transcript.

### Pause the local model for church services

These actions restart the Vision processor, so use them before starting important work. They do not uninstall the model or remove saved projects:

```powershell
# Stop local transcription; keep the rest of the processor available:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:ProgramData\VisionPC\Setup-Vision-PC.ps1" -Action DisableLocalTranscription

# Restore local transcription with the existing model, without downloading again:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:ProgramData\VisionPC\Setup-Vision-PC.ps1" -Action EnableLocalTranscription
```

Disabling the model does not stop separate YouTube imports, conversations, or other Windows applications. To stop all Vision work during a service, use the existing `Stop` action and later `Start`. Automatic service-hour scheduling is not configured.

`InstallLocalTranscription` makes a fresh isolated installation each time; use `EnableLocalTranscription` for routine re-enabling. Logs are `C:\ProgramData\VisionPC\data\local-*.log`. On Windows, [CTranslate2 requires the Microsoft Visual C++ runtime](https://opennmt.net/CTranslate2/installation.html). If validation reports a missing runtime DLL, install or repair Microsoft's supported x64 runtime, then rerun installation. This script does not install a system Python, GPU drivers, CUDA, or the Visual C++ runtime, and it does not change the Windows password or login settings. The installer has static and isolated model checks; it has not been executed on this particular Windows PC by the developer.

### If installation passes but a recording fails

Update the processor code and run its background-worker check from administrator PowerShell. This reuses the installed model and optional packages; it does not download them again. `Update` briefly restarts the processor, so run it outside a service or important active work.

```powershell
$VisionSetup = Join-Path $env:TEMP 'Setup-Vision-PC.ps1'
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Setup-Vision-PC.ps1' -OutFile $VisionSetup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VisionSetup -Action Update
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VisionSetup -Action CheckLocalTranscription
```

The check submits generated WAV and MP3 silence to the running local processor, using the same queue and Windows task identity as a real recording. It checks decoding, Whisper, the ONNX speech filter, and the saved result, without calling an AI API. It keeps one small diagnostic project and reuses it. If the PC is busy and the check times out, it cancels only its own test job.

If a previous check stopped at **Loading the local libraries** even though installation passed, run the update above. The Windows worker now watches its parent using Win32 pipe checks instead of a blocking input read, avoiding the [known NumPy import deadlock](https://github.com/numpy/numpy/issues/24290). Library startup has a separate 90-second limit so a stalled import cannot occupy the queue for half an hour. This fix reuses the existing model and packages.

New failed jobs show the failed stage and a diagnostic code in Activity; click **Retry** after updating to replace an older generic error. Detailed worker exceptions are retained in the private `data\server.log` (including native worker exit codes). The check prints only its own worker diagnostic. Model paths and raw exceptions are not returned to the web app. A successful generated-audio check confirms the processing path; it does not validate a particular recording or its transcript quality.

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

The processor starts automatically with Windows; no open terminal, Windows password, or logged-in desktop is required. After an ordinary internet interruption, Tailscale reconnects when the PC is back online.

Download the availability helper and apply it from **administrator PowerShell** on the PC:

```powershell
$VisionAvailability = Join-Path $env:TEMP 'Keep-Vision-PC-Available.ps1'
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Keep-Vision-PC-Available.ps1' -OutFile $VisionAvailability
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VisionAvailability -Action Enable
```

The helper saves the original settings, turns off automatic sleep and hibernation **while plugged in on the current power plan**, and checks the existing Vision startup task. It preserves the task's application and service identity, enables startup/retry settings, and starts the task if it is stopped and the processor is offline. The display can still turn off. To apply it to a different power plan later, restore the original backup first, select the new plan, then enable it again. This does not prevent manual shutdowns, Windows restarts, crashes, overheating, or loss of electricity.

It also writes a Desktop availability report with power settings, wired-network wake capabilities, Vision and Chrome Remote Desktop service/task status, and recent shutdown events. It does not include Vision connection tokens, Windows passwords, or API keys. Review it before sharing: it describes your machine and local network.

Use the same downloaded script with `-Action Status` for a report without changing settings, or `-Action Restore` to restore the backed-up settings. Enable/Restore require administrator privileges. Wake-on-LAN changes are optional with `-Action Enable -EnableWakeOnLan -AdapterName 'Ethernet'`; substitute the physical adapter's actual name from the report. Supported adapter settings are applied without restarting the network adapter. BIOS and driver-specific wake options may still need manual configuration.

### Recover after shutdown or power loss

Keeping Windows awake handles idle time. Waking a PC that is already off is a separate function: software on that PC cannot receive commands while it is off. For offsite Wake-on-LAN, a router or another powered device on the PC's network must send the wake packet. The PC needs wired Ethernet, electricity, and compatible firmware/adapter settings. Test this with someone onsite before relying on it.

For the **MSI MPG X570 GAMING EDGE WIFI**, the [official manual](https://download.msi.com/archive/mnu_exe/mb/E7C37v1.1.pdf), printed pages 52–53, documents these BIOS options under Settings > Advanced:

| Menu | Setting | Purpose |
| --- | --- | --- |
| Power Management Setup | ErP Ready: Disabled | Permits supported network/device wake in low-power states. |
| Power Management Setup | Restore after AC Power Loss: Power On | Boots when electricity returns after an outage. |
| Wake Up Event Setup | Wake Up Event By: BIOS | Enables the firmware wake settings below. |
| Wake Up Event Setup | Resume By PCI-E Device: Enabled | Enables supported wake through the integrated LAN controller. |

The manual also provides an RTC alarm for scheduled starts. BIOS menus can vary with firmware version. These settings cannot be verified from the supplied Windows report, and the helper does not change them. Do BIOS setup while someone can access the physical PC.

[Microsoft documents a Windows 10 limitation](https://learn.microsoft.com/en-us/troubleshoot/windows-client/setup-upgrade-and-drivers/wake-on-lan-feature): Fast Startup shutdown does not arm the network adapter for wake; wake from a full shutdown depends on firmware and hardware support. Enabling one Windows checkbox does not establish that shutdown wake works on this specific PC.

Chrome Remote Desktop is separate from Vision. Its installed remote-access host should be checked after a restart at the Windows sign-in screen. Automatic Windows sign-in is not needed for Vision. A UPS can reduce outages, but recovery still depends on power and network service returning.

### Replacing Tailscale later

The PC already handles Vision files and background requests; Tailscale supplies the reachable HTTPS connection. Removing it does not remove charges for paid AI APIs. Current [Tailscale pricing](https://tailscale.com/pricing) is based on plans, users, and resources, not a per-request charge for each Vision action. Personal-plan eligibility and an organization's plan should be checked separately.

Direct HTTPS is possible with a reverse proxy such as [Caddy](https://caddyserver.com/docs/quick-starts/https), DNS, and a router/ISP connection that permits inbound traffic. A private WireGuard connection is another option, but client devices then need VPN configuration. The router model, router access, and whether the ISP uses carrier-grade NAT determine which route works. A Firebase login can authenticate users; it does not make an offline PC reachable or replace a network route.

Keep the working connection until its replacement has been tested. Changing the endpoint also needs a Vision connection/project migration so existing saves and conversations keep working. The availability helper does not alter this connection.

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

The PC retrieves YouTube media, collects available captions, makes timestamped JPEG snapshots, and prepares audio when captions are unavailable. Vision receives the results and uses the selected transcription provider when transcription is needed. Local PC requires the optional installation above; Gemini is a separate online provider. Source video is temporary. Accepted jobs are stored on the PC so interrupted jobs can be retried after a restart; completed results are temporary and cleaned up. Vision retains waiting imports when the server is unavailable.

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
- Optional [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and [CTranslate2](https://opennmt.net/CTranslate2/), with the pinned English model linked above. Installation is separate from the standard setup/update.

The installer verifies the Tailscale MSI checksum/signature and Python executable signature before running them. Application files come from this repository over HTTPS. It does not request your Windows password, Firebase credentials, or OpenAI key during setup.
