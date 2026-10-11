# Vortex

Vortex runs at `https://jrdn-r.github.io/vision/vortex/`. Its page and modules are
independent of the Vision board and Venture conversation interface. Vision also offers Vortex imports in Add to board and the module Files tab.
Opening Vortex in the same tab saves pending board changes first.

## Import into Vision

Choose **Vortex** in **Add to board**, or **+ Vortex** in a selected module's
**Files** tab. Paste a video/audio URL, choose **Find**, then **Apply**. The red
orbital URL field matches Vortex. Controls retain the current transcription
provider and sound-effects preference, and let a Files import create a new module.
There is no download or video/audio format chooser in this dialog.

The server prefers 320–480p video where the source offers it, falls back to a
small available stream, and converts processing audio to 16 kHz mono at 32 kbps.
Video produces timestamped snapshots and a transcript. Audio-only links,
including YouTube Music and individual Spotify tracks, produce a transcript.
Spotify uses the existing metadata/matched-public-audio workflow. Source
availability, DRM, sign-in requirements and the two-hour limit are unchanged.

After Apply is accepted, download, snapshots and transcription run durably on
FUPCJ Server even if the browser closes. Gemini approval still applies. Reopen
the saved Vision project to receive the result. Completed import receipts remain
available for 24 hours to cover delayed project saves. Original media is not embedded
in Vision or its ZIP/RAG exports; the small processing copy is deleted once
snapshots and the durable transcription receipt are saved.

A separate account-owned **Balanced MP4** video or default **M4A** audio download
is silently queued in Vortex history. It uses the usual five-day retention,
account ownership, queue and disk limits. A full queue/storage retries the
history handoff without blocking the Vision result or creating duplicate copies.
History download failures remain visible in Vortex, without a Vision notification.

**This integration requires a PC update.** Run the latest installer with
`-Action Update` using the command below. An existing Vortex installation does
not need its engines or Docker services reinstalled. The updater includes
`vortex_imports.py`; the UI detects older processors and asks for the update.

## Version

Current release: **v1.0.1**. This patch fixes successful normalized URL lookups
being silently discarded by the webpage and adds visible feedback when an
inspection receipt is no longer available. The active job retains its original
submitted text so shared/short URLs can match the server's canonical response
without allowing late results to overwrite a genuinely edited input.
The fix is in the webpage and works with the existing backend; no engine or
Docker reinstall is required. Updating the Windows processor is only needed
to advance its separately reported version. This does not make unavailable
provider media accessible or bypass authentication requirements.

Vortex starts its own release numbering at **v1.0.0**, independent of Vision's
version. Open the side menu to see the web version directly beneath the Vortex
name. **Your account** also shows the connected Vortex server's version, reported
by the authenticated jobs response without an extra request or extraction job.
Older servers show "version not reported"; unavailable connections do not keep
showing an earlier server version. The web and Windows versions can differ:
publishing GitHub Pages does not update FUPCJ Server. A version number confirms
the software release, not that any particular provider or media link works.

For a release, update `VORTEX_VERSION` in `vision-pc/vortex.py` and the sidebar
label, accessible label, and asset revision queries in `vortex/index.html`.
Keep browser fixture expectations in sync; the backend regression test checks
that the shipped web label and asset revisions match the backend release.

## Activate on FUPCJ Server

Merge the reviewed Vortex change into `main`, then let **Build portable Vision**
finish publishing the navigation changes. On the FUPCJ Windows PC, run the
following in an administrator PowerShell window:

```powershell
$VortexSetup = Join-Path $env:TEMP 'Setup-Vision-PC.ps1'
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Setup-Vision-PC.ps1' -OutFile $VortexSetup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VortexSetup -Action Update
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VortexSetup -Action InstallVortexTools
```

`Update` installs the persistent queue, worker, routes and yt-dlp integration.
`InstallVortexTools` installs gallery-dl and spotDL in a separate versioned
package directory so their dependencies do not replace the shared Vision and
Venture packages. The installer stages the changed processor, retains its
rollback snapshot, restarts the existing task, and checks its health. It keeps
the existing Firebase project, server connection, port 8765 and Tailscale Funnel.
No new Firebase database or storage bucket is needed.

