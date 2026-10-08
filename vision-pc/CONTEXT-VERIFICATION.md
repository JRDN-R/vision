# Intelligent Context Engine: implementation and verification

This change is a review build, not a live FUPCJ deployment. Editable Vision
projects and their existing portable exports remain authoritative. The new
SQLite database, retained evidence and revision manifests are derived local data.

## Implemented behavior

- Durable, debounced indexing starts from saved projects, including projects
  discovered after restart. Changed sources are extracted and embedded again;
  unchanged content reuses cached processing. Atomic publication retains the
  prior index while a revision is being prepared.
- Retrieval combines exact identifiers, SQLite FTS5, complete records, graph
  dependencies and optional pinned CPU MiniLM embeddings. Semantic windows cover
  long units while complete source text remains available. Vectors live in the
  existing local SQLite architecture; no separate database service is required.
- Venture's existing Include board control submits an authorized project and
  exact revision. It supplies a bounded manifest and evidence, with authenticated
  search, node, source pagination, coverage, PDF page and inspected original-file
  operations for supported Responses models. Other models use server prefetch
  and explicit coverage limitations.
- Text, code, HTML, wrapped HAR, CSV/JSON records, Office/PDF preparation, images,
  transcripts, frame metadata and bounded ZIP archives use existing processors
  where possible. Original files remain available locally. Unsupported or partial
  extraction is reported, not treated as a complete reading of the source.
- Account authorization, secret filtering, ZIP validation, bounded subprocesses,
  content hashes and scoped source references protect retrieval. Uploaded content
  remains untrusted evidence. Full-source mode does not bypass secret filtering.
- Existing Venture pricing accounts for each completed provider hop. Actual
  usage, missing usage and local token/cost estimates remain distinct. Retained
  conversation context can still be billed by the provider; reuse does not imply
  free input or guaranteed prompt-cache hits.
- The current Windows updater stages and verifies the engine, snapshots the
  complete data directory before activation and retains rollback. Code rollback
  preserves projects and conversations created after an update.

The vector store deliberately uses normalized blobs in SQLite and exhaustive
per-project scoring. The measured semantic query over the supplied project's
428 units took about 79 ms, including query embedding. This avoids adding a
native vector-extension deployment requirement to the Windows runtime. It is
not a claim that exhaustive search will outperform an ANN index at larger
scales. A separate cross-encoder was not justified by these measured cases;
hybrid ranking uses exact matches, lexical coverage and semantic score strength.

## Verification record

The unchanged starting revision was
`863f329ed2ba7fa9254a494075acafb934ebed65`. Its Python baseline passed 184 server,
4 cloud and 13 other repository tests. Final implementation results are recorded
below; no test used a paid embedding or generation API.

| Check | Local result |
| --- | --- |
| Server Python suite | 269 tests: 268 passed, one optional real-model test skipped in the ordinary environment |
| Cloud Python suite | 4 passed |
| Other repository Python suite | 23 tests: 20 passed, three real-model tests skipped in the ordinary environment |
| Explicit offline CPU model suite | All three real MiniLM tests passed; the server semantic suffix test also passed separately |
| Workspace service suite | 16 passed |
| JavaScript regression scripts | All 15 scripts passed, including 12 named context integration tests |
| Portable build/version | Rebuilt successfully; version checks passed at 1.0.2.8 |
| Venture browser checks | Smoke and mixed-board submission passed at 390px and 1280px |
| Security subset | 22 passed, including authorization, forged references, archive limits and retained-source integrity |

The optional model tests check real normalized embeddings, synonym ranking,
persisted/reused vectors and semantic access beyond a long record's first model
window. Skipped tests in the ordinary suite are not counted as passes there.
Windows CI exposed retained SQLite connection handles; scoped production
connections now close after commit/rollback, with two explicit lifecycle tests.

The browser checks exercised the rebuilt portable bundle, including mobile
390px and desktop 1280px viewports. Venture submission tests verified that the
new path sends a revision reference without duplicate prompt/ZIP attachments,
retains drafts on failure and preserves the old-server ZIP path. Original
project/account, workspace, export and local-file checks were also exercised.

