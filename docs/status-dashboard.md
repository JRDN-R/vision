# Status modules and Vortex reporting

`activity.html` mirrors `JRDN-R/vision-status/index.html`. Keep both pages in sync when changing the owner dashboard.

The dashboard opens on a compact Overview. Horizontal tabs select Vortex, Transcriptions, Gemini, Logins, or Activity. The global account selector scopes activity, Gemini usage, and Vortex jobs; Gemini approval controls remain account-wide administrative actions.

## PC deployment

Merge the companion dashboard PR and this processor PR, then update the existing PC with the normal `Setup-Vision-PC.ps1 -Action Update` workflow. The installer already includes all changed processor modules. No model installation or new public access configuration is needed. The existing `activity-admins.json` owner allowlist still controls all administrative reads.

New additive audit tables record the verified authentication provider and the first/last observed request to each app. They preserve existing users, session counts, and events. A shared Firebase UID remains one account, and an existing shared session is not counted as a second login when it first reaches Vortex. Historical providers are unknown until observed again; they are not inferred from older `google_sign_in` event names.

## Reporting contract

- `GET /api/admin/activity?module=all|logins|transcriptions|vortex&user=<hashed-id>&limit=100`: module filtering happens before the event limit; totals and pagination use the same filters. The response includes module counts and provider/app metadata. Omitted `module` retains the existing behavior.
- `GET /api/admin/vortex?user=<hashed-id>&limit=100`: owner-only, read-only job metadata, totals over all matching retained jobs, newest-first pages up to 2,000 rows, and expiration-aware ready-file sizes. An older PC without the Vortex table returns `available: false`.

The Vortex report reads existing jobs. It excludes deleted jobs and never selects URLs, search queries, media titles, filenames, tickets, error output, or credentials. Ready downloads are not device-save counts. Request and worker audit records use fixed event names and allowlisted fields.

Frontend reads are authenticated, uncached, and discarded after account changes. Loss of owner authorization at any admin endpoint clears all private panels. Partial Vortex outages show an explicit unavailable/stale notice while the existing activity view remains usable.

## Validation

```sh
python -m unittest discover -s tests -p test_activity_dashboard.py -v
python -m unittest discover -s vision-pc -p test_firebase_auth.py -v
python -m unittest discover -s vision-pc -p test_vortex.py -v
node tests/gemini-admin.test.cjs
python tests/activity_browser_smoke.py
```

Browser checks use mocked Firebase and HTTP responses; backend tests use SQLite and locally signed test tokens. They do not verify a live PC rollout or make real media downloads.

## Owner account management (Firebase + local PC)

The Logins tab now lists all Firebase Authentication users, including people
who registered but have not sent a job to the PC. Names come from Firebase display
names or the observed identity, split into first/last fields. Missing names remain
"Not provided"; names are never invented from email addresses.

The directory and destructive action use separate, owner-only endpoints:

- GET /api/admin/accounts: lists registered Firebase users and aggregate
  Vision project, Venture conversation and Vortex job counts. Raw Firebase UIDs
  and service-account credentials are never returned.
- POST /api/admin/accounts/delete: accepts only a SHA-256 account identifier and
  the exact typed phrase "DELETE user@example.com". It refuses owner or
  administrator identities, and active jobs must be finished/cancelled first.

### Required private setup before enabling Delete

The normal PC updater deploys the new code, but deletion remains disabled
until the administrator configures Firebase's server-side Admin SDK:

1. In the Firebase Console for the same project used by Vision, generate a
   service-account credential authorized to list and delete Firebase Auth users.
2. Save its JSON file only on the Windows PC, e.g.
   C:\ProgramData\VisionPC\firebase-admin.json, protected so ordinary users
   cannot read it. Never commit it to GitHub, embed it in HTML, or expose it in
   browser storage.
3. Add the private config.json entry:
   "firebaseAdminServiceAccount": "C:\\ProgramData\\VisionPC\\firebase-admin.json"
   and restart the Vision PC service after updating. The path is validated
   against the Firebase project's ID.
4. Sign in to Vision Status with the Google account listed in the PC's
   activity-admins.json; open Logins and review the account before
   choosing Delete, then type the exact confirmation phrase.

Deletion invalidates the Firebase Authentication user, erases the account's
Vision project rows and files, Venture conversations, memory, profile, credentials,
usage data, local context index, Vortex jobs and download files, and its activity
records stored on the PC. A tombstone blocks already-issued tokens; when complete
it retains only a one-way hashed account identifier, not a name, email or raw UID.
If Firebase or disk operations fail, deletion reports an error and can be retried;
it never claims success with pending cleanup.

This deletes PC-managed account data. It cannot remove files the user
previously downloaded to their own device, offline browser-local copies, or data
held by unrelated/external services. If an older separate Vision Cloud deployment
is still in use, its Firestore/Cloud Storage records require an additional,
deployment-specific deletion procedure; this endpoint must not be represented
as deleting those external resources.

### Test

python -m unittest discover -s vision-pc -p test_account_administration.py -v

The tests mock Firebase Admin operations and use temporary account-owned
files. They do not delete live users.