Firebase authentication must already be enabled. The existing account setup is
`-Action EnableGoogleSignIn -FirebaseProjectId visionboard-api`. Vortex rejects
anonymous trials and installation connection keys. All media processing and
file storage happen on FUPCJ Server; keep the PC awake and online.

For a reviewed pre-merge processor test, download this same script from the
reviewed commit and pass that full commit SHA as `-SourceRef` to both actions.
Do not use a slash-containing branch name; the updater accepts a commit SHA or
a simple source reference. Publishing the new webpage does not update Windows.

## Use

Paste a supported public media URL and submit the field. Text queries search
YouTube videos and YouTube Music songs together; prefix a Spotify search with
`spotify:` (for example, `spotify: artist song`). A temporary failure in one
YouTube search source does not discard matches from the other. Long or live
search results can be listed; the duration/live restrictions apply when an
individual result is selected, never to the whole search.
Search results display source thumbnails and append another page as you scroll.
A Load more button also supports keyboard access and retries. Each request reads
up to eight videos and eight songs, plus a look-ahead result; searches stop when
the source has no next page (or after 50 bounded pages). Existing results stay
visible if a later page fails, and repeated URLs are not added twice.
Select a result to load actual source properties, choose Small,
Balanced or Max, then Download. Source details remain blank until supplied by
the engine. Unknown progress stays indeterminate; a percentage appears only
when a stream's byte count or fragment count is known. Percentages describe the
current stream or gallery file, not a fabricated estimate for the whole job.
Changing Small, Balanced or Max refreshes properties in the background while
keeping the current media visible. A failed quality refresh retains the last
confirmed properties and preset. The profile button uses the saved Vision
account photo from the shared authenticated profile endpoint.

Small selects video up to 480p, Balanced up to 1080p, and Max the highest
available source resolution. Video exports are explicitly **MP4** (default) or
**MOV**, with H.264 video and AAC audio. Compatible streams are copied; other
codecs are converted locally by FFmpeg. Smaller presets also downscale when a
source has no suitably small stream. Conversion can take longer than downloading
and can change the original encoding, but never invents higher source quality.

Choose **Audio only** on a video to discard its picture. Videos, YouTube Music
and other audio sources export as **M4A** (default), **MP3** or **WAV**. The worker
starts with the highest available audio source; AAC can be copied into M4A,
other M4A audio uses AAC, MP3 uses high-quality VBR, and WAV uses 16-bit PCM.
WAV is larger and does not restore information lost in the source. New video
and audio exports never deliver MKV, WebM or Opus files. Gallery images remain
original; gallery videos are converted before single-file or ZIP delivery.
Existing completed files are retained until their normal expiry; Download again
creates a new compatible export. Repeat downloads retain the chosen output type
and format independently of the current selection.

Click the selected thumbnail, or a thumbnail in Recent Activity, to open its
original source in a new tab. This remains available during and after conversion.
Conversion progress uses actual encoded timestamps when the duration is known;
otherwise it stays indeterminate. Final technical properties come from ffprobe.

Open an item's overflow menu, or hold the item on a touch screen, to save,
share, cancel, download again or delete it. Native file sharing is offered when
the browser supports it and the file fits its in-memory sharing limit. Preparing
the file and tapping Share are separate steps so iOS receives a fresh user
gesture. Save file uses a short-lived URL and the browser's download/Save to
Files workflow, without holding a large video in page memory.

## Parallel extraction and engine availability

A user action still creates exactly one SQLite job and one history row. Within
that job, `vortex_urls.py` canonicalizes known provider URLs and the guarded
worker expands supported share links. Unknown hosts, identifiers and signed
query strings are not guessed. Music identity and non-first X media selections
are retained. Each redirect is validated before fetching it.

