# Durable import ownership: Phase 1A, opt-in preview

Implementation base: `d190c6bbff80440d733538989d0fe09c43deefdd` (2026-10-11).
The matching source artifact was built from `b9237ae27c3cdaa33682a81c634373c02bd6be37`.
This is a limited first implementation of the supplied Vision/Venture/Vortex
worker specification. It is **not the complete Phase 1 or a Qwen release**.

## Actual behavior and rollout boundary

`durableAssets.enabled` is **false unless an administrator explicitly sets it to
true** in the existing private configuration. No user preference/model migration,
model download, remote service change, or live activation is performed by this
change. Existing cloud models, credentials and settings remain unchanged.

When enabled for a newly accepted **Vision Vortex import**, the server stamps an
internal `assetOwnership: 1` contract into that job. Clients cannot request a
source lease or override this internal flag. Toggling the flag later does not
change accepted jobs or break request-idempotency. Legacy imports keep their
existing behavior; enabling this preview does not migrate them automatically.

The acquired low-bandwidth **analysis proxy** is copied into
`<configured DATA_DIR>/owned-assets/<SHA256(owner UID)>/<random asset ID>.blob`.
It is not called a platform-original or full-quality download. Completed sparse
frames and transcript results are also retained as a bounded legacy JSON asset.
No source bytes are added to ordinary board JSON; the browser adds an asset ID
and representation label alongside the existing import metadata.

The Vortex history copy remains independent, with existing Balanced/M4A defaults
and temporary retention. **Later higher-quality history copies are not yet adopted.**
Direct uploads, YouTube's separate import path, documents and other source types
are not covered by this preview's registry.

## Ownership, publication and recovery

Additive tables in the existing SQLite database are `owned_assets`,
`owned_import_receipts`, and `owned_source_leases`. There is no new database or
service. A source lease is acquired atomically with the internal downloader's
acceptance and prevents its complete file from expiring or being manually removed
while adoption is pending. Leases do not expire based on a clock.

Publication uses these checkpoints:

1. Reserve owner/global/disk capacity and an immutable acquisition identity in a
   short transaction. The same import/role always returns the same asset identity.
2. Under an OS-released, cross-process file lock, stream into an owner-scoped
   staging file. Flush it and independently re-read it to verify byte count/hash.
3. Commit the verified hash, rename staging to the final file on the destination
   filesystem, then atomically publish the ready receipt and release source leases.

No SQLite write transaction is held during media copying or hashing. Cross-volume
sources are streamed; the publication rename stays within the destination volume.
After process interruption, the same import resumes from copying/verified/ready
state. An acknowledged source is reused without another download. Size/hash
mismatches fail rather than replacing it. Windows file data is flushed, but true
NTFS power-loss durability still needs the actual-host acceptance test.

A completed retained import remains available after its browser delivery cache
expires or is acknowledged. The old result endpoint falls back to its retained
JSON. Owner-scoped source/result receipts remain accessible even if the destination
project is subsequently removed. The worker does not recreate a removed project.
Account-deletion tombstones deny reads/new publication, and the existing explicit
account purge includes these new files, receipts and leases.

**Important incomplete recovery work:** there is no automatic board mutation,
recovery-inbox UI, individual-asset deletion UI/API, reference-counted orphan GC,
or supported interrupted-import retry UI yet. Errors can leave protected leases
or incomplete staging rows for administrator review; no timer silently deletes
them. Do not manually discard these files to satisfy a storage limit. Full
revision-safe attachment, module tombstones and stale-autosave protection remain
release gates. Closing the browser does not stop processing, but adding the result
to the board still uses the existing browser completion path.

## API, privacy and storage limits

All new routes require the existing Firebase account authorization:

- `GET /api/owned-assets/imports?after=<receipt-id>` returns at most 50 own receipts
  and an opaque next cursor. It does not enumerate other accounts.
- `GET /api/owned-assets/<id>` returns metadata and provenance for an owned asset.
- `GET /api/owned-assets/<id>/file` streams the file with conditional byte-range
  support, no-store/nosniff headers and attachment/sandbox policy.

These routes resolve random asset IDs, never client-supplied filesystem paths.
Symlinks and Windows reparse points are rejected in managed asset paths. The
private DATA_DIR itself is trusted administrator configuration. Source URLs and
metadata are owner-readable; diagnostic inventory omits prompts, URLs, raw UIDs,
keys and titles. Its local report includes the configured DATA_DIR, so keep it
private. Disk/hash checks are not a claim to detect every possible host compromise.

Initial configurable safeguards: 2 GiB disk reserve, 20 GiB retained bytes per
owner, 100 GiB globally, existing 2 GiB acquired-file limit and 32 MiB retained
result limit. Reservations include incomplete assets. These are conservative
configuration defaults, **not measured FUPCJ capacity**. Disk/owner/global limits
pause or reject new work; they never authorize removal of retained sources.

The existing 120-frame browser limit, base64 frame payload and two-hour analysis
preparer remain. This is not the requested paginated canonical evidence pipeline,
not exhaustive video understanding and not a complete offline project backup.

## Installation and diagnostics