This container required a single-process Chromium wrapper. For the original
multi-context `run-local-smoke.py` and `workspace-browser.py` checks, a scratch
compatibility shim deferred page/context closure until browser shutdown; test
assertions and application code were unchanged. Hosted emulation and literal
`file://` startup both passed. The unmodified `dictation-export.py` passed using
the bundled FFmpeg and verified byte-identical original audio recovery.

The PR adds Linux and Windows context CI and retains the existing full Venture
workflow. CI runs on disposable runners; passing Windows CI does not establish
that installation on the user's FUPCJ machine succeeded.

## Benchmarks and reproducibility

See [BENCHMARK-CONTEXT.md](BENCHMARK-CONTEXT.md) for token counts, field coverage,
latency, memory, incremental reuse and input-only cost estimates, with the
machine-readable aggregate results linked there. The supplied approximately
17 MB export was used locally. Its original source content is not committed.

The export contains prompt packages rather than native editable board JSON.
The benchmark adapter preserves module/source provenance but does not invent
connections, coordinates or revision history. Native graph behavior is covered
by separate automated fixtures. The real-project oracle independently checks all
13 PDF operation identifiers, work centers, descriptions, hours and complete
notes, plus applicable procedural prompts.

The baseline is the actual existing `Preparation.prepare` path on the ZIP. A
separate full-text reference represents a hypothetical complete reading of the
exported prompt and prepared page text. These comparisons are deliberately kept
separate: a smaller incomplete initial FTS response is not a quality-equivalent
full-context baseline. Token measurements use `tiktoken/o200k_base`; this encoding
is not claimed to be the selected provider model's verified tokenizer. Tool
schemas, images, retained history, generated output, cache effects and provider
continuations can change the actual bill.

Run deterministic checks from the repository root:

```bash
python -m unittest discover -s vision-pc -p 'test_*.py' -v
python -m unittest discover -s vision-cloud -p 'test_*.py' -v
python -m unittest discover -s tests -p 'test_*.py' -v
python vision-pc/setup_context.py --self-test
python web/build.py
python tests/versioning-smoke.py
node tests/intelligent-context.test.cjs
```

Real-model checks require explicitly installed local model/packages; see
[CONTEXT-ENGINE.md](CONTEXT-ENGINE.md#local-development-checks). Without those,
semantic tests skip transparently. The benchmark's `--help` documents private
ZIP/model inputs and optional measurement dependencies. No paid call is needed.

## Remaining gates and limitations

1. Review the PR, then execute the documented staging, backup, installation and
   rollback checks on the actual Windows server. Its CPU/RAM/GPU, scheduled task,
   private runtime DLLs, Tailscale connectivity and concurrent transcription load
   were not measured in the Linux development environment.
2. Run an explicitly authorized live Venture comparison. The tests verify
   retrieval, source coverage, provider payloads and accounting with mocked API
   transport. They do not prove that a paid model will choose every required
   retrieval operation or generate a correct browser Console script.
3. The real PDF field oracle does not validate screenshot interpretation or
   HAR-derived browser behavior. OCR/transcripts cannot establish full visual or
   audio coverage. Image pixels and unusual binary encodings may still contain
   sensitive information that heuristic inspection cannot detect.
4. Processing and retrieval have explicit size, archive, aggregate extraction,
   unit, call and character limits. Large complete-enumeration requests paginate or report incomplete
   coverage. They are not silently reduced to top-k snippets and declared done.
   Optional MiniLM quality and exhaustive vector search need further evaluation
   for other languages, domains and much larger projects.
5. Derived historic generations and installation backups currently require disk
   monitoring; automatic retention/garbage collection is not implemented.

Use the exact reviewed commit with the commands in
[CONTEXT-ENGINE.md](CONTEXT-ENGINE.md#installation-and-update-after-review).
Neither a GitHub commit nor PR merge installs the Windows service. This work does
not authorize a production merge or live installation.
