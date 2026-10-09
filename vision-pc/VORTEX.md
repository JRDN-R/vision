# Vortex

Vortex runs at `https://jrdn-r.github.io/vision/vortex/`. Its page and modules are
independent of the Vision board and Venture conversation interface. The only
changes to those interfaces are their two Vortex links and release notes.
Opening Vortex in the same tab saves pending board changes first.

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
YouTube; prefix a Spotify search with `spotify:` (for example, `spotify: artist song`).
Select a result to load actual source properties, choose Small,
Balanced or Max, then Download. Source details remain blank until supplied by
the engine. Unknown progress stays indeterminate; a percentage appears only
when a stream's byte count or fragment count is known. Percentages describe the
current stream or gallery file, not a fabricated estimate for the whole job.

Small prefers source video up to 480p, Balanced up to 1080p, and Max the highest
available source quality. Audio downloads retain the best available source
audio without a default lossy compatibility conversion. Video/audio streams
may be packaged in MKV to preserve their original codecs. The actual resulting
format appears in history; device playback support depends on that format.

Open an item's overflow menu, or hold the item on a touch screen, to save,
share, cancel, download again or delete it. Native file sharing is offered when
the browser supports it and the file fits its in-memory sharing limit. Preparing
the file and tapping Share are separate steps so iOS receives a fresh user
gesture. Save file uses a short-lived URL and the browser's download/Save to
Files workflow, without holding a large video in page memory.

## Engines and scope

| Input | Engine | Behavior |
| --- | --- | --- |
| YouTube searches and supported public video/audio URLs | yt-dlp | One media item, native download, local stream packaging |
| Supported public gallery/post URLs, including Instagram, Reddit, Imgur, Flickr, DeviantArt and Pixiv | gallery-dl | One file or a ZIP for a bounded collection |
| Spotify track URLs and `spotify:` searches | spotDL + yt-dlp | Spotify metadata matched to public YouTube Music/YouTube audio |

Spotify downloads are **matched audio**, not Spotify's original stream; the
interface identifies this explicitly. Availability and matching depend on the
source services. DRM content, logged-in sources requiring cookies, live streams
and unbounded playlists are not supported. No account cookies, Firebase token
or saved model API key is passed to the media engines.

Spotify metadata uses the official API. If the engine's default application is
rate-limited or unavailable, the PC administrator can configure
`VORTEX_SPOTIFY_CLIENT_ID` and `VORTEX_SPOTIFY_CLIENT_SECRET` in the Windows
service environment and restart the processor. Those optional credentials are
sent only to the isolated Spotify worker and never returned to the browser.

The source of truth for engine availability is authenticated
`GET /api/vortex/capabilities`. A missing engine produces a specific installation
message rather than pretending a source was downloaded successfully.

## Storage and job lifecycle

Jobs are committed to the existing SQLite database before acceptance. A single
Vortex worker processes that durable queue independently of any browser. On
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
| Lookup / download timeout | 3 / 45 minutes |
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
