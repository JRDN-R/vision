"""Bounded local source parsing for the derived context index.

Original bytes are stored separately by ContextEngine. Every extraction carries
limitations. Records are indivisible retrieval units; generic text has explicit
line ranges. Uploaded HTML/code is parsed as data and is never executed.
"""
from __future__ import annotations

import base64
import csv
import html
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import tempfile
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import zipfile

PARSER_VERSION = 'context-sources-v6'
MAX_SOURCE = 50 * 1024 * 1024
MAX_TEXT = 4_000_000
TEXT = set('.txt .md .csv .tsv .json .xml .html .htm .har .log .yaml .yml .ipynb .py .js .ts .jsx .tsx .css .scss .sql .ps1 .sh .c .h .cpp .cs .java .rs .go .ini .toml .tex .svg'.split())
IMAGE = set('.jpg .jpeg .png .webp .bmp .gif .tif .tiff'.split())
SECRET_KEY = re.compile(r'(?:authorization|proxy.authorization|cookie|set.cookie|password|passwd|secret|api.?key|access.?token|refresh.?token|id.?token|auth.?token|jwt|session(?:id|token)?|csrf|xsrf|credential|signature|client.?secret)', re.I)
SECRET_FIELD = r'(?:authorization|proxy[-_]?authorization|set[-_]?cookie|cookie|password|passwd|api[-_]?key|access[-_]?token|refresh[-_]?token|id[-_]?token|auth[-_]?token|jwt|client[-_]?secret|session(?:[-_]?(?:id|token))?|csrf(?:[-_]?token)?|xsrf(?:[-_]?token)?|credential|signature|secret|token)'
ENCODED_OMITTED = '[ENCODED PAYLOAD OMITTED; inspect locally]'


class ExtractionBudgetExceeded(RuntimeError):
    """Stop a generation, rather than publishing a silently partial index."""


class ExtractionBudget:
    def __init__(self, limit):
        self.limit = max(1024,min(128*1024*1024,int(limit)))
        self.used = 0

    @property
    def remaining(self):
        return self.limit-self.used

    def reserve(self, count):
        if count>self.remaining:
            raise ExtractionBudgetExceeded('Project derived-text budget exceeded ('+str(self.limit)+' UTF-8 bytes). Remaining sources were not indexed; the previous valid index and original project are retained. Increase maxProjectTextBytes within its 128 MiB ceiling or split the project.')
        self.used += count

    def retain(self, result, already_reserved=0):
        # Count incrementally without materializing another potentially large
        # serialized copy. safeText, units and location metadata all count.
        size = 0
        for part in json.JSONEncoder(ensure_ascii=False,separators=(',', ':')).iterencode(result):
            size += len(part.encode('utf-8'))
            if size-already_reserved>self.remaining:
                self.reserve(size-already_reserved)
        self.reserve(max(0,size-already_reserved))