`vortex_adapters.py` defines compatibility and adapter dispatch;
`vortex_race.py` owns isolated processes, scheduling and cancellation. All
installed/configured compatible inspections start together (maximum five).
For Small/Balanced inspection, the first usable result returns. Max inspection
waits for bounded candidate catalogs and selects the greatest known source
height. A download first collects compatible inspections, then races at most
two downloads in the best known quality cohort. Small/Balanced heights are
compared after their 480p/1080p cap. Lower or unknown-quality candidates remain
available if better candidates fail. No candidate is declared successful solely
because metadata was returned. Re-extraction cannot silently lower a known
resolution; conversion is never allowed to upscale it.

Each attempt has a separate generated working directory and process tree.
Downloads run through the existing FFmpeg export pipeline. A winner must have
the requested container and streams, valid duration, acceptable size/resolution,
and pass a full local FFmpeg decode. The worker moves exactly one winning file
to the job directory, reaps competitors and removes temporary files before
returning success. Losing attempt failures never create jobs/history entries.
No engine configuration comes from a browser request.

| Integration | Implementation and deployment status |
| --- | --- |
| yt-dlp | Native public video/audio inspection and download; compatible providers and existing YouTube search/pagination. Installed by Update/InstallVortexTools. |
| gallery-dl | Native public gallery/video extraction; now races yt-dlp for X, Instagram and Reddit. X candidates must match the requested tweet ID; quotes, replies and previews are excluded. Installed by InstallVortexTools. |
| Instagram signed MP4 | New local adapter accepts a pasted direct HTTPS Instagram CDN MP4 or an explicit `dl.videodropper.app/?url=...` download link **only when it wraps one Instagram CDN MP4**. Attempts the CDN first, then the user-supplied relay if needed, with standard size limits, local FFmpeg conversion and full-file verification. Never sends ordinary Instagram links to VideoDropper automatically; signed URLs expire. |
| spotDL | Existing official Spotify metadata and public YouTube/YouTube Music audio matching. Only compatible Spotify work is dispatched here. Installed by InstallVortexTools. |
| FFmpeg/FFprobe | Existing Windows tools; shared local conversion plus mandatory final file verification. |
| Cobalt | Implemented opt-in API adapter and pinned self-hosted Linux-container provisioning. The local service must pass health checks; unsupported services, picker responses and unavailable streams fail only this adapter. It is not enabled by a normal Update. |
| FxEmbed/FxTwitter | Implemented opt-in metadata/stream adapter for the current v2 API, with v1 envelope parsing compatibility. It verifies exact post identity, rejects quoted/external/multiple-video ambiguity, chooses MP4 variants, and downloads the actual public stream through the guarded worker. No default endpoint; not automatically provisioned. |
| twitter-video-dl | **Not installed or advertised as operational.** Reviewed upstream is from 2023, has no request timeouts, may match media from the entire thread, follows the first detected repost, and writes request state to its package directory. Its guest-token lookup failed on both supplied X examples in live upstream checks. Substituting a fake wrapper would not add a reliable independent engine. |

Engine presence is not proof that a provider accepts anonymous requests. Missing
packages, failed imports, unhealthy services and per-source failures are isolated.
Spotify audio remains matched audio, not Spotify's original stream. DRM, private
accounts, browser-cookie collection, CAPTCHA bypass, live streams and unbounded
playlists are not supported. Optional Spotify credentials continue to be scoped
to the Spotify child; no Firebase tokens or unrelated credentials are forwarded.

All browser messages are neutral (Finding media, Preparing download, Downloading
media, Converting to the selected format). All-engine failure yields one concise
retrieval error. Progress is indeterminate unless a foreground candidate has real
byte/fragment/encode measurements. Internal diagnostic events contain only an
allowlisted engine, failure category and elapsed milliseconds, and are sent to
the server logger. Raw exceptions, URLs, cookies and signed streams are not logged.

### Optional Cobalt service on FUPCJ

