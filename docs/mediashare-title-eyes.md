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

New drops use one JPEG, PNG, WebP, or still GIF slide, up to 10 MiB / 32 MP.
The first selected file is used; old multi-image history remains viewable.
Images use the exact title-eyes normalization (JPEG, maximum 1600 px edge).
Saving optional Text does not wait for OCR. **New drop** starts a separate slide;
choosing a photo while editing a saved drop replaces that drop's slide.

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

Warm standby remains Sunday/Wednesday, using the church PC local clock. MediaShare
also accepts uploads on other days: it loads the same model on demand, including
during a livestream, processes pending slides and unloads when the queue is empty.
These queue entries survive the day boundary and server restart (seven-day OCR expiry). The standalone title-eyes page retains its
existing off-day restriction.

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
