# Intelligent Context Engine

Vision's editable project remains the source of truth. The context engine builds
a separate local SQLite index and content-addressed evidence store. An optimized
Venture manifest identifies one owner, project and saved revision; it is **not**
an offline project export. Ordinary Vision imports, exports and portable HTML do
not require this index to stay editable.

### Downloading RAG context

Vision's main **Export** button offers **Download RAG (recommended)** and
**Download ZIP**. RAG saves the latest board revision and uses the existing
authenticated context status/search endpoints to download a readable `.rag.txt`
snapshot for the project's main task. It does not require a new PC deployment
when the context engine is already installed. ZIP opens the existing image and
attachment export options.

The RAG text includes retrieved instructions, evidence, source references and
coverage limitations. Included text is readable offline, but source references
do not grant another AI access to FUPCJ. Images, binary originals and the vector
index are not embedded. File size depends on the evidence selected; this is not
a lossless compression format or an editable project backup. If retrieval is
incomplete, Vision displays its limitations before a second click downloads the
available text. Choose ZIP when the task requires original files or images.

Preparation is cancellable and tied to one account, project and saved revision.
An account/project switch, new edit, mismatched response or unavailable index
cannot silently produce a stale RAG download. No paid generation or embedding
request is introduced by this export.

The implementation combines exact/FTS5 matches, complete record groups, connected
nodes and optional local semantic embeddings. Complete-enumeration requests expand
the source set, and a context limit defers whole evidence units with explicit
coverage warnings. A "ready" index confirms indexing finished, not that an answer
has proved every relevant fact. Unsupported extraction, visual-only evidence,
conflicting records and budget limits still require attention.

## Installation and update after review

Do not run these commands against the live server until the pull request and
deployment have been approved. No server installation is performed by a GitHub
commit or by these repository changes alone.

Use **64-bit Windows PowerShell as Administrator on the FUPCJ server**. Finish or
pause ongoing transcription, video processing and Venture runs before activation.
The existing `Vision Private PC` task, its SYSTEM identity, Tailscale route,
Firebase settings, encrypted credentials and user-owned data remain in use.

1. Record a known-working application commit and keep a current editable export
   of an important project for the post-update checks.
2. Substitute the **reviewed 40-character commit SHA** from the approved PR below.
   Do not use a moving branch name for the final activation.
3. Run this sequence. It installs application code, the optional CPU semantic
   dependencies/model, then runs the local check. Downloads occur before each
   activation; the service is stopped while its complete data snapshot is made.

```powershell
$VisionReviewedCommit = 'REPLACE_WITH_REVIEWED_40_CHARACTER_COMMIT_SHA'
if ($VisionReviewedCommit -notmatch '^[0-9a-f]{40}$') {
    throw 'Set the exact reviewed commit SHA before continuing.'
}
$VisionUpdater = Join-Path $env:TEMP ('Vision-context-update-' + [Guid]::NewGuid().ToString('N') + '.ps1')
Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/JRDN-R/vision/$VisionReviewedCommit/vision-pc/Setup-Vision-PC.ps1" -OutFile $VisionUpdater
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VisionUpdater -Action Update -SourceRef $VisionReviewedCommit
if ($LASTEXITCODE -ne 0) { throw 'Application update failed. Read the rollback/backup message before retrying.' }
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VisionUpdater -Action InstallContextEngine -SourceRef $VisionReviewedCommit
if ($LASTEXITCODE -ne 0) { throw 'Optional CPU model activation failed. The completed application update and backups are retained.' }
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VisionUpdater -Action CheckContextEngine
if ($LASTEXITCODE -ne 0) { throw 'Context verification requires attention. Do not assume semantic retrieval is ready.' }
```

The existing `Update-Venture.ps1` wrapper also accepts
`-SourceRef <reviewed SHA> -InstallContextModel` and resolves its reference to one
commit before downloading the installer. It keeps its existing default `main`
behavior when no reference is supplied.

An application-only update works without optional semantic packages. That mode
reports **lexical-only**, with exact, record and graph retrieval still available.
InstallContextEngine is required before claiming semantic retrieval is active.
It copies the currently installed application into its activation stage rather
than silently downloading a different code revision.

### What is installed

| Component | Location / behavior |
| --- | --- |
| Existing web processor | Existing private Python runtime and scheduled task |
| Derived index | `data\context\context.sqlite3`, beneath the configured data directory |
| Local evidence | Engine-owned content-addressed files beneath `data\context` |
| Optional semantic packages | A new private `plugins\context-<installation-id>` directory |
| Embedding model | A new private `models\minilm-l6-v2-<installation-id>` directory |
| Model | `sentence-transformers/all-MiniLM-L6-v2`, pinned revision `c9745ed1d9f207416be6d2e6f8de32d1f16199bf` |
| Model representation | Safetensors weights, tokenizer and configuration; 384 float32 values per vector |
| Default local limits | Two indexing workers, two embedding CPU threads; CPU inference explicitly selected |
| Per-project derived text | `intelligentContext.maxProjectTextBytes`: 64 MiB by default, configurable from 1 KiB through a 128 MiB ceiling; counts UTF-8 extraction results, retained full text and provenance, including cached results |
| Previous application/data | Protected retained `downloads\processor-update-backup-<id>` directories |

