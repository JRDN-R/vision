# Context engine benchmark (2026-10-08)

The supplied 17,008,401-byte export passed the complete-record check at the default 48,000-character context budget: **all 13 operations, all five required fields, every complete note, and all 12 procedural node instructions** were present in the initial context. The unchanged semantic paraphrase test also retrieved the expected complete record with the real local MiniLM model.

The initial context contained **14,758 reference-encoding tokens**, compared with **141,897 tokens** for the single-full-prompt plus prepared-page/OCR reference: **89.599% less text**. This is a measured text-context comparison, not a measured reduction in an OpenAI invoice.

## What was actually compared

The legacy server stores `project_prompt` capped at 250,000 characters but does **not** automatically send that field as the API prompt. It sends selected FTS excerpts and attachment preparation notes, then makes originals available through Code Interpreter. A later model/Code Interpreter read can incur additional context and tool costs. Those later paid steps were not run.

The real-project legacy baseline therefore invokes the existing `Preparation.prepare` on the original ZIP, including its local document worker, FTS retrieval and actual text wrapper. Its initial text was smaller but contained only two complete operation records. It would be misleading to claim that the new initial payload is cheaper than that incomplete initial payload.

The full-text reference includes one exported main prompt plus the supplied prepared PDF-page and OCR text. It does not count duplicate module prompts, repeated CSV projections, or extraction receipts again. For synthetic cases it contains each source once. This is an explicit counterfactual full-text workload, not observed provider usage.

All token counts use `tiktoken`'s `o200k_base` encoding. They are exact for that reference encoding; compatibility with every selectable model's tokenizer is not assumed. The new initial counts include the actual `ContextIntegration.prepare` manifest, evidence, coverage wrapper and limitations. The function schema adds 199 reference tokens separately. Conversation history, images, general system instructions and provider framing are excluded.

## Initial retrieval results

| Dataset | Legacy initial text tokens | Legacy complete records | Engine initial text tokens | Engine complete records |
| --- | ---: | ---: | ---: | ---: |
| Synthetic, 13 records | 4,715 | 9/13 | 8,596 | 13/13 |
| Synthetic, 100 records | 5,275 | 11/100 | 9,577 | 26/100 |
| Supplied export, 13 records | 6,553 | 2/13 | 14,758 | 13/13 |

The 100-record case exceeds the initial budget and explicitly reports incomplete coverage. Four deterministic source-pagination calls supplied all 100 complete records, with 48,575 tokens across the initial evidence and returned tool-output texts. That sum is **not** total billable input: continuation requests can include prior conversation context again. No savings claim is based on its incomplete 26-record first response.

The small synthetic fixture preserved its two procedural instructions; the large fixture also preserved both. The supplied export preserved all 12 expected procedural instructions. Whole-project completeness remains unestablished where other source units, images, or deliberately redacted material have not been inspected, even when the requested operation records pass independently.

## Exact and semantic checks

The two synthetic focused queries request an exact identifier located within the source, including a late record in the 100-record case. Each supplied its complete expected record. The small legacy FTS result contained the identifier but truncated its notes, which the oracle correctly failed; the large legacy exact query also passed.

The real-project focused query was a semantic paraphrase about the bonding material, without the operation identifier or product name. The unchanged query passed after hybrid ranking was corrected to consider lexical term coverage, semantic similarity strength, and duplicate candidate content. The legacy lexical result supplied none of that expected record's five required fields. This is one measured semantic case, not a claim of universal semantic recall.

The independent oracle parses the original PDF's tabular records and checks operation number, work center, exact description lines, decimal run hours, and the complete notes. It preserves case and punctuation while normalizing layout whitespace. Repeated continuation-page records are checked for consistency. Finding all 13 identifiers alone cannot pass. Four oracle unit tests include missing notes, truncation, wrapped descriptions and punctuation changes.

## Local performance

