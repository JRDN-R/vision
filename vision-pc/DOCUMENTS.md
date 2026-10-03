# Background document preparation

The first rollout defaults to **Standard**: PDF text/tables and page previews,
English OCR for scans and images, Word, PowerPoint, Excel, text/CSV/JSON/HTML,
email/legacy-document fallbacks, and bounded ZIP contents. Existing FFmpeg,
Whisper, sound events, Google sign-in, saved projects and video processing remain
on their existing paths. No paid model/API fallback is used.

## Install on the existing Vision PC

Use **Windows PowerShell as Administrator**, outside an important active run:

```powershell
$VisionDocuments = Join-Path $env:TEMP 'Enable-Vision-Documents.ps1'
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Enable-Vision-Documents.ps1' -OutFile $VisionDocuments
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VisionDocuments
```

The wrapper resolves one GitHub commit for the entire installation. The running
processor stays online during downloads, then briefly restarts for activation.
The installer runs actual extraction checks before activating and rolls the
processor/configuration back if activation fails. It reuses compatible installed
document packages. The existing scheduled task handles future startups; the
PowerShell window can close after success. Keep the PC awake and online.

If setup previously stopped at **Tesseract installation did not finish**, run
the same three installation commands again to fetch the corrected installer.
Installed Python packages are reused. Setup recovers Tesseract from the normal
Windows installation folder when the upstream installer ignores its requested
destination, then downloads and verifies the small English OCR model directly.
Diagnostic files are saved under `C:\ProgramData\VisionPC\data\document-ocr*`.

After success, refresh Vision. Supported originals are uploaded automatically
as they are added to modules. Each module shows its document status. Uploading
requires the browser to stay open; accepted jobs continue on the PC after it
closes. Saved receipts recover on reopening; if an acceptance response was lost,
the request ID recovers the existing job. Results return to the original module
and are saved as attachments. Account/project changes discard late responses.

AI packages retain originals and add extracted text, tables, visual previews,
source locations, extraction notes and `PREPARATION.json`. Instructions direct
the AI to prepared evidence first, then relevant visuals/originals for gaps.
Exporting before preparation finishes is allowed and explicitly reported.
Generated content is evidence, not a replacement for the user's instructions.

## Storage and limits

| Item | Planning estimate / limit |
| --- | --- |
| Standard tools and private runtime | Approximately 0.8–1.5 GiB additional |
| Full profile including CPU Docling and selected models | Approximately 3–6 GiB additional total |
| Minimum free space checked before installation | Standard: 4 GiB; Full: 12 GiB |
| Document result cache | 4 GiB across the server; 512 MiB per project |
| Individual original upload | 50 MiB |
| Individual prepared result | 16 MiB |
| Worker | One at a time; shares the existing media CPU slot; below-normal Windows priority |
| Per-job deadline | 30 minutes |
| Extraction limits | 100 PDF pages/slides, 40 images, 2 million text characters; spreadsheet/ZIP limits are recorded in extraction notes |

These are estimates, not measured values for your machine. Exact installation
size is printed at completion and recorded in
`C:\ProgramData\VisionPC\data\document-storage.json`. The report excludes existing
Whisper/video tools, old optional runtimes from previous upgrades, saved
projects, and temporary working files. Installer archives are removed after
success; a failed installation can leave its staged downloads for diagnosis.
Files saved in projects consume additional storage, including original files
and prepared attachments. Repeatedly upgrading dependency versions can retain
previous isolated runtimes; this installer does not silently delete them.

Uploaded temporary originals are deleted when a job completes or fails; the
project's original attachment remains. Server result cache expires after 30 days
and is cleaned at startup and new uploads. Copies already saved in projects are
retained. **Projects → Document processing** lets the owner remove cached
results or cancel accepted jobs. Removing an attachment locally does not delete
its retained cache, so undo/older project copies can still recover results.

## Optional later upgrade

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VisionDocuments -Profile Full
```

Full adds Docling's CPU PDF layout/table processing. It downloads only the
selected layout and table model assets during setup. It does not add a chat/VLM,
image-generation model, additional speech model, or remote processing service.
The document Python/Java programs are blocked from outbound networking after
installation; model use is offline. Local inference requires downloaded model
weights. A network pipeline cannot remove that requirement while keeping all
processing local and avoiding a hosted inference service.

## Coverage and fidelity

- PDFs keep numbered page text and previews; sparse-text pages receive OCR.
- Word keeps document block order and tables, plus embedded raster images.
- PowerPoint keeps slide text, tables, notes and embedded raster images.
- Excel keeps sheet names, cell addresses, formulas and cached values. It does
  not recalculate formulas or execute macros. Cached values may be stale.
- Other supported documents use local MarkItDown, then Apache Tika if needed.
- ZIP handling checks expanded size, entry count, paths, compression ratios,
  encryption and nesting. It never executes embedded files.
- OCR is English in this first rollout and is not a visual-description model.
  Other scripts, handwriting, diagrams, charts, tracked changes, slide layouts
  and protected/damaged files may still need the original or direct inspection.

Limits, fallback conversions and missing details appear in extraction notes.
The Windows child process is bounded and killed with its server task; this is
not a VM sandbox for hostile executables. No arbitrary program-upload feature
is provided.

## Components

Standard uses MarkItDown (MIT), Tesseract (Apache 2.0), Apache Tika (Apache 2.0),
Eclipse Temurin JRE (GPL with Classpath Exception), pdfplumber, PDFium bindings,
python-docx, python-pptx, openpyxl and Pillow. Full additionally uses Docling
(MIT), PyTorch CPU and selected model assets under their own licenses.
Package license files remain in the private installed runtime; Tika/Java/OCR
distributions retain their bundled notices. Existing FFmpeg build licensing is
unchanged. This feature does not bundle PyMuPDF or a paid API.

Source implementations: `documents.py`, `document_worker.py`,
`Document-Tools.ps1`, and `web/documents.js`.

Validation: queue ownership, hash/receipt conflicts, cancel/restart recovery,
capacity bounds and real PDF/Office/OCR/ZIP fixtures are covered by
`test_documents.py`. `setup_documents.py` performs actual offline extraction
checks on the Windows installation before enabling it.
