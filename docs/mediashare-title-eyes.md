# MediaShare + Livestream Manager 1.2.0

This integration uses the existing title-eyes model, prompt, image preparation,
title casing, persistent queue, and Sunday/Wednesday warm standby. It does not
introduce a second model or a cloud OCR provider.

## Install together

1. Merge the Vision server pull request and update FUPCJ Server using the normal
   `Setup-Vision-PC.ps1 -Action Update` command after the stream ends.
2. Apply the supplied MediaShare changes to `FirstUPC/media` and let GitHub Pages
   deploy. The current connected account has read-only access to that repository;
   connect an account with write access or create a fork before opening its PR.
3. Extract the 1.2.0 manager ZIP. With OBS closed, run `INSTALL-FIRSTUPCJAX.cmd`.
   Its existing installer builds the Windows executable and updates the stable
   Lua integration. It retains settings and backs up the existing installation.
   The native OBS button DLL stays at its independent, unchanged 1.1.6 version.
4. Confirm the church PC clock is Eastern Time with automatic DST. The Vision
   processor must be running. No MediaShare or title-eyes tab is needed for the
   scheduled model or accepted jobs to continue.

## Upload and editing

A new image is immediately sent to FUPCJ Server. Once the page says **received
and saved**, its sanitized slide and OCR job are committed to SQLite. The server
publishes the slide to the existing MediaShare Firestore `drops` collection and
continues OCR even if the browser closes. Closing the page **before the upload
is received** cannot finish sending bytes that never reached the server.

New drops use one JPEG, PNG, WebP, or still GIF slide, up to 50 MiB / 32 MP (standalone title-eyes retains its 10 MiB limit).
The first selected file is used; old multi-image history remains viewable.
Images use the exact title-eyes normalization (JPEG, maximum 1600 px edge).
Saving optional Text does not wait for OCR. **New drop** starts a separate slide;
choosing a photo while editing a saved drop replaces that drop's slide.

The title worker first checks for **actual visible writing** anywhere in the slide, including tiny, decorative, rotated, partly obscured, and non-Latin characters. It must not convert a wordless image's subject or artistic style into a title. An explicit zero-character finding forces a blank Auto Title even if the model also invents a descriptive phrase. Difficult-to-read text is not treated as absent; a genuinely unreadable or ambiguous headline remains marked for review. The server publishes the image normally without sending a fabricated title to downstream destinations.

Auto Title sits between the preview and Text. It is read-only until the model
finishes. The pencil then opens editing and becomes **Save**. Corrections are
saved explicitly and preserve the editor's casing. An old poll result, old OCR
job, or stale edit cannot replace a newer image/title revision. Model failures
leave a clear retry-by-replacement message. Uncertain/no-title outputs are
editable when the model completes; they do not overwrite livestream titles
until a readable, reviewed title is saved. Titles over YouTube's 100-character
limit also require a correction before publication.

The browser remembers the last accepted drop (or upload request ID) for recovery.
Firestore publication retries independently when the network fails. The API
reports receipt separately from publication. A retained upload is not reported
as already published to YouTube or Planning Center.

## Schedule and OBS

**Temporary October 19–20, 2026 override:** after this server update is installed,
the title model preloads without waiting for a slide and remains warm on Monday
and Tuesday, using the church PC's Eastern local clock. At 00:00 Wednesday,
October 21, the exception expires and the regular all-day Wednesday schedule
continues with the same loaded process. Thursday returns to on-demand titles;
future Mondays/Tuesdays are unchanged. The authenticated title standby status
includes `temporaryWarmup` and `temporaryWarmupEndsLocal` alongside `loaded` and
`phase`, so scheduled intent can be distinguished from actual readiness.

During this exception, idle Venture Ultra yields the shared inference lease to
the enabled title worker. Active Venture answers still finish, and other accepted
local jobs retain their existing scheduling. The title worker resumes standby
when that work releases the slot. Memory limits, maintenance, and shutdown rules
still apply. This does not run both models in RAM simultaneously.