Measured on the available Linux test host with Python 3.12.14, CPU-only MiniLM and two embedding threads. This was **not** the user's Windows FUPCJ server. Peak RSS covers the benchmark process and child converters sampled every 10 ms during indexing and initial retrieval; it includes runtime/library memory and earlier benchmark work.

| Dataset | Initial indexing | Initial retrieval | Title-only revision | One changed source | Peak process-tree RSS |
| --- | ---: | ---: | ---: | ---: | ---: |
| Synthetic, 13 records | 8.888 s | 7.6 ms | 0.032 s | 0.055 s | 705.0 MiB |
| Synthetic, 100 records | 32.821 s | 14.5 ms | 0.192 s | 0.220 s | 792.4 MiB |
| Supplied export | 61.655 s | 69.6 ms | 0.491 s | 0.574 s | 1001.9 MiB |

The supplied-export title-only revision reused all 125 extracted sources. Adding one changed caption indexed one source and reused the other 125. Real source units were embedded with the installed `sentence-transformers/all-MiniLM-L6-v2` snapshot `c9745ed1d9f207416be6d2e6f8de32d1f16199bf`; the multiwindow adapter preserves semantic searchability beyond a single model window. Exact source text remains available independently of embeddings.

## Cost interpretation

Using the repository's `venture-pricing.json` version `2026-10-06`, the supplied-export input-only estimate for the reference workload is **$1.41897**, and the engine's initial text estimate is **$0.14758**. The legacy incomplete initial text estimate is **$0.06553**. These estimates apply the shipped catalog to reference-encoding text counts; they are not official account-balance or actual provider-usage measurements.

No paid API request was made. Output/reasoning tokens, image tokens, tools, continuation input, provider caching and Code Interpreter costs remain unmeasured. Runtime funding accounting separately records every returned provider hop and prices each hop before summing, avoiding per-request threshold errors and duplicate final-response/container charges; seven accounting tests cover those behaviors.

## Limitations and privacy

- The supplied artifact is a prompt export, not editable board JSON. The benchmark derives stable node IDs from its 25 module folders and retains their contents. It does not reconstruct native edges, conditional state, coordinates or revision history. Graph behavior is exercised separately by the engine tests.
- The independent operation oracle validates supplied evidence, not a generated browser Console script. No live work-order system or paid model output was tested.
- Screenshot interpretation, end-to-end HAR/browser behavior, long conversation continuation costs, and Windows workload contention need deployment-environment validation.
- The public JSON contains aggregate counts, timing, reference tokenization, model identity, code hashes and the input fingerprint only. The original ZIP, extracted records, screenshots, source names and query identifiers remain outside the repository.
- Results are workload-specific. Large exhaustive requests may need more context and pagination; completeness takes priority over an impressive reduction percentage.

## Reproduce

Use an isolated environment with the repository's server/document dependencies, `tiktoken` and `psutil`. Install the optional local model using the documented context setup; the benchmark never downloads an embedding model or invokes a paid provider. `tiktoken` may need its public encoding vocabulary cached before an offline run.

```powershell
python vision-pc/benchmark_context.py --model-path "C:\path\to\context-model" --model-packages "C:\path\to\context-packages" --output "C:\private\context-benchmark.json"
```

Add `--project-zip "C:\private\project-export.zip"` to include a private prompt export compatible with the table oracle. Omit `--model-packages` when the embedding dependencies are already in the interpreter environment. Omitting `--model-path` intentionally benchmarks lexical-only fallback; it does not validate semantic retrieval. An unsupported PDF layout fails the independent oracle instead of inventing expected records.

```powershell
python -m unittest discover -s vision-pc -p "test_context_billing.py"
python -m unittest discover -s vision-pc -p "test_context_benchmark.py"
```

The complete aggregate measurement is in [benchmarks/context-2026-10-08.json](benchmarks/context-2026-10-08.json). Source implementation fingerprints are included so later algorithm changes can be identified.