The model's safetensors weights are about 90.9 MB; the complete optional Python
runtime dependencies require substantially more disk than the weights alone.
The CPU PyTorch wheel is explicitly selected from PyTorch's CPU index. This is
not a continuously running language model and does not use a paid embedding API.
The model is downloaded only during the explicit installation action. Runtime
embedding calls use local files and disable remote model code.

Direct dependencies are pinned in `vision-pc/requirements-context.txt`; the
resolved package list is recorded in `vision-context-packages.json` inside the
private package directory. Previously installed transcription/document packages
and downloaded models are retained. Updating optional dependencies should use a
new private directory and repeat the offline smoke test before activation.

### Prerequisites and observed hardware

The existing updater requires x64 Windows, an administrator session and the
already-installed private Python runtime. The context preflight checks SQLite
FTS5, actual logical CPU count, CPU identification, physical memory and free disk
space. It does not assume the FUPCJ machine has 48 GB RAM and does not require or
claim to detect a GPU. To record the actual machine before deployment:

```powershell
& "$env:ProgramData\VisionPC\runtime\python.exe" "$env:ProgramData\VisionPC\setup_context.py" --report
Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors
Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory
Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion
```

The report command becomes available after the code update. These CIM commands
are read-only and can be run before it. Keep the initial two-worker/two-thread
settings until measurements on this machine justify changing them. Thread limits
are resource controls, not a guaranteed memory ceiling or a hard scheduler
priority over other work.

The derived-text limit is enforced across source files and while archive members
are processed, before later members/sources are collected or cached. If it is
exceeded, the new generation stops with an explicit **attention** diagnostic;
the previous valid index and original editable project remain intact. No partial
generation is published as complete. Split an oversized project or adjust the
reviewed configuration within the ceiling, then rebuild. This bounds accumulated
derived text, not total process RAM: parser/model allocations, original payloads,
Python objects and concurrent work consume additional memory. Historical index
generations remain retained, so monitor disk usage separately.

Internet access is needed during explicit installation for GitHub, PyPI,
PyTorch's CPU wheel host and Hugging Face's pinned model files. No API key or new
subscription is needed. The staged offline inference check fails activation if
required wheels, DLLs or model files cannot be loaded on the actual machine.

### Backups, activation and rollback

The updater first downloads/stages source, compiles it and runs a disposable
index/retrieval check. It saves the existing application files and configuration,
stops the existing task, and snapshots the **complete configured data directory**
before loading the new application. The snapshot includes SQLite databases and
WAL sidecars, original sources, conversations and protected credential files.
It rejects links/junctions and requires space for the data copy plus a margin.
Each copied file is checked with SHA-256; a completed manifest is written last.
A failed or interrupted snapshot is not marked valid and does not activate new
code. For large data directories, this cold-copy stage can extend downtime.

The previous valid derived index remains available until each new revision is
published. The derived schema is independent of editable project storage. A
schema version mismatch fails closed; it is not a reason to delete user data.

Failed activation restores the previous application/configuration. Successful
activation retains its protected backup and prints its exact path. To revert a
reviewed deployment later, use the updater version that created that backup:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:ProgramData\VisionPC\Setup-Vision-PC.ps1" -Action Rollback -BackupDirectory 'C:\ProgramData\VisionPC\downloads\processor-update-backup-REPLACE_WITH_PRINTED_ID'
```

Rollback verifies the backup manifest and application/configuration hashes,
stops the task, restores application/configuration, then checks/restarts the
existing task when it was running. It deliberately **does not restore the older
data snapshot over current data**: projects and conversation messages saved after
the update must survive code rollback. The snapshot is available for separately
reviewed disaster recovery. Before any manual data restoration, stop the task and
preserve another copy of current data; do not restore a live SQLite database or
mix a database with unrelated WAL sidecars.

Backups are never automatically deleted by the new transaction. Inspect disk
usage after several updates and retain at least the last known-working backup
until real-server verification is complete. They contain private user data and
configuration, so keep their administrator/SYSTEM access protection.

To disable the context engine while retaining its files and use the existing
Venture preparation path:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:ProgramData\VisionPC\Setup-Vision-PC.ps1" -Action DisableContextEngine
```

`-Action EnableContextEngine` re-enables it through the same backup/activation
path. Neither action deletes original projects, conversations, indexes or models.

## Verification on the actual Windows server

The following checks remain necessary even if repository CI passes:

1. Run `-Action CheckContextEngine`. Its disposable native project has 13 complete
   CSV operation records, linked instructions and exact values `0.380` and
   `133-430065-2`. It verifies indexing, retrieval, complete enumeration and account
   isolation, plus configured offline semantic inference and authenticated health.
   It never calls a paid model API and does not create a project in user storage.