GitHub publishing alone does not change the running Windows processor. Install
the change with the normal `Setup-Vision-PC.ps1 -Action Update` workflow, then
verify `/api/title-eyes/standby` in an authenticated Vision session reports
`loaded: true` and `phase: "Ready"`. Public `/api/status` confirms reachability
only; it cannot prove that a model is loaded.

Each authenticated MediaShare drop response also includes a bounded `worker`
snapshot: `loaded`, `ready`, `scheduled`, `temporaryWarmup`,
`temporaryWarmupEndsLocal`, `phase`, and `loadsSinceRestart`. `ready` requires a
live process and the `Ready` phase; merely starting a process is not readiness.
The existing drop polling refreshes this status without exposing Vision's
administrative routes or model paths. This lets MediaShare distinguish a loaded
model from scheduled intent, and compare reload counts while troubleshooting.
It does not report inference percentage. The browser's 0–100% progress remains
explicitly estimated during analysis and reaches 100% only after completion.

Warm standby remains Sunday/Wednesday, using the church PC local clock. On all
other days, both MediaShare and standalone title-eyes accept uploads: the worker
loads the same model on demand, including during a livestream, processes pending
slides and unloads when the queue is empty. Pending titles are not cancelled
when Sunday/Wednesday ends. Queue entries survive a server restart (MediaShare
jobs have a seven-day OCR expiry; standalone title-eyes jobs expire sooner).

The title worker now permits OBS to remain open. It retains CPU-only inference,
four threads, below-normal process priority, a 4 GiB resident-memory limit and at
least 10 GiB available system-memory headroom. Accepted MediaShare titles get the
next available shared inference lock; no second model is launched alongside an
existing local worker. Heavy Vision/Venture workers retain their existing rules.
This is not a claim of measured livestream performance or 0.8 GB RAM usage.

## Watcher and destination updates

OBS Lua still launches the hidden manager watcher for the active stream. The
watcher now stays alive after its first image succeeds. It independently tracks
image keys, title values/revisions, and the current Planning Center episode.
Images can publish before titles; pending titles never erase current service
names. Manual corrections and replacement slides are detected for the duration
of the active session. Successful destinations are not resent when the other
one needs a retry. New titles preserve YouTube descriptions, category, tags,
languages, privacy and broadcast times; Planning Center updates preserve Live
dates and video links.

The manager holds one authenticated long-poll request to FUPCJ Server. New server
revisions wake it immediately. Firestore is reread on wake and at the normal
15-second fallback interval, recovering missed notifications or server restarts.
This is not a browser-to-OBS connection. Existing stop/session/watch-token guards
remain in use. A title revision is always tied to its exact image key.

## Access and retention

Only signed Firebase tokens issued for `fupc-media` are accepted on
`/api/media-share/*`. Anonymous sign-in is intentional: this is the existing
shared MediaShare access model. These tokens cannot open Vision accounts or
other Vision APIs. Browser CORS is limited to `https://firstupc.github.io` on
these routes. No admin credential or server secret is added to the webpage.

The background publisher creates its own anonymous Firebase session and stores
its refresh token only under the server data directory. It writes the same
`drops`/`chunk_*` documents as the current web app. Existing Firestore rules must
permit these authenticated operations; if they do not, the outbox reports the
failure and retains the upload instead of bypassing the rules.

Uploaded bytes are removed from the outbox after successful publication and from
OCR after completion. Old replaced chunks are deleted after the new metadata
publishes. Server editing metadata is retained for 30 days; Firestore history
remains. Limits: 3 queued titles per uploader, 20 global queued/active titles,
100 recently created/replaced drops per hour and 500 retained server drops.

## Validation limits

Offline tests cover signed project/route isolation, closed-browser/server-restart
continuation, title locking, stale revisions, image replacement, outbox retries,
scheduled standby and off-day on-demand loading, wake notifications, destination retry/idempotency, metadata
preservation, and existing livestream behavior. They do not run a real OBS
broadcast, contact YouTube/Planning Center, measure model RAM/latency, or verify
production Firestore rules. The Windows executable is built by the included
installer on the church PC; it was not built or run in this Linux environment.
