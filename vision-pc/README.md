# Vision PC processor

Use an always-on Windows 10/11 Intel/AMD 64-bit PC to process Vision's YouTube links. The website, downloaded HTML, and Vision copies hosted elsewhere can use this processor over the internet when public access is enabled. Firebase and Google Cloud are not needed. Gemini/OpenAI still use their online APIs when requested.

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
