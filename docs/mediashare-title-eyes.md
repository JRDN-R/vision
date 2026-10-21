# MediaShare and Livestream Manager 1.3.0

This coordinated update keeps the existing title-eyes model, persistent OCR
queue, scheduled-service engine, caption setup, and per-destination metadata
writers. It adds durable slide ownership, optional availability dates,
OBS-driven model readiness, and an unscheduled-service configuration path.

## Deploy together

1. Merge the Vision server change, then update FUPCJ Server with the existing
   `Setup-Vision-PC.ps1 -Action Update` workflow outside a broadcast.
2. With OBS closed, install the companion Livestream Manager 1.3.0 source ZIP
   using `INSTALL-FIRSTUPCJAX.cmd`. The installer builds the Windows executable,
   preserves ProgramData settings/history, and updates the stable Lua script.
3. Merge/deploy the companion `FirstUPC/media` change after the server and
   Manager support availability dates and reservations. Older clients do not
   enforce those guarantees. The native OBS button DLL remains version 1.1.6.
4. Verify a non-broadcast configuration and authenticated readiness status on
   the church PC. Repository publication alone does not update that PC.

## Slide receipt, eligibility, and ownership

MediaShare sends the image, `availableStartingDate`, and `targetServicePeriod`
in one multipart request
on selection. Waiting until **received and saved** confirms SQLite receipt;
Firestore publication and OCR continue independently if the browser closes.
The optional calendar control is labeled **Optional — Available Starting Date**.
Its date is a do-not-use-before date in `America/New_York`. A blank/missing date
means unrestricted; reaching the date does not expire the slide.

Choosing a Sunday exposes one AM/PM choice. `targetServicePeriod` stores `am`,
`pm`, or an empty string; a nonempty value requires a Sunday date. Existing
slides without this field stay compatible. Changing the date to a weekday or
clearing it also clears the hidden period. The date and period are included in
upload recovery, SQLite persistence, replacement, public drop responses, and
Firestore publication as one revision.

The Manager first prefers unused slides matching the selected service's Eastern
date and AM/PM period, choosing the newest matching upload. Date-only slides
still match their date. If no match exists it uses the newest eligible unused
fallback. A Sunday AM slide cannot be selected for that Sunday's PM service,
or vice versa; after that calendar date, an unused slide remains eligible as a
fallback. A future date still cannot be used early during preparation.

Reservation requests include `serviceStart`, an ISO timestamp with an explicit
offset. The server normalizes it to America/New_York, enforces the service/date
restriction from authoritative drop metadata, and stores it with the claim.
Targeted slides require this context, so old Managers cannot accidentally
consume the other Sunday period. Repeated claims retain their original image
and service association; a retry supplying a different start time is rejected.

The Manager selects the newest eligible unused slide without a same-day or
90-minute age cutoff. Server reservations verify authoritative metadata,
including legacy Firestore-only drops. SQLite transactions and a unique image
key prevent two services from claiming one image. Reservations survive failed
uploads, restarts, image deletion, and normal upload-metadata retention. They
are not silently released when a request has an ambiguous outcome. Claimed
Firestore image chunks are retained for retry even after replacement or deletion
of the drop; this increases persistent storage rather than discarding recovery
bytes while the service still owns them.

`media_share_assignments.py` exposes authenticated MediaShare reservation,
receipt, and lookup endpoints. Each claim records the service, drop, exact image
key, snapshot, and image/title receipts for each exact destination. A reservation
is distinct from successful use. Completed destinations are retained when the
other destination needs a retry. No candidate produces an explicit waiting
state rather than fallback to used content.

Existing automatic replacement by a newer eligible upload is preserved. A late
result from an older/replaced image cannot overwrite the current image's title.
The usage ledger preserves those earlier assignments. Source inspection cannot
reconstruct unrecorded historical use before this release; see the Manager's
release notes for migration and validation limits.

## Delayed title updates

The image can publish before text exists. The Manager continues checking its
pinned drop/image association after image success; it does not discard that
association merely because the image has been marked used. Title updates do not
repeat successful image uploads or overwrite descriptions, categories, tags,
privacy, video language, service dates, or unrelated Planning Center fields.
Planning Center updates run only when enabled for the service.

Queued/processing extraction, completed-without-text, completed-but-uncertain,
failed extraction, and a superseded/deleted source are separate outcomes. Empty
or uncertain output never invents a sermon title. Manual reviewed title
corrections remain supported. The OCR job ID and image key guard replacement
races on the server, and destination receipts guard repeated watcher updates.

## Model readiness while OBS is open

`title_eyes_standby.py` observes the co-located OBS process, independently of
websocket connectivity or broadcasting state. One shared process scan is
cached for five seconds. Temporary unknown process scans retain a previously
observed OBS session for up to two minutes; a confirmed exit releases it.
Server restart triggers fresh detection. This applies on every day, including
unscheduled services. No slide is analyzed simply to keep the model warm.

The authenticated standby response reports `obsActive`, `warmReason`, actual
`loaded` readiness, phase, load count, and the explicit Eastern timezone. A
spawned process is not reported ready until startup has completed. MediaShare
drop responses include the bounded `worker` snapshot that its existing frontend
expects, including OBS readiness; model paths and administrative access are
not exposed. This incorporates the readiness work proposed in PR #111. A memory,
maintenance, configuration, or active-work blocker remains visible.

The existing exclusive local-model lease is retained: idle Venture Ultra yields
to OBS readiness, an active answer finishes, and new competing local-model work
waits while OBS stays open. This avoids loading two models at once. It means
leaving OBS open also defers that queued work. Existing title protections remain:
CPU-only inference, four threads, below-normal priority, a 4 GiB resident cap,
and at least 10 GiB available-memory headroom. These are configured protections,
not measured RAM consumption or a promise of readiness under insufficient RAM.

When OBS closes, normal Sunday/Wednesday standby and off-day on-demand behavior
resume. The existing October 19–20, 2026 exception remains date-limited. All
service/date decisions use `America/New_York`, including DST; `tzdata` supplies
that timezone on Windows.

## Validation boundaries

Automated tests use mocked destinations/model processes and temporary databases.
They exercise date eligibility, reservation contention, partial retries,
restart recovery, delayed titles, OBS residency, and scheduled-service/caption
regressions. They do not create broadcasts, edit service records, benchmark the
real model, validate production Firebase rules, or prove YouTube Studio selectors
against a live signed-in browser. Windows build, OBS UI, real model readiness,
and deployment verification remain installation checks. The companion package
contains the exact test report and changed-component list.