For an existing Vision PC, `Setup-Vortex-Windows.ps1` installs the Windows
prerequisites and Cobalt in one workflow. Run this in **64-bit Windows PowerShell
as administrator**, from the account that normally signs into FUPCJ:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $VortexBootstrap = Join-Path $env:TEMP 'Setup-Vortex-Windows.ps1'
    Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Setup-Vortex-Windows.ps1' -OutFile $VortexBootstrap
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VortexBootstrap -AcceptDockerLicense
    if ($LASTEXITCODE -eq 3010) {
        Write-Host 'Restart Windows when convenient, sign in, and paste this same block again.'
    } elseif ($LASTEXITCODE -ne 0) {
        throw 'Vortex setup stopped. See the message above.'
    }
}
```

`-AcceptDockerLicense` permits Docker's documented unattended license acceptance
flag. Review the [Docker Subscription Service Agreement](https://www.docker.com/legal/docker-subscription-service-agreement/)
before running it. This does not purchase a subscription or create an account.

The installer checks the local Docker Linux engine first. When it already works,
it reuses the installed prerequisites and preserves the existing Linux backend;
it does not reinstall WSL based on a version-query failure. Otherwise it enables
WSL and Virtual Machine Platform without restarting the PC, installs modern WSL
if necessary, and installs Docker with `--backend=wsl-2`. It downloads
official portable MinGit if Git is missing, and runs the existing Cobalt service
installer and health check. It verifies Microsoft/Docker installer signatures and
the published GitHub release SHA-256 digests for WSL and MinGit. It does not need
winget or install an Ubuntu distribution, Git Bash, or a Linux desktop. Existing
native Vortex packages are checked and only installed when missing.

A **restart-required** result is a pause, not installation failure. Feature states,
Windows restart markers, and a saved boot identifier prevent starting Docker before
the reboot. Save other work, restart Windows, sign back in, and rerun the same block.
The script never reboots automatically or changes BIOS settings. If virtualization
is disabled, enable AMD SVM/Intel VT-x in firmware first. An older `wsl.exe` printing
its help text for `--update` or `--version` does not mean modern WSL is installed.
WSL queries request UTF-8 output and match only the runtime's version, including
localized labels. Ambiguous output stops prerequisite provisioning instead of
starting an MSI installation. A known old runtime or legacy help response can
still trigger installation. MSI progress uses an indeterminate status message;
verbose installer logs stay in the protected `downloads\vortex-windows-setup`
folder. A timeout does not kill the MSI client or claim rollback. A persistent
marker prevents another WSL MSI attempt on the same boot when completion is
unknown; Windows Installer exit 1618 reports another installation in progress.

Docker settings are backed up, unrelated settings are retained, and Docker is set
to use WSL 2, start at this user's **Windows sign-in**, and keep its dashboard closed.
Applying those settings briefly restarts Docker Desktop and its existing containers;
run this maintenance outside a live service. Engine requests explicitly target the
local `desktop-linux` context, never an administrator's saved remote Docker endpoint.
Neither unattended Windows sign-in nor Docker startup before sign-in is configured.
Close PowerShell after success; the existing Vision task, Funnel and Docker continue
in the background. Do not quit Docker from its tray menu if Cobalt should stay available.

The bootstrap does not impose a global WSL CPU/RAM limit or change limits for other
Windows apps. The Cobalt containers retain the limits below; native FFmpeg workers
are separate Windows processes. A first-run Docker dialog, blocked download, invalid
publisher signature, or engine startup timeout stops setup with a readable message.
No Windows/Docker deployment was performed by the automated installer tests.

If Docker is already running and only Cobalt setup needs to be retried, add
`-ServicesOnly` to the bootstrap command:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VortexBootstrap -ServicesOnly
```

This mode verifies the local `desktop-linux` context uses a Windows named pipe
and that its daemon reports Linux, then reuses the existing service installer.
It does not call WSL, run Windows/Docker installers, change Docker settings, or
stop Docker Desktop. An unhealthy engine causes a short, actionable stop without
modifying Windows. It can be used after a WSL MSI timeout if Docker still works;
it neither cancels nor declares that separate MSI transaction complete. Existing
Docker startup preferences remain in effect. PowerShell can be closed after the
service health checks succeed; keep Docker running and the PC awake. The native
processor activation can still briefly restart Vision, as with the normal setup.

The lower-level service actions remain available when the prerequisites are
already installed:

After installing the reviewed processor and native tools, with **Docker's Linux
container daemon running** and Git available to the administrator:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VortexSetup -Action InstallVortexServices
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VortexSetup -Action CheckVortexServices
```

`Vortex-Services.ps1` itself checks prerequisites without changing Windows features,
clones the audited Cobalt commit, builds its upstream Dockerfile, starts Compose,
checks API health/commit **from the Windows host**, then activates the server configuration through the
existing update/rollback path. Docker/WSL installation is a prerequisite, not an
unattended OS change. The API is bound only to `127.0.0.1:9000`. Containers have
restart-on-daemon-start behavior, CPU/memory/PID limits, read-only root filesystems
and no request logs or account cookies. Docker must start with Windows for the
sidecar to be available after reboot. `CheckVortexServices` reports service state;
`docker compose --project-name vision-vortex-services --file <serviceDirectory>\vortex-services.compose.yml restart`
restarts it. A Docker health check reports an unhealthy process; request workers
exclude it until the service recovers. Failed activation brings back the previous
Compose configuration when one exists.

The egress proxy's three public Python modules are copied into its image during
the elevated build, with explicit read permissions for the unprivileged container
user. No runtime Windows bind mounts or changes to Vision's directory permissions
are needed. This avoids Docker Desktop's `CreateFile ... vortex_network.py: Access
is denied` error when its normal user cannot read the administrator-only service
folder. After this error, rerun the corrected installer; the existing native
engines remain available and previously downloaded Docker layers can be reused.

Cobalt shares a dedicated network namespace with a small firewall container.
The namespace remains attached **only** to the Docker internal engine network.
Its IPv4/IPv6 OUTPUT policy permits loopback, established replies and the exact
egress proxy port only, including blocking the host bridge gateway. Cobalt has
no NET_ADMIN capability. A small CONNECT proxy is the only outward TCP path;
it rejects private/mixed DNS results, pins the checked IP, restricts ports to
80/443, and bounds connections, transfer bytes and time. Anything ignoring the
proxy fails closed. The egress proxy is not published to the host.

A fourth, small **ingress relay** solves Docker's internal-only publication
limitation: Docker can accept `ports:` on an internal-only network yet omit the
actual host mapping, leaving every container healthy but Windows unable to connect.
Only this relay joins a dedicated normal `ingress` bridge and publishes the API
on **host loopback** `127.0.0.1:9000`. It forwards TCP to one fixed destination,
`network-policy:9000`; it is not a general proxy and cannot take a destination
from a submitted URL. Cobalt listens on `0.0.0.0` **inside** its isolated namespace,
not on the Windows LAN/public interface. The relay runs unprivileged, read-only,
with no added capabilities or request logging, at most 16 forwarding children,
120-second idle timeout, 32 MiB memory / 0.25 CPU / 32 PID limits. It adds no
external route to Cobalt, including during daemon startup when Docker restart
policies do not honor Compose health dependencies. No Windows firewall change,
public extraction API, account cookies or runtime host mount is needed. Container
base images/dependencies still need routine administrator updates; the Cobalt
source itself is commit-pinned.

After Compose reports healthy containers, setup separately checks the published
API with bounded retries, no Windows HTTP proxy, no credentials and no redirects.
It validates the pinned commit before activating Vortex. A host connection failure
still rolls back instead of advertising a working engine. The full-stack Linux
Docker regression builds the pinned upstream service, checks its actual published
loopback mapping and host API response, and tests that the namespace firewall
continues to block direct connections. This is not proof of a live FUPCJ deployment
or of any provider's media availability; the post-install Windows health check
and a real Vortex download remain necessary.

Cobalt does not return a rich inspection object. Its adapter downloads and probes
a bounded real file for inspection, and reuses that file if it wins the subsequent
race. This can cost more bandwidth than native metadata inspection. Picker and
local-processing responses are deliberately rejected rather than guessing a media
item or running remote FFmpeg inputs. Some providers require unavailable accounts
or service features; other adapters remain available in those cases.

### FxEmbed and explicitly authorized endpoints

The inspected FxEmbed upstream now has a Docker/Workers-runtime recipe, but its
runtime does not share Python's network guard or Cobalt's confirmed proxy path.
Its example configuration also includes external services and optional credential
infrastructure. Automatically starting that recipe would weaken Vortex's network
and privacy boundary. This PR therefore supplies the API adapter but does **not**
activate a native Windows or public FxEmbed service. Administrators must first
provision a service with public-only egress and no external relays/credential
sources, then configure it explicitly. This limitation is intentional.

Example protected `config.json` fragment for an already secured local instance:

```json
{
  "vortex": {
    "services": {
      "fxembed": {
        "enabled": true,
        "url": "http://127.0.0.1:8787/",
        "apiHost": "api.fxtwitter.com",
        "publicEgressOnly": true
      }
    }
  }
}
```

`publicEgressOnly` is an administrator assertion about a separately enforced
policy, not a switch that creates a firewall. `apiHost` selects the local Workers
API realm; it does not send traffic to the public FxTwitter service. A public
HTTPS service additionally requires `allowExternal: true`, explicitly authorizing
sending normalized public links to that instance. Neither adapter currently sends
service API keys or source-account cookies. Non-loopback private service origins,
URL credentials, API redirects and arbitrary local tunnel targets are rejected.
Returned public media is validated through the normal socket guard; Cobalt local
tunnels are restricted to the exact configured origin and `/tunnel` path.

### Audited upstream sources (2026-10-09)

- [Cobalt API and service deployment](https://github.com/imputnet/cobalt/tree/a636575b09de1fc55d9b8cd98cac88f5f2f16b42): v11.7.1, documented service list, `POST /`, `GET /`, tunnels, proxy settings and Dockerfile.
- [FxEmbed source](https://github.com/FxEmbed/FxEmbed/tree/f3d17f0484c2c9a3ed7ff8a14dde6d004051ff52) and [API documentation](https://docs.fxembed.com/api/introduction/): v2 `/2/status/{id}`, API schema and Docker/Workers runtime.
- [twitter-video-dl](https://github.com/inteoryx/twitter-video-dl/tree/35a24a8e432f2f247c5cccfaa47b2f321c612fdf): reviewed token lookup, broad regex selection, repost recursion and downloader behavior.
- Native adapter checks used yt-dlp 2026.08.19 and gallery-dl 1.32.16.

## Storage and job lifecycle

Jobs are committed to the existing SQLite database before acceptance. A single
Vortex supervisor processes that durable queue independently of any browser. On
restart, interrupted jobs are requeued, with a bounded three-attempt restart
policy. Cancelling stops the extractor process and its descendants; deleting an
active item requests cancellation and removes its output.

Completed files receive an absolute expiry exactly five days after completion.
File access stops at that deadline, even before cleanup runs. Cleanup runs at
startup and periodically during work, including retries when Windows still has
a file open. Download history remains until you remove it. Completed inspection
receipts are pruned after 24 hours; see the other limits below.
Processor-update rollback snapshots exclude Vortex media so those snapshots do
not keep additional copies past expiry. Other project data and the shared
database remain included in the existing backup process.

| Limit | Default |
| --- | --- |
| Completed file retention | 5 days from completion |
| Download history retention | Until explicitly removed |
| Completed inspection receipts | 24 hours |
| History rows per account | 500 |
| Active jobs per account / server queue | 3 / 20 |
| Downloaded artifact | 2 GiB |
| Account / total Vortex storage | 10 / 40 GiB, including reserved jobs |
| Media duration | 2 hours |
| Gallery contents | 50 files |
| Search page size / maximum pages | 8 videos + 8 songs / 50 pages |
| Lookup / download timeout | 3 / 45 minutes |
| Parallel adapter inspections / downloads | 5 / 2 within one durable job |
| Per-attempt memory / event output | 2 GiB / 1 MiB |
| Aggregate temporary disk | Existing 3 × 2 GiB allowance, shared across attempts |
| Per-attempt lookup / download timeout | 2 / 40 minutes |
| File delivery URL lifetime | At most 5 minutes and never beyond file expiry |

Paths live under the configured `dataDir/vortex/`, using generated job IDs and
server-chosen filenames. Job IDs alone grant no access: every history, update,
delete, cancel and delivery-ticket request checks the verified Firebase owner.
Delivery URLs grant temporary access to one file and should be treated as
private. Deleting or expiring an item revokes existing URLs immediately.

Extractor subprocesses restrict outgoing destinations to public HTTP(S), reject
private and local DNS results, validate connection targets, and disallow
unguarded external network downloaders. FFmpeg works only on local files.
Resource limits bound downloads and working files without changing other
Vision processing limits.

## Verification and rollback

The `Test Vortex` workflow runs backend checks on Windows and Linux, plus real
Chromium interface checks with deterministic Firebase and media-server fixtures.
The browser tests cover 320px, 390px and desktop layouts, gate enforcement,
metadata/results, progress, history recovery, account changes, actions, menu
placement and reduced motion. Offline backend tests cover the durable queue,
ownership, idempotency, retention, cancellation, file tickets, byte ranges,
path checks, public-network boundaries and engine adapters.

### Verification for this upgrade

Offline regression checks include URL normalization and redirect SSRF rejection,
actual concurrent subprocess execution, winner selection, failed inspection and
download candidates, Max quality ordering, descendant cancellation, aggregate disk
limits, per-account/global quotas, normalized idempotency and diagnostic redaction.
Real local FFmpeg checks cover MP4/MOV and MP3/WAV/M4A plus corrupt-file rejection.
Existing shared Firebase/session, persistence, expiry, delivery and ownership tests
remain in the suite. The Windows CI job parses both installer scripts and runs the
same backend tests; Linux CI also validates Compose.

Live upstream checks on 2026-10-09 used anonymous requests through the development
environment's configured HTTPS proxy and its trusted system CA. This is **not** a
FUPCJ production-network test: direct DNS to X is unavailable in this workspace,
so Vortex's guarded public-network worker cannot perform an end-to-end live job
here. TLS verification remained enabled throughout the upstream checks. No public
third-party extraction API or user cookies were used.

| Supplied X post | yt-dlp 2026.08.19 | gallery-dl 1.32.16 | Self-hosted Cobalt 11.7.1 upstream API | Legacy twitter-video-dl |
| --- | --- | --- | --- | --- |
| `2104675740168155503` (`moviehub222`) | Matching post metadata, 9 formats, 240.266-second source | Matching tweet ID and MP4 media | HTTP 200, direct-media response | Guest lookup assertion failure |
| `2108340421264912569` (`shamarupdates`) | No usable result (not-found failure) | Extraction aborted | HTTP 400, `error.api.fetch.empty` | Guest lookup assertion failure |

The working post also completed a real 35,518,894-byte MP4 download and a full
FFmpeg decode in the upstream check (720 × 974, 240.326531 seconds). This verifies
that one public source file, not the Windows deployment.

X's own syndication endpoint returned a `TweetTombstone` and no media for the
second post. That does not establish whether the cause is deletion, account
restrictions or another availability rule. **No alternate engine was verified to
download the second post.** FxEmbed was not tested live because no authorized,
secured instance was configured. Mocked API responses are adapter-contract tests
only and are not presented as evidence of public-media availability.

The Cobalt API was started locally from the exact audited upstream source for
these checks; the Windows Docker/egress-proxy deployment was not exercised here
because Docker and the FUPCJ host are unavailable. The installer must be exercised
on FUPCJ with the reviewed commit before considering that sidecar deployed. Browser
regression execution was blocked by the available Chromium download returning an
invalid archive in this workspace; the existing browser CI job remains enabled.

These tests do not authenticate to the production PC or guarantee that every
upstream service accepts downloads from its IP. After activation, sign in to
Vortex and try a short public media item, leave and return while it runs, save
the finished file, then delete it. Confirm cross-device recovery with the same
account and isolation with a second account. Verify Save to Files and native
sharing on an actual iPhone.

Use the existing updater's `Rollback` action and selected backup directory to
restore the previous processor if activation fails. Vortex tables are additive;
do not remove the shared database or other Vision data during rollback. Revert
the Vortex change in GitHub to remove its page and navigation additions.
