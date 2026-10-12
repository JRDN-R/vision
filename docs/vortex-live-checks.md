# Vortex live-download checks

The public-source workflow performs a real download and full-file verification.
Metadata, an available format, or passing offline tests do not count as a
successful download. A provider restriction remains a failing live check.

## Verified on 12 October 2026

[Run 38262729461](https://github.com/JRDN-R/vision/actions/runs/38262729461)
tested the original URLs after PR48 merged:

| Source | Result on GitHub's runner |
| --- | --- |
| YouTube | Sign-in/bot-verification challenge before extraction. |
| X | Metadata returned; actual media transfer rejected with HTTP 403. This does not establish that login would fix it. |
| Vimeo | Metadata returned; selected HLS stream contains FairPlay DRM signaling. |
| Instagram | Complete download and verification passed. |

These observations apply to that runner and those URLs. They do not establish
whether another public item works from FUPCJ. Vortex retains its network guard,
DRM refusal, file limits, source identity checks, and full-file verification.
The new error messages describe the restriction after alternatives are exhausted.
The diagnostic output contains fixed reason codes, never signed URLs, cookies,
keys, or response bodies.

## GitHub approval status

The bot-generated PR48 runs expired without required approval. GitHub rejected
an API retry of the expired Vortex run. PR49's source checks started normally;
no repository approval/security setting was changed. Old expired runs remain
historical results and are not evidence of the new revision's test status.

## PC update

This change requires no new model, downloader dependency, or PowerShell installer.
Do not interrupt an active Qwen installation to investigate these CI results.
An application code update is needed only to receive the improved error messages
after reviewing/merging this change. The existing `Update-Vortex-Retrieval.ps1`
can refresh Vortex code/tools but can restart Vision; schedule that separately.
Updating packages alone is not a verified remedy for these provider restrictions.

When testing from FUPCJ, use the same original public URL in Vortex and record
the returned reason. Do not copy source-account cookies, signed CDN links, or
private content into GitHub reports. An original unprotected media file can
still be imported through the existing file workflow.