**Do not install this draft on FUPCJ merely because its source is available.**
Merge and deployment require separate authorization and the outstanding gates.
For an approved code-only installation, use the existing updater with a reviewed
full application SHA (`-SourceRef`); do not use a moving branch name or run a new
server identity. The installer includes the new Python/helper files and preserves
configured data paths, credentials, scheduled task and Tailscale setup.

The new `Durable-Assets.ps1` and identical `.txt` copy live directly under the
existing install root (normally `C:\ProgramData\VisionPC`). They support only
**DryRun, Check and Backup**. They do not implement the requested final unified
Qwen install/download/benchmark/enable/rollback interface.

Read-only inventory after an approved code installation:

```powershell
& 'C:\ProgramData\VisionPC\Durable-Assets.ps1' -Action DryRun -OutputPath 'C:\ProgramData\VisionPC\retention-inventory.json'
& 'C:\ProgramData\VisionPC\Durable-Assets.ps1' -Action Check -OutputPath 'C:\ProgramData\VisionPC\retention-integrity.json'
```

Use a new output filename each time. `Check` reads and hashes retained assets;
it can cause disk load. Missing/incomplete/corrupt assets produce a failing check.
DryRun inventories available legacy source/history files and existing evidence;
it does **not** copy, migrate, infer or reconstruct missing originals.

The optional Backup action is a **cold DATA_DIR + configuration backup**, not a
complete installation rollback. It requires administrator privileges and an
operator-arranged maintenance boundary, refuses a running task/server, and never
stops or restarts services. It creates a new restricted-ACL backup envelope:

```powershell
& 'C:\ProgramData\VisionPC\Durable-Assets.ps1' -Action Backup -BackupDirectory 'D:\VisionBackups\retention-preflight-01'
```

The snapshot is under `retention-preflight-01\snapshot`. This explicit backup
includes Vortex as well as owned assets. The ordinary updater's transient-media
exclusion now preserves **leased** Vortex source directories; unleased expiring
media stays excluded. An unreadable ownership database stops an exclusion backup.
Include separately any configured persistent paths outside DATA_DIR, installation
code/private runtimes, and configuration dependencies. The new helper does not
claim to provide that complete backup set or an unattended maintenance lock.

To stop accepting new retained imports, set `durableAssets.enabled` false through
an authorized configuration change/restart. Already accepted retention contracts
continue. Do not roll back to an unaware pre-preview binary while retained imports
or leases exist: its cleanup logic does not honor them. Do not restore a stale
SQLite backup over live data. A tested forward-compatible rollback/deactivation
procedure, complete path inventory and ongoing-write reconciliation remain pending.

## Requirement-to-file/test map

| Scope | Implementation | Verification / remaining gate |
|---|---|---|
| R1/R2; T01/T02 (partial) | `owned_assets.py`, `vortex.py`, `vortex_imports.py` | Real file/SQLite interruption, lease, limits and recovery fixtures; actual NTFS/power-loss and complete cleanup audit pending. |
| R3; T03 (partial) | `server.py`, durable receipts/result fallback | Browser-independent processing/results; **server-side board attachment not implemented**. |
| T04/T15/T22 (partial) | owner checks, tombstones, `account_administration.py` | Deleted-project/account, cross-owner, path/reparse and range fixtures; stale autosave/module race gates pending. |
| T05 (inventory only) | `inspect_owned_assets.py`, `Durable-Assets.ps1/.txt` | Read-only inventory and checksum tests; actual migration/rollback pending. |
| T09 (source reuse) | owned receipt lookup in `vortex_imports.py` | Adopted source is not downloaded again after preparation failure. Broader canonical cache reuse pending. |
| T23/T24 (partial) | `setup_context.py`, existing updater file list | Cold backup keeps owned assets and leased temporary sources. Complete project exports and real Windows install/reboot pending. |
| UI/build compatibility | `web/vortex-import.js`, normal `web/build.py` | Retained asset metadata only, no model-picker or authentication rewrite. |

Tests added: `test_owned_assets.py`, `test_inspect_owned_assets.py`,
`test_vortex_owned_assets.py`; extended `tests/test_context_setup.py`.
The focused CI workflow exercises Ubuntu/Windows fixtures and Windows PowerShell
parsing. The repository's existing PR workflows remain the full regression gate.

Initial sandbox observations: 19 core filesystem/SQLite tests, 5 read-only inventory
tests and 10 context-installer/backup tests passed. Syntax compilation passed.
These counts are not a claim that every existing regression/browser/Windows test
has passed; use the actual PR check results and attached raw logs. No FUPCJ runtime,
GPU, model, public media retrieval or live deployment was tested by these fixtures.

## Unimplemented phases and mandatory release gates

The requested Qwen GGUF has not been downloaded, installed or tested here. No
provider registry/routing, local tokenizer, no-key answering, GPU FIFO scheduler,
vision helper/projector, no-cloud preprocessing audit, measured ETA, disclaimer,
Qwen branding or all-user default epoch is implemented by this slice. Existing
model selection remains unchanged. Code execution is not introduced.

Do not activate an all-user local default until those components and the essential
file/photo/security tests are actually connected and pass. Also finish durable
revision-safe board completion, archive-quality acquisition, paginated evidence,
legacy migration, explicit deletion/reference policies, backup/rollback closure
and real host operational checks before describing the complete specification as
implemented. No benchmark numbers, host memory headroom or photos-seen claims are
provided by this preview.
