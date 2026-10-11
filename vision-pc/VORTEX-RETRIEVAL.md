# Original-link retrieval corrections (proposed v1.0.3)

This extends the signed-Instagram-MP4 work in PR #45. It does not require a user
to visit a different downloader first. It is not a promise of universal access.

## PR #45 completion audit (October 11, 2026)

The original X URL is `https://x.com/iam_scholar/status/2109036692065649078/video/1`.
The source reports a 8,700.672-second video. Its post ID and attached media ID
are different identifiers; comparing them alone was not evidence of wrong media.
The live test at `c8a9d1520e31940c66923a025cdc1c56cd2ce9a7` downloaded and
fully decoded its 8,700.715-second, 864x360 MP4 through gallery-dl in 134 seconds
(437,697,911 bytes). Its SHA-256 was
`537a388db278bce717a09d9f8fb30342148c677d48d02965e7b9d835cd71d814`.
The original public Instagram Reel `Chunk8-jurw` also downloaded and passed
full-file decoding. Latest per-platform outcomes and CI links are kept in PR #45.

Two other original-URL probes remain limited by the source: YouTube
`YE7VzlLtp-4` requires sign-in from the test runner even with Deno and EJS
installed. Vimeo `76979871` resolves through the same-ID player fallback, but
yt-dlp's native HLS downloader detects DRM in its selected manifest and refuses
the transfer. The latter was reproduced inside the guarded worker and traced to
the upstream HLS DRM check, not a file-size, memory or packaging failure. DRM
rejection now has its own redacted category. Neither probe is counted as a
successful download; the live workflow continues to show these failures.

The remaining implementation defects corrected in this investigation are:

* **Whole-file selection:** yt-dlp and gallery-dl could select a high-bitrate
  stream that exceeded 2 GiB, even when smaller complete variants were available.
  yt-dlp now budgets the combined audio/video streams using exact sizes or
  duration/bitrate estimates, with headroom for estimates and muxing. Unknown
  sizes remain eligible under the existing actual-byte checks. It never silently
  replaces an oversized video with audio. The returned dimensions describe the
  selected stream, and yt-dlp adds a note when size constrained its selection.
* **X transport:** prefer a progressive MP4 over HLS at the same resolution/fps.
  gallery-dl and FxEmbed also select a fitting progressive variant. This avoids
  thousands of fragment requests when the same attachment has a suitable MP4.
* **Fallback deadlines:** direct/page, Instaloader, Cobalt and FxEmbed adapters
  that must download to inspect now receive a transfer slot for download jobs.
  They no longer have to finish a large file under the metadata deadline first.
  Pending candidates receive shares of the remaining overall transfer deadline.
  Small and Balanced race verified streams within their height caps; a slower
  higher-resolution HLS stream no longer blocks a fitting progressive fallback.
  Max retains resolution priority. Alternate recipes from the same native engine
  follow independent primary candidates rather than monopolizing transfer slots.
* **Temporary storage:** near-limit transfers are serialized when simultaneous
  input/output files would exceed the unchanged shared budget. Smaller transfers
  can still race two at once. The first verified success cancels all other work.
* **Identity and completeness:** X attachment numbering includes photos. Native
  yt-dlp status data must match the requested post before quoted media is removed;
  syndication retains only that post's original ordered attachments. Direct
  adapters preserve a provider-supplied duration through probing/conversion, so a
  shorter file cannot redefine its own expected length and pass verification.
* **Registry routing:** gallery-dl's installed registry now receives public hosts
  beyond the old allowlist, including recognized platform labels, without the
  collector rejecting them again. YouTube and Spotify remain dedicated video/
  music routes rather than potential thumbnail galleries.
* **Vimeo:** a failed anonymous API lookup can try the same numeric ID through
  Vimeo's public player. The returned ID must match; this does not add login,
  account cookies, private-video access, or a new remote service.
