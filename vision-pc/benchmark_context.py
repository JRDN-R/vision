"""Repeatable, offline context benchmark. No source text is written to reports.

Run synthetic fixtures without arguments. --project-zip accepts a private Vision
prompt export; it does not reconstruct missing editable board state. Reports are
aggregate measurements and explicitly separate tokenization from provider usage.
Never commit the input ZIP, engine work directory, or extracted private evidence.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import platform
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import zipfile


QUERY = ('Reproduce every operation from the work order PDF, including all operation numbers, '
         'work centers, descriptions, run hours and complete notes. Follow the procedural instructions.')


def normalized(value):
    return ' '.join(str(value).replace('\\n', '\n').replace('\\r', '\r').split())


def attachment(name, raw):
    return dict(id=hashlib.sha256(name.encode()).hexdigest()[:24], name=name,
                data='data:application/octet-stream;base64,'+base64.b64encode(raw).decode())


def synthetic_project(count=13):
    """Fictional records: intentionally unlike any user's technical instructions."""
    records = [dict(operation=f'{(i+1)*30:04d}', workCenter=f'DEMO{i%3}',
                    description=f'Synthetic demonstration task {i+1}', runHours=f'{i/4:.2f}',
                    notes=('Fictional notes, not maintenance instructions. '+
                           f'Keep unique value TEST-{i+1:04d} and punctuation, exactly.\n')*12)
               for i in range(count)]
    evidence = '\n\n'.join('OPN '+item['operation']+'\nWC: '+item['workCenter']+'\nDescription: '+item['description']+
                           '\nRun Hrs: '+item['runHours']+'\nNotes: '+item['notes'] for item in records)
    unrelated = '\n'.join(f'Archive commentary {i}: landscaping and unrelated background discussion.' for i in range(count*80))
    project = dict(title='Synthetic context benchmark', mainPrompt='Preserve every operation and its complete notes.',
                   nodes=[dict(id='instruction', title='Procedure', prompt='Apply Add, fields, Notes, New, text, OK in this order.'),
                          dict(id='records', title='Operations', attachments=[attachment('operations.txt', evidence.encode())]),
                          dict(id='archive', title='Unrelated history', attachments=[attachment('history.txt', unrelated.encode())])],
                   edges=[dict(from_='instruction', to='records')])
    project['edges'][0]['from'] = project['edges'][0].pop('from_')
    ground = [dict(id=item['operation'], wc=item['workCenter'], description=item['description'],
                   hours=item['runHours'], notes=item['notes']) for item in records]
    return project, evidence+'\n'+unrelated, [('operations.txt', evidence), ('history.txt', unrelated)], ground, {
        'kind': 'synthetic', 'expectedRecords': count, 'originalGraphAvailable': True,
        'limitations': ['Records are fictional benchmark fixtures, not supplied project evidence.']}


def pdf_ground_truth(raw):
    """Independent PDF page text oracle, not retrieval output or ID-only checks.

    This recognizes the supplied tabular report's layout. Other formats are
    explicitly unsupported rather than assigned invented expected records.
    """
    import pdfplumber
    result = {}
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        pages = len(pdf.pages)
        for page in pdf.pages:
            text = page.extract_text() or ''
            matches = list(re.finditer(r'(?m)^(\d{4})[ \t]+\d{5}[ \t]+([A-Z0-9]{6})[ \t]+(.+?)[ \t]+(\d+\.\d{2})(?:[ \t]+.*)?$', text))
            for i, match in enumerate(matches):
                segment = text[match.end():matches[i+1].start() if i+1<len(matches) else len(text)]
                continuation, separator, notes = segment.partition('Opn Notes:')
                if not separator:
                    raise ValueError('PDF ground-truth record has no identifiable notes boundary.')
                notes = re.split(r'(?m)^Step Quality Check|^Report Name:', notes)[0].strip()
                record = dict(id=match[1], wc=match[2], description=normalized(match[3]+' '+continuation),
                              descriptionParts=[match[3], *[line for line in continuation.splitlines() if line.strip()]],
                              hours=match[4], notes=notes)
                prior = result.get(record['id'])
                if prior and any(normalized(prior[k]) != normalized(record[k]) for k in record):
                    raise ValueError('Conflicting repeated PDF records require a reviewed ground truth.')
                result[record['id']] = record
    if not result:
        raise ValueError('This PDF layout has no supported ground-truth records.')
    return list(result.values()), pages