def redact(text):
    """Best effort text redaction in addition to structured HAR field removal."""
    text = str(text)
    # HARs are often wrapped in a .txt export. URL redaction must also cover
    # prose, attributes, comments, and filenames, not only a HAR's `url` field.
    text = re.sub(r'(?i)\b(?:https?|wss?|ftp)://[^\s<>\"\']+', lambda m: clean_url(m[0]), text)
    text = re.sub(r'(?is)([\"\']?\b'+SECRET_FIELD+r'[\"\']?\s*[:=]\s*)([\"\'])(.*?)\2',
                  r'\1\2[REDACTED]\2', text)
    # Credentials in copied form markup are not JSON property assignments.
    def form_field(match):
        tag = match[0]
        fields = re.findall(r'(?i)\b(?:name|id)\s*=\s*[\"\']([^\"\']+)[\"\']', tag)
        if any(SECRET_KEY.search(v) or v.lower() == 'token' for v in fields):
            tag = re.sub(r'(?is)(\b(?:value|content)\s*=\s*)([\"\'])(.*?)\2', r'\1\2[REDACTED]\2', tag)
            tag = re.sub(r'(?i)(\b(?:value|content)\s*=\s*)(?![\"\'])[^\s>]+', r'\1[REDACTED]', tag)
        return tag
    text = re.sub(r'(?is)<(?:input|meta)\b[^>]*>', form_field, text)
    text = re.sub(r'(?im)\b(authorization|proxy-authorization|cookie|set-cookie)\s*:\s*[^\r\n]+', r'\1: [REDACTED]', text)
    text = re.sub(r'(?i)([\"\']?(?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|session[_-]?(?:id|token)|secret)[\"\']?\s*[:=]\s*)([\"\'])(.*?)\2', r'\1\2[REDACTED]\2', text)
    text = re.sub(r'(?i)(\b(?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|secret)\s*=\s*)[^\s&;,]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)([\"\']?\b'+SECRET_FIELD+r'[\"\']?\s*[:=]\s*)(?![\"\'])[A-Za-z0-9_./+=:-]+',
                  r'\1[REDACTED]', text)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b', '[REDACTED]', text)
    text = re.sub(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', '[REDACTED PRIVATE KEY]', text, flags=re.S)
    text = re.sub(r'(?i)([?&](?:token|key|password|secret|session|api_key|access_token|signature)=)[^&#\s"\']+', r'\1[REDACTED]', text)
    return text


def clean_url(url):
    try:
        parts = urlsplit(html.unescape(str(url)))
        # Credentials can be embedded before @, even on non-HTTP URLs.
        netloc = parts.netloc.rsplit('@', 1)[-1]
        query = urlencode([(k, '[REDACTED]' if SECRET_KEY.search(k) or k.lower() in ('key', 'token') else v)
                           for k, v in parse_qsl(parts.query, keep_blank_values=True)])
        return urlunsplit((parts.scheme, netloc, parts.path, query, ''))
    except ValueError:
        return '[INVALID URL]'


def sanitize(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == 'text' and str(value.get('encoding', '')).lower() == 'base64':
                # Do not let an encoded response or request body bypass field
                # sanitization, even inside generic JSON or a wrapped HAR.
                out[key] = ENCODED_OMITTED
            elif SECRET_KEY.search(str(key)) or str(key).lower() in ('token', 'key'):
                out[key] = '[REDACTED]'
            elif key in ('url', 'redirectURL'):
                out[key] = clean_url(item)
            elif key in ('headers', 'queryString', 'params') and isinstance(item, list):
                out[key] = [dict(name=str(v.get('name', '')), value='[REDACTED]' if SECRET_KEY.search(str(v.get('name', ''))) or str(v.get('name', '')).lower() in ('key', 'token') else redact(v.get('value', '')))
                            for v in item if isinstance(v, dict)]
            elif key == 'text' and isinstance(item, str):
                try:
                    out[key] = json.dumps(sanitize(json.loads(item)), ensure_ascii=False)
                except (ValueError, TypeError, RecursionError):
                    out[key] = redact(item)
            else:
                out[key] = sanitize(item)
        return out
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return redact(value) if isinstance(value, str) else value


def embedded_har(text):
    """Redact complete HAR objects embedded in text while retaining wrappers.

    Never repeatedly decode every opening brace in a large source. Candidate
    positions are limited to recognizable HAR objects, and replacements are
    performed from right to left so offsets remain stable.
    """
    matches = list(re.finditer(r'\{\s*\"log\"\s*:', text))[:32]
    replacements = []
    decoder = json.JSONDecoder()
    for match in matches:
        try:
            value, length = decoder.raw_decode(text[match.start():])
            if isinstance(value, dict) and isinstance(value.get('log'), dict) and isinstance(value['log'].get('entries'), list):
                replacements.append((match.start(), match.start()+length, json.dumps(sanitize(value), ensure_ascii=False, indent=2)))
        except (ValueError, TypeError, RecursionError):
            continue
    for start, end, value in reversed(replacements):
        text = text[:start]+value+text[end:]
    return text


def decode_data(data):
    if not isinstance(data, str) or not re.match(r'^data:[^,]*;base64,', data):
        raise ValueError('Attachment is missing a supported inline payload.')
    head, payload = data.split(',', 1)
    if len(payload) > (MAX_SOURCE * 4 // 3 + 8):
        raise ValueError('Attachment exceeds the local 50 MB indexing limit.')
    raw = base64.b64decode(payload, validate=True)
    if len(raw) > MAX_SOURCE:
        raise ValueError('Attachment exceeds the local 50 MB indexing limit.')
    return raw, head[5:].split(';')[0]


def text_units(text, location='', name=''):
    """Preserve entire operations/tables/JSON records; split prose only by lines."""
    text = redact(text)
    suffix = Path(name).suffix.lower()
    if suffix in ('.csv', '.tsv'):
        rows = list(csv.reader(io.StringIO(text), delimiter='\t' if suffix == '.tsv' else ','))
        if rows:
            header = rows[0]
            return [dict(text=json.dumps({'columns': header, 'values': row}, ensure_ascii=False),
                         location=f'{location} / row {i+2}', kind='record', group='table:1')
                    for i, row in enumerate(rows[1:]) if any(row)] or [dict(text=text, location=location, kind='table', group='table:1')]
    if suffix == '.json':
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list) and parsed:
                return [dict(text=json.dumps(v, ensure_ascii=False, separators=(',', ':')), location=f'{location} / record {i+1}', kind='record', group='records') for i, v in enumerate(sanitize(parsed))]
        except (ValueError, RecursionError):
            pass
    # Operations frequently have multiline notes. Keep every field until the
    # next operation number, even where the record is larger than a text chunk.
    opns = list(re.finditer(r'(?im)^\s*(?:OPN|OPERATION(?:\s+NO\.?)?)\s*[:#-]?\s*(\d{3,6})\b', text))
    if not opns and re.search(r'(?i)\bOp(?:eratio)?n\s+Notes\s*:',text):
        # Tables can put operation identifiers beneath a column heading, then
        # carry multiline notes. Require both the notes label and a row with
        # operation, optional order, work center, description and decimal hours.
        opns = list(re.finditer(r'(?m)^\s*(\d{3,6})[ \t]+(?:\d{4,8}[ \t]+)?[A-Z0-9]{4,10}[ \t]+.+?[ \t]+\d+\.\d{1,3}(?:[ \t]+[^\n]*)?$',text))
    if opns:
        result = []
        if text[:opns[0].start()].strip():
            result.append(dict(text=text[:opns[0].start()], location=location+' / heading', kind='text', group='operations'))
        for i, match in enumerate(opns):
            result.append(dict(text=text[match.start():opns[i+1].start() if i+1<len(opns) else len(text)], location=location+' / OPN '+match[1], kind='record', group='operations', identifier=match[1]))
        return result
    if len(text) <= 3500:
        return [dict(text=text, location=location, kind='text', group='document')] if text.strip() else []
    # Code boundaries get preferred breaks, while the full source remains
    # accessible by source ID. Chunks are never represented as full files.
    lines = text.splitlines(keepends=True)
    result, block, start, size = [], [], 1, 0
    for i, line in enumerate(lines, 1):
        boundary = bool(re.match(r'^\s*(?:def |class |async def |function |export |(?:const|let) \w+\s*=.*=>)', line))
        if block and (size > 3500 or (boundary and size > 800)):
            result.append(dict(text=''.join(block), location=f'{location} / lines {start}-{i-1}', kind='text', group='document'))
            block, start, size = [], i, 0
        block.append(line)
        size += len(line)
    if block:
        result.append(dict(text=''.join(block), location=f'{location} / lines {start}-{len(lines)}', kind='text', group='document'))
    return result


def validate_archive(archive, limits):
    members = archive.infolist()
    limits['entries'] += len(members)
    limits['expanded'] += sum(i.file_size for i in members)
    if limits['entries'] > 2000 or limits['expanded'] > 128 * 1024 * 1024:
        raise ValueError('Archive exceeds the shared 2,000 entry / 128 MB expanded limit.')
    for item in members:
        name = item.filename.replace('\\', '/')
        if (name.startswith('/') or ':' in name or '..' in PurePosixPath(name).parts or item.flag_bits & 1 or
            (item.external_attr >> 16) & 0o170000 == 0o120000 or item.file_size > MAX_SOURCE or
            item.file_size > max(1024*1024, item.compress_size*200)):
            raise ValueError('Archive contains unsafe, encrypted, or over-compressed members.')
    return members


def local_document_process(args, directory, timeout=300, stop_event=None):
    """Bound a fixed converter's entire process tree and working directory.

    Windows uses the same kill-on-close job wrapper as existing media workers.
    POSIX process groups provide the corresponding cleanup in offline tests.
    No uploaded value is evaluated as a command or written into a log.
    """
    from uploaded_media import WindowsJob
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               HF_HUB_DISABLE_TELEMETRY='1', OMP_NUM_THREADS='2', MKL_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2')
    flags = ({'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS}
             if os.name == 'nt' else {'start_new_session': True})
    process = guard = None
    try:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, env=env, **flags)
        guard = WindowsJob(process)
        deadline = time.monotonic()+timeout
        while process.poll() is None:
            if stop_event and stop_event.is_set():
                raise InterruptedError('Local context extraction interrupted.')
            if time.monotonic() >= deadline:
                raise ValueError('Local document extraction exceeded its time limit.')
            # Raw original + safe converter copy + generated evidence are capped
            # together. No unbounded stdout/stderr files are ever created.
            working_bytes = 0
            for path in directory.rglob('*'):
                try:
                    if path.is_file():
                        working_bytes += path.stat().st_size
                except FileNotFoundError:
                    pass  # The converter may remove a temporary page concurrently.
            if working_bytes > 256*1024*1024:
                raise ValueError('Local document extraction exceeded its working-storage limit.')
            output = directory/'result.json'
            if output.is_file() and output.stat().st_size > 24*1024*1024:
                raise ValueError('Local document extraction exceeded its output limit.')
            if stop_event:
                stop_event.wait(.1)
            else:
                time.sleep(.1)
        if process.returncode:
            raise ValueError('Installed document extractor could not prepare the source.')
    finally:
        if guard:
            guard.close()  # Also kills descendants after the direct child exits.
        if process:
            if os.name != 'nt':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.kill()
            process.wait(timeout=10)


def extract_source(raw, name, settings=None, depth=0, limits=None):
    settings = settings or {}
    limits = limits or {'entries': 0, 'expanded': 0}
    suffix = Path(name).suffix.lower()
    result = dict(units=[], warnings=[], complete=True, visual=False, secretsRedacted=False)
    budget = settings.get('_projectTextBudget')
    reserved_before = budget.used if budget else 0
    if len(raw) > MAX_SOURCE:
        return dict(result, warnings=['Source exceeds the 50 MB indexing limit. Original is retained.'], complete=False)
    try:
        if suffix == '.zip':
            if depth >= 3:
                raise ValueError('Nested archive depth exceeds three; original retained.')
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = validate_archive(archive, limits)
                archive_text_count = 0
                for item in members:
                    if item.is_dir():
                        continue
                    child = extract_source(archive.read(item), item.filename, settings, depth+1, limits)
                    for unit in child['units']:
                        prior_location = unit['location']
                        unit['location'] = item.filename + (' / '+unit['location'] if unit['location'] else '')
                        if budget:
                            budget.reserve(max(0,len(unit['location'].encode('utf-8'))-len(prior_location.encode('utf-8'))))
                    archive_text_count += sum(len(unit['text']) for unit in child['units'])
                    if archive_text_count>MAX_TEXT:
                        raise ValueError('Archive text exceeds the 4,000,000-character index limit; original retained.')
                    result['units'].extend(child['units'])
                    result['warnings'].extend(child['warnings'])
                    result['complete'] = result['complete'] and child['complete']
                    result['visual'] = result['visual'] or child['visual']
                    result['secretsRedacted'] = result['secretsRedacted'] or child.get('secretsRedacted',False)
        elif suffix in TEXT:
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                try:
                    text = raw.decode('utf-16')
                except UnicodeDecodeError:
                    text = raw.decode('utf-8', errors='replace')
                    result['warnings'].append('Some source characters could not be decoded; inspect original encoding.')
                    result['complete'] = False
            if len(text) > MAX_TEXT:
                text = text[:MAX_TEXT]
                result['warnings'].append('Only the first 4,000,000 text characters were indexed; original retained.')
                result['complete'] = False
            if suffix == '.har':
                har = json.loads(text)
                if not isinstance(har, dict) or not isinstance(har.get('log'), dict):
                    raise ValueError('HAR root is malformed.')
                entries = har.get('log', {}).get('entries', [])
                if not isinstance(entries, list):
                    raise ValueError('HAR entries are malformed.')
                result['safeText'] = redact(json.dumps(sanitize(har),ensure_ascii=False,indent=2))
                for index, entry in enumerate(entries):
                    if not isinstance(entry, dict):
                        raise ValueError('HAR entry is malformed.')
                    # Base64 content may conceal credentials; do not index it.
                    entry = sanitize(entry)
                    if ENCODED_OMITTED in json.dumps(entry):
                        result['warnings'].append('Encoded HAR response payload was excluded from AI evidence.')
                        result['complete'] = False
                    result['units'].append(dict(text=json.dumps(entry, ensure_ascii=False, separators=(',', ':')), location=f'request {index+1}', kind='record', group='har'))
            else:
                # Type labels are untrusted: JSON and HAR commonly arrive as
                # .txt files or inside an HTML/Markdown source-export wrapper.
                try:
                    text = json.dumps(sanitize(json.loads(text)), ensure_ascii=False, indent=2)
                except (ValueError, RecursionError):
                    text = embedded_har(text)
                if ENCODED_OMITTED in text:
                    result['warnings'].append('Encoded source payload was excluded from AI evidence.')
                    result['complete'] = False
                result['units'] = text_units(text, name=name)
                result['safeText'] = redact(text)
            original_marks = len(re.findall(r'\[REDACTED\b',raw.decode('utf-8',errors='replace')))
            result['secretsRedacted'] = len(re.findall(r'\[REDACTED\b',result.get('safeText','')))>original_marks
        elif suffix in IMAGE:
            result.update(visual=True, complete=False)
            result['warnings'].append('Image pixels are retained but text retrieval cannot establish visual completeness. Retrieve the original image for visual inspection.')
            result['units'] = [dict(text='Original image: '+name, location='image', kind='visual', group='image')]
        else:
            document = settings.get('documentSettings') or {}
            python = Path(document.get('pythonPath') or '__not_installed__')
            if not python.is_file() or not document.get('enabled'):
                result['complete'] = False
                result['warnings'].append('Local extractor is unavailable for '+suffix+'; original retained. Rebuild after enabling document processing.')
            else:
                with tempfile.TemporaryDirectory(prefix='vision-context-') as tmp:
                    work = Path(tmp)
                    source, output, options = work / ('source'+suffix), work/'result.json', work/'options.json'
                    source.write_bytes(raw)
                    options.write_text(json.dumps(document), encoding='utf-8')
                    from media import MEDIA_LOCK
                    stop_event = settings.get('_stopEvent')
                    while not MEDIA_LOCK.acquire(timeout=.2):
                        if stop_event and stop_event.is_set():
                            raise InterruptedError('Local context extraction interrupted.')
                    try:
                        local_document_process([str(python), str(Path(__file__).with_name('document_worker.py')),
                            '--source', str(source), '--name', Path(name).name, '--output', str(output),
                            '--options', str(options)], work, stop_event=stop_event)
                    finally:
                        MEDIA_LOCK.release()
                    if not output.is_file() or output.stat().st_size > 24*1024*1024:
                        raise ValueError('Installed document extractor could not prepare the source.')
                    parsed = json.loads(output.read_text(encoding='utf-8'))
                    result['warnings'].extend(str(v) for v in parsed.get('warnings', []))
                    for artifact in parsed.get('artifacts', []):
                        if isinstance(artifact.get('text'), str):
                            result['secretsRedacted'] = result['secretsRedacted'] or len(re.findall(r'\[REDACTED\b',redact(artifact['text'])))>len(re.findall(r'\[REDACTED\b',artifact['text']))
                            result['units'].extend(text_units(artifact['text'], str(artifact.get('location', '')), str(artifact.get('name', ''))))
                        if artifact.get('data', '').startswith('data:image/'):
                            result['visual'] = True
                    if result['warnings'] or not result['units']:
                        result['complete'] = False
    except InterruptedError:
        raise
    except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile, RecursionError, TypeError) as error:
        result['complete'] = False
        result['warnings'].append('Source processing incomplete ('+type(error).__name__+'); inspect original or rebuild with compatible local tools.')
    for unit in result['units']:
        unit['text'] = redact(unit['text'])
        unit['location'] = redact(unit.get('location', ''))
    if result['secretsRedacted']:
        result['complete'] = False
        result['warnings'].append('Credential-like fields were redacted from derived evidence; original sensitive values remain local.')
    if any('[REDACTED' in unit['text'] or '[REDACTED' in unit['location'] for unit in result['units']):
        result['complete'] = False
        result['warnings'].append('Credential-like values were removed from derived evidence; byte-for-byte source reproduction is not complete. The local original is retained.')
    result['warnings'] = list(dict.fromkeys(redact(warning) for warning in result['warnings']))
    if budget:
        budget.retain(result,already_reserved=budget.used-reserved_before)
    return result
