# Original-link retrieval corrections (proposed v1.0.3)

This extends the signed-Instagram-MP4 work in PR #45. It does not require a user
to visit a different downloader first. It is not a promise of universal access.

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
