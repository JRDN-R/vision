# Vision private PC processor

Use an always-on Windows 10/11 Intel/AMD 64-bit PC to process Vision's YouTube links. Phones, the website, and downloaded HTML can use the same private processor. Firebase and Google Cloud are not needed in this mode. Gemini/OpenAI still use their online APIs when requested.

The setup script downloads the application and its own private runtime automatically. You do not need to install Python or Node separately. This is an application background process, not a Windows Sandbox VM and not an unrestricted remote shell.

## 1. Run this on the Windows PC

Download [Setup-Vision-PC.ps1](https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Setup-Vision-PC.ps1) into **Downloads**. Right-click **Windows PowerShell** in Start and select **Run as administrator**. Paste:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\Setup-Vision-PC.ps1"
```

This execution-policy setting applies only to that process; it does not change your system policy.

Setup installs the private processor in `C:\ProgramData\VisionPC`, adds a startup task, and installs Tailscale if needed. The initial download may take several minutes. Keep the window open until setup finishes.

When Tailscale displays a sign-in link, open it and sign in. If it asks you to enable HTTPS, approve that for your private network. If setup stops while waiting for either step, finish the browser step and run the same command again; it keeps the existing connection token.

The script checks both the local processor and private HTTPS before reporting success. It will not replace another application's existing Tailscale Serve configuration.

## 2. Keep the processor available

In **Settings > System > Power & sleep**, set sleep to **Never while plugged in**. The screen may turn off. The processor starts automatically with Windows and does not require an open terminal or a logged-in Windows desktop. A powered-off, sleeping, disconnected, or restarting computer will be unavailable until it returns.

## 3. Connect your phone or another computer

Install [Tailscale](https://tailscale.com/download) on each device using Vision and sign in to the **same account/private network**. Turn its connection on.

Setup writes two text files to the Windows Desktop:

- **Vision-Connection.txt:** your private connection settings, including an access token. Transfer it privately to your own device, then import it in Vision's connection settings. You can also paste the JSON between its BEGIN/END markers.
- **Vision-Connection-public.txt:** the server address and connection check results, without the token. Send this one if you want help connecting. This report does not grant access by itself.

In Vision, click the **Vision logo**, open **Processor connection**, choose **Import connection file**, and select the private connection file. Click **Connect**. Use the same connection on the website and in a newly downloaded HTML copy. Each device must remain connected to Tailscale. A browser may ask to allow local-network access; allow it for Vision to reach your private server.

No router port forwarding is required. The processor listens only on the PC's loopback address; Tailscale Serve provides the private HTTPS route. Access requires both the private network connection and Vision's random access token. The text file with that token should not be committed to GitHub, pasted into a public issue, or sent for support.

## What runs where

The PC retrieves the YouTube media, collects available captions, makes timestamped JPEG snapshots, and prepares audio when captions are unavailable. Vision receives the results and uses its existing Gemini queue if transcription is needed. The source video is temporary. Accepted jobs are stored on the PC so an interrupted job can be retried after a restart; completed results are temporary and cleaned up. Vision reports an unavailable server when it cannot connect.

YouTube can restrict some videos or ask for verification. Public, unrestricted videos are the intended input. A private processor does not guarantee every link can be retrieved.

## Start, stop, update, or export settings again

Run these from **administrator PowerShell** on the processor PC:

```powershell
# Start or stop the processor without uninstalling it:
& "$env:ProgramData\VisionPC\Setup-Vision-PC.ps1" -Action Start
& "$env:ProgramData\VisionPC\Setup-Vision-PC.ps1" -Action Stop

# Recheck the connection and rewrite the two Desktop text files:
& "$env:ProgramData\VisionPC\Setup-Vision-PC.ps1" -Action ExportConnection

# Refresh application files and dependencies, keeping the existing token:
& "$env:ProgramData\VisionPC\Setup-Vision-PC.ps1"
```

If local script execution is disabled, use `powershell.exe -NoProfile -ExecutionPolicy Bypass -File` before the quoted script path, as in the initial setup command.

Stopping the process leaves its startup task enabled. To keep it stopped across restarts, disable **Task Scheduler > Vision Private PC**. Logs are in `C:\ProgramData\VisionPC\data\server.log`. The configuration and working files are restricted to Windows administrators and SYSTEM.

To remove Vision's processor, stop it, delete its **Vision Private PC** scheduled task, and delete `C:\ProgramData\VisionPC`. This deletes queued/completed processor data. If no other service has since taken over the route, remove only Vision's route with:

```powershell
& "$env:ProgramFiles\Tailscale\tailscale.exe" serve --https=443 off
```

Leave Tailscale installed if you use it for anything else. Do not run a global Serve reset on a PC that hosts other applications.

## Installed components and official references

- [Python embedded distribution](https://docs.python.org/3/using/windows.html#the-embeddable-package), contained inside Vision's installation.
- [Tailscale Windows installation](https://tailscale.com/docs/install/windows/msi), [unattended mode](https://tailscale.com/docs/how-to/run-unattended), and [private HTTPS Serve](https://tailscale.com/docs/reference/tailscale-cli/serve).
- [FFmpeg Windows builds](https://ffmpeg.org/download.html#build-windows), downloaded from the linked Gyan provider with its SHA-256 check.
- [Deno](https://docs.deno.com/runtime/getting_started/installation/), bundled for media-site processing.

The installer verifies the Tailscale MSI checksum/signature and Python executable signature before running them. Application files come from this repository over HTTPS. It does not request your Windows password, Firebase credentials, or OpenAI key during setup.