2. Sign into the actual Vision UI. Open an existing project, edit one node, save,
   and confirm context progresses from updating to ready for the new revision.
   Reopen it on mobile and desktop. Re-import its original editable export and
   verify nodes, connections, instructions and attachments are unchanged.
3. Use two different authorized accounts with separate projects. A retrieval
   request for the other account's project must fail; no cross-account sources
   should appear in manifests or results.
4. In an authorized test conversation, enable Include board and ask to reproduce
   all records from a known source. Compare every field and note with the original.
   Then ask a narrow question and inspect context coverage, provider-reported
   usage and any completeness warnings. Paid provider calls occur only during
   this explicitly submitted Venture turn, not during indexing/checks.
5. Observe Task Manager during indexing plus normal transcription/video work.
   Record actual CPU load, working set, indexing duration and retrieval latency.
   The Linux development machine's measurements are not a FUPCJ benchmark.
6. During a disposable project's index update, restart the existing Vision task.
   Confirm queued work resumes and the last complete revision stays readable.
   Verify old saved conversations and retained generated files still open.
7. On a spare installation/copy of data, exercise rollback and confirm a project
   saved **after** the update remains intact. Do not test disaster recovery by
   overwriting the only live copy of user data.

If the semantic model is installed after projects were already indexed in
lexical-only mode, the changed processing/model fingerprint makes those derived
generations stale and queues refresh on indexing/retrieval. The published older
generation is retained while rebuilding. The authenticated project-context
rebuild operation remains available for explicit repair.

## Current bounds and operational limits

These safeguards limit derived processing; they do not lower the original
editable-project save limit or remove original files. Projects or attachments
outside these bounds require explicit full-source handling or a future measured
increase, rather than a claim that the index is complete.

| Derived-processing limit | Current value |
| --- | --- |
| Project JSON indexed | 64 MiB |
| Project topology | 10,000 nodes / 30,000 edges |
| One attachment indexed | 50 MiB |
| Extracted source text | 4,000,000 characters; excess is reported incomplete |
| Shared ZIP expansion | 2,000 entries / 128 MiB expanded, with depth/path/type checks |
| Project evidence units | 40,000 |
| Default initial context budget | 48,000 characters, with model-aware reduction where supported |
| Default aggregate additional retrieval | 200,000 characters; bounded operation count also applies |
| Visual/original transfer | Additional type, size, secret-screening and image/PDF inspection limits |

Character budgets are application safeguards, **not provider token counts**.
Provider-reported usage and optional tokenizer measurements are labeled
separately. A complete source unit that does not fit remains available locally
and is flagged as omitted rather than silently cut to achieve token savings.

Published revisions, queued revision metadata, evidence blobs and optional
package/model installations persist on disk. Attachment payloads in durable job
snapshots reference content-addressed blobs, so unchanged attachment bytes are
not duplicated in every revision's queue payload. Automatic retention/garbage
collection across historic revisions is not yet implemented. Monitor free disk
space and preserve a recoverable backup before any manual cleanup. Do not delete
the editable project database, conversation database or original source storage
to repair a derived index. Per-project rebuilding is the first repair option.

Semantic ranking is exhaustive within a project rather than an approximate
nearest-neighbor service. This keeps deployment simple but needs benchmarking
before raising the unit limit substantially. The English MiniLM model is not a
guarantee of equal retrieval quality for every language or technical domain.
OCR and transcript extraction are incomplete representations of visuals/audio;
original evidence and any reported processing limitations remain relevant.

## Local development checks

```bash
python -m unittest discover -s tests -p 'test_context_setup.py' -v
python vision-pc/setup_context.py --self-test
```

On Windows, `tests/context-installer-windows.ps1` parses installer scripts and runs
the offline check, backup/security fixtures and a mocked-task rollback exercise.
It uses only a temporary directory and does not register tasks, modify Tailscale,
download a model or read live Vision data. The Windows CI job should pass before
deployment; its result is separate from testing on the FUPCJ server.

Real semantic tests are opt-in and never download files:

```bash
VISION_CONTEXT_TEST_MODEL=/absolute/model/path \
VISION_CONTEXT_TEST_PACKAGES=/absolute/private/site-packages \
python -m unittest discover -s tests -p 'test_context_model.py' -v
```

Without those variables, the real-model tests report a skip. They do not replace
real embeddings with random/hash vectors and label that semantic validation.

## Upstream implementation references

- [Sentence Transformers local-files-only and remote-code controls](https://sbert.net/docs/package_reference/sentence_transformer/model.html)
- [Sentence Transformers installation requirements](https://sbert.net/docs/installation.html)
- [Pinned MiniLM model files and model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/c9745ed1d9f207416be6d2e6f8de32d1f16199bf)
- [PyTorch CPU package index](https://download.pytorch.org/whl/cpu)

See the implementation report and benchmark artifacts for measured retrieval
coverage, token counts, test results and remaining functional limits. An 80–95%
input reduction is a workload target, not an installation guarantee.