def private_export(path):
    from context_sources import validate_archive
    nodes, texts, ground = {}, [], []
    pdf_pages = 0
    with zipfile.ZipFile(path) as archive:
        entries = validate_archive(archive, {'entries': 0, 'expanded': 0})
        main = archive.read('MAIN_PROMPT.txt').decode('utf-8-sig')
        texts.append(('MAIN_PROMPT.txt', main))
        for entry in entries:
            if entry.is_dir() or '/' not in entry.filename:
                continue
            folder, filename = entry.filename.split('/', 1)
            node = nodes.setdefault(folder, dict(id='export-'+hashlib.sha256(folder.encode()).hexdigest()[:24],
                                                title=folder, prompt='', attachments=[]))
            raw = archive.read(entry)
            if filename == 'MODULE_PROMPT.txt':
                # Everything preceding the export navigation is actual supplied
                # node content. Navigation is retained as separate source data;
                # no native graph is invented from textual directions.
                content, marker, navigation = raw.decode('utf-8-sig').partition('--- VISION CONTEXT (for internal use) ---')
                node['prompt'] = content.rstrip()
                if marker:
                    node['attachments'].append(attachment('export-navigation.txt', navigation.encode()))
                texts.append((folder+'/MODULE_PROMPT.txt', raw.decode('utf-8-sig')))
            else:
                node['attachments'].append(attachment(filename, raw))
                if Path(filename).suffix.lower() in ('.txt', '.md', '.csv', '.json'):
                    texts.append((entry.filename, raw.decode('utf-8-sig')))
                if filename.lower().endswith('.pdf'):
                    records, pages = pdf_ground_truth(raw)
                    ground.extend(records)
                    pdf_pages += pages
    return dict(title='Private exported project benchmark', mainPrompt='', nodes=list(nodes.values()), edges=[]), main, texts, ground, {
        'kind': 'private-prompt-export', 'archiveBytes': path.stat().st_size,
        'archiveSha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'moduleCount': len(nodes),
        'pdfPages': pdf_pages, 'expectedRecords': len(ground), 'originalGraphAvailable': False,
        'limitations': ['No editable board JSON was supplied. Module-folder identifiers and source contents are preserved in a derived benchmark schema.',
                       'No native graph, coordinates, revision history, or conditional edge state is claimed to be restored.',
                       'The ground-truth oracle checks PDF record fields and entire notes, not generated browser script correctness.',
                       'Screenshot interpretation, HAR-driven browser behavior, and generated outputs require a separate end-to-end evaluation.']}


def record_coverage(text, records):
    haystack = normalized(text)
    fields = ('id', 'wc', 'description', 'hours', 'notes')
    checks = [{key: bool(normalized(record[key])) and normalized(record[key]) in haystack for key in fields} for record in records]
    for record, check in zip(records, checks):
        if record.get('descriptionParts'):
            # PDF table columns interleave the hours/certification fields with
            # a wrapped description. Every original description line must be
            # present; demanding artificial cross-column adjacency is invalid.
            check['description'] = all(normalized(part) in haystack for part in record['descriptionParts'])
    return dict(expected=len(records), idsPresent=sum(item['id'] for item in checks),
                completeRecords=sum(all(item.values()) for item in checks),
                fieldCoverage={key: sum(item[key] for item in checks) for key in fields},
                allComplete=bool(checks) and all(all(item.values()) for item in checks))


class PeakMemory:
    def __enter__(self):
        import psutil
        try:
            self.process = psutil.Process()
        except psutil.NoSuchProcess:
            # Some isolated runners expose a translated os.getpid() but mount
            # host /proc. Read only our own kernel-provided PID for measurement.
            pid = int(re.search(r'^Pid:\s+(\d+)', Path('/proc/self/status').read_text(), re.M)[1])
            self.process = psutil.Process(pid)
        self.peak = self.process.memory_info().rss
        self.stop = threading.Event()
        def sample():
            while not self.stop.wait(.01):
                total = self.process.memory_info().rss
                for child in self.process.children(recursive=True):
                    try:
                        total += child.memory_info().rss
                    except psutil.NoSuchProcess:
                        pass
                self.peak = max(self.peak, total)
        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()