* **Diagnostics:** file size, duration, memory, temporary disk, event-log size and
  timeout now have separate redacted categories. Terminal policy failures retain
  an actionable message. Native failures retain their category when converted
  to a safe message, including a rejected Vimeo player lookup. Stream progress
  is throttled independently of transfer
  speed. No signed media URLs, cookie values or raw provider errors are logged.
* **Installation/probes:** specialist checks import Instaloader and yt-dlp EJS,
  confirm all native engines after activation, and require FFmpeg, ffprobe and
  Deno >=2.3. The live runner installs Deno and allows the production 45-minute
  transfer deadline instead of the previous four-minute timeout. Its old removed
  YouTube fixture was replaced with the upstream Big Buck Bunny fixture.

### Engine and installation audit

| Engine | Runtime / activation | Boundaries and remaining constraints |
| --- | --- | --- |
| yt-dlp | Isolated `yt-dlp[default]`, EJS, FFmpeg/ffprobe; Deno for YouTube | Native HTTP/HLS/DASH only; no external network downloader or source cookies; whole-file byte selection and local FFmpeg processing |
| X syndication / legacy | Same isolated yt-dlp; separate extraction recipes | Original post/attachment identity; provider APIs can still reject requests |
| gallery-dl | Isolated package, no machine/user configuration loaded | Registry routing, bounded queues/items/bytes; X progressive variant selection; each video in a ZIP is verified |
| Instaloader | Isolated package, no saved login session | Original Instagram shortcode and explicit/unambiguous video; provider throttling/authentication can still prevent access |
| page-media / instagram-direct | Built in; requests and FFmpeg/ffprobe | Guarded public redirects, MIME/body bounds, explicit page declarations; expiring CDN signatures cannot be renewed from a CDN URL alone |
| Cobalt | Separately configured service; pinned Docker stack for local installation | Fixed-origin tunnels/public egress; picker and progressive merges supported; local HLS/subtitle/cover processing remains unsupported |
| FxEmbed | Explicitly configured local or authorized external service | Exact post ID, one selected progressive video; hosted service availability/authentication is outside this repository |
| spotDL | Isolated package; optional administrator Spotify API credentials | Official metadata API and matched YouTube audio; not the original Spotify stream; no source-account cookies or curl-based free-provider bypass |

### Unchanged limits and protections

* 2 GiB maximum output / media transfer budget; four-hour source duration ceiling.
* 3x the file cap for job temporary storage; 2 GiB resident memory per adapter
  process tree; 1 MiB adapter event log and 4 MiB supervisor event log.
* At most three parallel metadata inspections and two parallel transfers,
  further restricted by space reservations; 180-second top-level inspection and
  45-minute top-level download deadline, with earlier adapter deadlines.
* Full local FFmpeg decoding, local-only demuxer/protocol allowlists, DNS/socket
  SSRF checks, source-cookie prohibition, account ownership, cancellation,
  storage quotas and five-day download retention remain enabled.

The supplied history contains signed Instagram/competitor download endpoints,
but no original Instagram post URL for those exact two videos. They cannot be
honestly listed as verified original-post reproductions. The Instagram live
fixture is explicitly a different, public original Reel used by upstream tests.

## Corrections

* Preserve X `/video/1` and every other explicit attachment selection, including
  `/photo/N`. Previously the normalizer dropped the first selector and converted
  non-first photo selections into video selections. yt-dlp uses this selector to
  distinguish a selected video from a whole-post/playlist lookup.
* Try yt-dlp's documented `syndication` and `legacy` X extraction recipes as
  independent bounded attempts in addition to its default method. They reuse the
  original worker, not a second download pipeline or a fake downloader wrapper.
* Add Instaloader for original Instagram post/Reel URLs. Match the requested
  shortcode, select only one unambiguous or explicitly selected video, and pass
  the real media through the same guarded transfer and local verification.
