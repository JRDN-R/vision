# Status modules and Vortex reporting

`activity.html` mirrors `JRDN-R/vision-status/index.html`. Keep both pages in sync when changing the owner dashboard.

The dashboard opens on a compact Overview. Horizontal tabs select Vortex, Transcriptions, Gemini, Logins, or Activity. The global account selector scopes activity, Gemini usage, and Vortex jobs; Gemini approval controls remain account-wide administrative actions.

## PC deployment

Merge the companion dashboard PR and this processor PR, then update the existing PC with the normal `Setup-Vision-PC.ps1 -Action Update` workflow. The installer already includes all changed processor modules. No model installation or new public access configuration is needed. The existing `activity-admins.json` owner allowlist still controls all administrative reads and mutations.

New additive audit tables record the verified authentication provider and the first/last observed request to each app. They preserve existing users, session counts, and events. A shared Firebase UID remains one account, and an existing shared session is not counted as a second login when it first reaches Vortex. Historical providers are unknown until observed again; they are not inferred from older `google_sign_in` event names.

## Reporting contract

- `GET /api/admin/activity?module=all|logins|transcriptions|vortex&user=<hashed-id>&limit=100`: module filtering happens before the event limit; totals and pagination use the same filters. The response includes module counts and provider/app metadata. Omitted `module` retains the existing behavior.
- `GET /api/admin/vortex?user=<hashed-id>&limit=100`: owner-only, read-only job metadata, totals over all matching retained jobs, newest-first pages up to 2,000 rows, and expiration-aware ready-file sizes. An older PC without the Vortex table returns `available: false`.
- `GET /api/admin/accounts/<hashed-id>`: owner-only deletion preview (the same hashed directory identifier as activity, never an arbitrary Firebase UID). Returns counts and whether current jobs block deletion.
- `POST /api/admin/accounts/<hashed-id>/delete` with JSON `{"email":"exact-account-email","confirmation":"DELETE"}`: owner-only permanent removal of the Firebase Auth account and its local Vision, Venture, Vortex, context, and media assets. Owner/admin targets and active jobs are rejected.

The Vortex report reads existing jobs. It excludes deleted jobs and never selects URLs, search queries, media titles, filenames, tickets, error output, or credentials. Ready downloads are not device-save counts. Request and worker audit records use fixed event names and allowlisted fields.

Frontend reads are authenticated, uncached, and discarded after account changes. Loss of owner authorization at any admin endpoint clears all private panels. Partial Vortex outages show an explicit unavailable/stale notice while the existing activity view remains usable.

## Deletion prerequisite and scope

Account deletion is **off by default**. Configure `firebaseAuth.adminServiceAccountPath` in the existing protected `C:\\ProgramData\\VisionPC\\config.json` to the **absolute path** of a private Firebase Admin SDK service-account JSON for the same Firebase project. Do not commit or serve this JSON, and restrict it to the service administrator. The server checks the explicit Google UID allowlist in `activity-admins.json`; Firebase ID tokens, sign-in names, or general access tokens alone cannot authorize this operation.

The backend first verifies the preview, exact email/DELETE confirmation, and that the account is not running jobs. It writes a permanent local tombstone before calling Firebase Authentication deletion to prevent any surviving ID token from recreating content. Local cleanup then removes account-owned Vision projects, Venture conversations, Vortex downloads, processed uploads/transcripts/documents, context index entries, encrypted account credentials, and audit metadata, leaving the tombstone. If cleanup is interrupted, the administrator can retry while the user remains blocked. Removal does **not** delete downloaded copies on other devices or guarantee deletion of independently retained external-service data. Take a protected backup before using this operation. Never attempt an actual account deletion in automated tests.

First/last names use verified Firebase given/family claims where present, falling back to the recorded display name. Missing names remain undisclosed as `Not provided`; sign-in methods are retained without guessing.

## Validation

```sh
python -m unittest discover -s tests -p test_activity_dashboard.py -v
python -m unittest discover -s vision-pc -p test_firebase_auth.py -v
python -m unittest discover -s vision-pc -p test_vortex.py -v
node tests/gemini-admin.test.cjs
python tests/activity_browser_smoke.py
```

Browser checks use mocked Firebase and HTTP responses; backend tests use SQLite and locally signed test tokens. They do not verify a live PC rollout or make real media downloads.