def run_case(project, full_prompt, source_texts, ground, metadata, encoding, model_path=None, archive_path=None, model_packages=None):
    from context_engine import ContextEngine
    from context_integration import ContextIntegration
    from venture_files import Preparation
    from venture_billing import estimate
    token_count = lambda text: len(encoding.encode(text, disallowed_special=()))
    with tempfile.TemporaryDirectory(prefix='vision-context-benchmark-') as temporary:
        root = Path(temporary)
        @contextmanager
        def db():
            connection = sqlite3.connect(root/'baseline.db')
            connection.row_factory = sqlite3.Row
            try:
                with connection:
                    yield connection
            finally:
                connection.close()
        document_settings = dict(enabled=True, pythonPath=sys.executable, profile='Standard')
        legacy_sessions = SimpleNamespace(stop=threading.Event(), row=lambda rid: dict(cancel_requested=False),
                                          update=lambda *args, **kwargs: None)
        legacy = SimpleNamespace(sessions=legacy_sessions, db=db, root=lambda cid: root/'legacy',
                                 documents=SimpleNamespace(settings=document_settings, capability=lambda: dict(ready=True)))
        baseline = Preparation(legacy)
        began = time.perf_counter()
        if archive_path:
            prepared, visuals, baseline_context = baseline.prepare(dict(id='benchmark-run', project_id='benchmark', message=QUERY),
                [dict(path=str(archive_path.resolve()), name=archive_path.name, mime='application/zip', size=archive_path.stat().st_size)])
            tokens = [item['source'] for item in prepared]
            excerpt = baseline.retrieve('benchmark', QUERY, tokens)
            baseline_method = 'Actual existing Preparation.prepare on the original ZIP; local document worker, FTS, notes and excerpt wrapper. Image token costs excluded.'
        else:
            tokens = []
            for i, (name, source_text) in enumerate(source_texts):
                token = str(i)
                tokens.append(token)
                baseline.index('benchmark', token, name, [dict(location=name, text=source_text)])
            excerpt = baseline.retrieve('benchmark', QUERY, tokens)
            baseline_context = ('SELECTED LOCAL SEARCH EXCERPTS (not exhaustive; full originals and prepared evidence are in the file packages)\n'+excerpt+
                                '\n\nVisual previews are bounded. Do not infer that unshown pages/frames were inspected.')
            baseline_method = 'Existing Preparation.index/retrieve on synthetic text sources, with its actual excerpt wrapper.'
        baseline_seconds = time.perf_counter()-began
        # A fair full-text reference does not count every MODULE_PROMPT again
        # when it is already present in MAIN_PROMPT. Prepared PDF page text/OCR
        # carries the attachments' readable evidence; duplicated CSV projections
        # and extraction receipts are excluded from this reference workload.
        full_reference = full_prompt if metadata['kind'] == 'synthetic' else full_prompt+'\n'+'\n'.join(
            text for name, text in source_texts if name.endswith('.md'))
        settings = dict(documentSettings=document_settings)
        if model_path:
            settings['embeddingModelPath'] = str(model_path.resolve())
        if model_packages:
            settings['embeddingPackagesPath'] = str(model_packages.resolve())
        engine = ContextEngine(root/'index', settings)
        # Invoke the integration's actual context formatter and tool schema.
        # Only durable authorization/update plumbing is stubbed: no request or
        # provider transport is used by this benchmark.
        integration = ContextIntegration.__new__(ContextIntegration)
        integration.engine, integration.settings = engine, dict(contextCharacters=48000)
        integration.sessions = SimpleNamespace(update=lambda *args, **kwargs: None)
        integration.binding = lambda row: dict(owner='benchmark-owner', projectId='benchmark-project', revision=row['revision'], mode=row['mode'])
        row = dict(id='benchmark-run', revision=1, mode='adaptive', model='gpt-6-astra', max_tokens=8192,
                   message=QUERY, run_options_json=json.dumps(dict(codeInterpreter=True)))
        def status_summary(state):
            return dict(status=state.get('status'), ready=state.get('ready'), revision=state.get('revision'),
                        semantic=state.get('semantic'), metrics=(state.get('manifest') or {}).get('metrics'))
        with PeakMemory() as memory:
            began = time.perf_counter()
            indexed = engine.index_project('benchmark-owner', 'benchmark-project', 1, project)
            index_seconds = time.perf_counter()-began
            began = time.perf_counter()
            text, tools = integration.prepare(row)
            retrieval_seconds = time.perf_counter()-began
        result = engine.prepare('benchmark-owner', 'benchmark-project', 1, QUERY, budget=48000, mode='adaptive')
        expected_instructions = [str(project['mainPrompt'])] if project.get('mainPrompt') else []
        expected_instructions.extend(node['prompt'] for node in project['nodes']
                                     if node.get('prompt') and len(node['prompt'])<4000
                                     and not node['prompt'].lstrip().startswith(('{', '<html')))
        instruction_evidence = normalized('\n'.join(item.get('text', '') for item in result.get('items', [])))
        instruction_coverage = dict(expected=len(expected_instructions),
                                    included=sum(normalized(instruction) in instruction_evidence for instruction in expected_instructions))
        instruction_coverage['allIncluded'] = instruction_coverage['expected'] == instruction_coverage['included']
        catalogue = json.loads(Path(__file__).with_name('venture-pricing.json').read_text(encoding='utf-8'))
        def cost(count):
            fake = dict(model='gpt-6-astra', service_tier='default', output=[],
                        usage=dict(input_tokens=count, output_tokens=0,
                                   input_tokens_details=dict(cached_tokens=0, cache_write_tokens=0)))
            return estimate(fake, 'gpt-6-astra', catalogue)['micro']/1_000_000
        before, reference, after = token_count(baseline_context), token_count(full_reference), token_count(text)
        # A second, full-source request makes insufficient initial retrieval
        # visible. It is not silently merged into the optimized token result.
        full = engine.prepare('benchmark-owner', 'benchmark-project', 1, QUERY, budget=1_500_000, mode='full')
        full_text = full.get('text', '')
        # Exercise the real tool-output formatting and unit pagination. This is
        # deterministic source selection, not an unmeasured claim that a paid
        # model would discover the correct source or write a correct script.
        targets = [source for source in indexed['manifest']['sourceInventory']
                   if source['name'].lower().endswith('.pdf') or source['name'] == 'operations.txt']
        expanded_texts, expanded_contents, expansion_calls, expansion_complete = [], [], 0, True
        for source in targets:
            offset = 0
            for _ in range(30):
                value, media = integration.execute(row, dict(name='vision_context', arguments=json.dumps(
                    dict(operation='source', query='', reference=source['id'], offset=offset))), key='')
                expanded_texts.append(json.dumps(value, ensure_ascii=False, separators=(',', ':')))
                expanded_contents.append(value.get('text') or '\n'.join(item.get('text', '') for item in value.get('items') or []))
                expansion_calls += 1
                next_offset = value.get('nextOffset')
                if next_offset is None:
                    expansion_complete = expansion_complete and bool(value.get('complete'))
                    break
                if not isinstance(next_offset, int) or next_offset <= offset:
                    expansion_complete = False
                    break
                offset = next_offset
            else:
                expansion_complete = False
        expanded_evidence = text+'\n'+'\n'.join(expanded_contents)
        if metadata['kind']=='synthetic':
            focused_record = ground[len(ground)//2]
            term = re.search(r'TEST-\d+', focused_record['notes'])[0]
            focused_query, focused_kind = 'What is specified for '+term+'?', 'exact-identifier'
        else:
            focused_record = next((item for item in ground if 'ADHESIVE' in item['notes'].upper()), ground[-1])
            focused_query = 'Which bonding compound joins the patch to the aircraft surface?'
            focused_kind = 'semantic-paraphrase'
        began = time.perf_counter()
        focused_text, focused_tools = integration.prepare({**row, 'message': focused_query})
        focused_seconds = time.perf_counter()-began
        focused_baseline = baseline.retrieve('benchmark', focused_query, tokens)
        changed = json.loads(json.dumps(project))
        changed['title'] += ' revised'
        began = time.perf_counter()
        incremental = engine.index_project('benchmark-owner', 'benchmark-project', 2, changed)
        incremental_seconds = time.perf_counter()-began
        changed['nodes'][0]['caption'] = 'One changed source in the benchmark.'
        began = time.perf_counter()
        changed_source = engine.index_project('benchmark-owner', 'benchmark-project', 3, changed)
        changed_source_seconds = time.perf_counter()-began
        engine.close()
        return dict(dataset=metadata, tokenizer=dict(library='tiktoken', encoding=encoding.name,
                    status='exact-text-tokenization', providerModelTokenizerVerified=False),
                    baseline=dict(method=baseline_method,
                                  includeBoardPromptTokens=token_count(full_prompt), storedProjectPromptTokens=token_count(full_prompt[:250000]),
                                  projectPromptSentDirectly=False, existingFtsExcerptTokens=token_count(excerpt),
                                  textContextTokens=before, preparationAndRetrievalSeconds=baseline_seconds,
                                  excerptRecordCoverage=record_coverage(excerpt, ground),
                                  textContextRecordCoverage=record_coverage(baseline_context, ground)),
                    fullContextReference=dict(textTokens=reference, kind='counterfactual single MAIN_PROMPT plus prepared page/OCR text, without duplicate module prompts or table projections',
                                              recordCoverage=record_coverage(full_reference, ground)),
                    hybrid=dict(initialTextTokens=after, toolSchemaTokens=token_count(json.dumps(tools, separators=(',', ':'))),
                                reductionVersusFullTextReferencePercent=round((1-after/reference)*100, 3) if reference else None,
                                changeVersusExistingInitialTextPercent=round((after/before-1)*100, 3) if before else None,
                                recordCoverage=record_coverage(text, ground), engineComplete=result.get('complete'),
                                instructionCoverage=instruction_coverage,
                                warningCount=len(result.get('warnings') or []), metrics=result.get('metrics'),
                                indexSeconds=index_seconds, retrievalSeconds=retrieval_seconds,
                                peakProcessTreeRssBytes=memory.peak, indexStatus=status_summary(indexed),
                                incrementalSeconds=incremental_seconds, incrementalStatus=status_summary(incremental),
                                changedSourceSeconds=changed_source_seconds, changedSourceStatus=status_summary(changed_source)),
                    fullSourceCheck=dict(textTokens=token_count(full_text), recordCoverage=record_coverage(full_text, ground),
                                         engineComplete=full.get('complete'), warningCount=len(full.get('warnings') or [])),
                    targetedExpansion=dict(method='Deterministic selection of PDF/operations source from server inventory, using actual integration tool outputs and pagination; not model-selected.',
                                           sourceCount=len(targets), toolCalls=expansion_calls,
                                           additionalToolOutputTokens=sum(token_count(value) for value in expanded_texts),
                                           initialPlusToolOutputTokens=after+sum(token_count(value) for value in expanded_texts),
                                           recordCoverage=record_coverage(expanded_evidence, ground), engineComplete=expansion_complete,
                                           billingNote='Evidence token count only. Continuation API input also includes retained conversation; it is not equal to this sum.'),
                    focusedQuery=dict(kind=focused_kind, initialTextTokens=token_count(focused_text),
                                      retrievalSeconds=focused_seconds, recordCoverage=record_coverage(focused_text, [focused_record]),
                                      baselineExcerptTokens=token_count(focused_baseline),
                                      baselineRecordCoverage=record_coverage(focused_baseline, [focused_record]),
                                      reductionVersusFullTextReferencePercent=round((1-token_count(focused_text)/reference)*100, 3)),
                    cost=dict(kind='counterfactual-input-only-estimate', catalogVersion=catalogue['version'],
                              model='gpt-6-astra', existingInitialUsd=cost(before), fullTextReferenceUsd=cost(reference), hybridInitialUsd=cost(after),
                              actualProviderUsage=None, paidRequestsMade=0,
                              excludes=['output', 'reasoning output', 'image tokens', 'tool definitions', 'conversation history',
                                        'continuation requests', 'provider cache effects', 'Code Interpreter']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-zip', type=Path)
    parser.add_argument('--model-path', type=Path, help='Optional preinstalled local embedding model; never downloaded by this script.')
    parser.add_argument('--model-packages', type=Path, help='Optional existing embedding dependency directory installed by Context-Tools.ps1.')
    parser.add_argument('--output', type=Path, help='Aggregate JSON destination. Source material is never included.')
    args = parser.parse_args()
    import tiktoken
    import psutil
    encoding = tiktoken.get_encoding('o200k_base')
    cases = [synthetic_project(13), synthetic_project(100)]
    if args.project_zip:
        cases.append(private_export(args.project_zip))
    report = dict(schema='vision-context-benchmark-v1', host=dict(platform=platform.system(), python=platform.python_version(),
                  logicalCpus=psutil.cpu_count(), ramBytes=psutil.virtual_memory().total,
                  isUserWindowsServer=False), limitations=[
                  'These are local text-retrieval measurements, not paid model quality or official API billing measurements.',
                  'o200k_base is an explicit reference encoding; tokenizer compatibility for every selectable model is not assumed.',
                  'Memory includes this Python process and child converters sampled every 10 ms, not dedicated Windows hardware profiling.',
                  'No semantic quality claim applies unless a real local embedding model is explicitly configured.'], cases=[])
    report['implementationSha256'] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ('context_engine.py', 'context_sources.py', 'context_embeddings.py', 'context_integration.py', 'venture_files.py', 'venture_billing.py')}
    for case in cases:
        report['cases'].append(run_case(*case, encoding, args.model_path,
                                       args.project_zip if case[-1]['kind']=='private-prompt-export' else None,
                                       args.model_packages))
    encoded = json.dumps(report, indent=2, ensure_ascii=False)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding='utf-8')
        print('Aggregate benchmark report written; '+str(len(cases))+' cases completed.')
    else:
        print(encoded)


if __name__ == '__main__':
    main()