* Add a general page/direct-media adapter for explicit OpenGraph/HTML media and
  video/audio HTTP responses. An endpoint need not end with `.mp4`. Social-page
  metadata must affirm the requested post identity; multiple/ambiguous videos,
  login-page metadata and unrelated canonical links do not silently win.
* Let the configured Cobalt instance decide its supported providers, instead of
  refusing everything outside six hard-coded platform names. Handle single-video
  or explicitly selected picker results and bounded progressive local-processing
  tunnels. Separate streams are downloaded first and merged from local files.
* Remove FxEmbed's per-request OpenAPI dependency and permit an explicit first
  video. Still require the exact returned post ID; never follow quoted media as
  a substitute. FxEmbed remains an explicitly configured service.
* Queue up to three inspections at once and two downloads at once. More than five
  eligible methods no longer cause immediate failure. Deadlines reserve time for
  later attempts; missing/unconfigured methods receive redacted internal reasons.
* Classify HTTP/service failures without retaining upstream error bodies, tokens,
  cookies, or signed media URLs in diagnostics. Close failed transfers and remove
  partial files. Keep other download candidates after a failed transfer.

## Deployment on an existing FUPCJ server

After this PR is merged, run the following in Administrator Windows PowerShell:

```powershell
$update = Join-Path $env:TEMP 'Update-Vortex-Retrieval.ps1'
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Update-Vortex-Retrieval.ps1' -OutFile $update
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $update -EnablePublicXFallback
```

The switch explicitly enables `https://api.fxtwitter.com/` as a public-X-post
fallback, with `allowExternal: true`. It sends public post IDs, not account cookies.
Omit it to keep all external services unchanged. An existing enabled/self-hosted
FxEmbed configuration is preserved rather than replaced. Existing Cobalt settings
are always retained. No new Docker installation is required.

The helper uses the existing isolated `InstallVortexTools` staging and rollback
path, updates the worker, refreshes yt-dlp/gallery-dl/Instaloader/spotDL, and checks
that those packages import. A configuration-only activation uses a protected
backup, an atomic file replacement, a conflict check, and the existing processor
restart/health check. Health is not proof that a particular source accepts access.
For a reviewed pre-merge test, fetch the helper from the reviewed full commit SHA
and pass that SHA as `-SourceRef`. GitHub Pages alone never updates Windows.

## Testing and limits

The normal Vortex CI still runs on Windows and Linux, with real FFmpeg export and
full-decode checks, account/storage/network isolation and browser regressions.
Additional deterministic cases exercise X selector preservation, real yt-dlp
recipe arguments, Instaloader shortcode identity, Cobalt provider/picker/local
merge handling, page identity, direct MIME media, private redirects, cleanup,
service-error redaction and the expanded bounded scheduler.

`Vortex public-source probe` is separate from deterministic CI. It attempts the
reported original X post with native methods and an explicitly authorized public
FxEmbed configuration **in the test runner**, not FUPCJ. It prints only a bounded
status report, per-engine categories, measured metadata and a file digest. It
prints no media URLs or content and deletes every downloaded file. A failed live
provider probe fails visibly rather than being reported as a passed download.

No source-account cookie collection, CAPTCHA solving, DRM removal, or automatic
use of private posts was added. A site can still deny access, require an authorized
session, impose rate/region restrictions, or change its extraction interface.
Cobalt local HLS manifests, subtitles/cover sidecars and ambiguous multi-video
pickers are not guessed or passed to remote FFmpeg; other adapters can still try.
SSSTwitter/TWMate/VideoDropper's unpublished extraction APIs are not scraped or
represented as verified backend dependencies. A supplied direct video endpoint
can work via its MIME response without creating a dependency on its extractor.

## Upstream contracts

* yt-dlp documented extractor arguments: https://github.com/yt-dlp/yt-dlp#extractor-arguments
* Cobalt API: https://github.com/imputnet/cobalt/blob/main/docs/api.md
* Instaloader module: https://instaloader.github.io/as-module.html
* FxEmbed API: https://docs.fxembed.com/api/introduction/
