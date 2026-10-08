"""Fixed local subprocess for selected PDF pages and binary transfer preflight.

No network, uploaded commands, or executable Office content. Successful scanning
means no detected text credentials/active payloads; it cannot prove that pixels
or unusual binary encodings contain no private information.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from context_sources import redact, validate_archive
from document_worker import no_network


PDF_UNSAFE = frozenset(('JavaScript','JS','Launch','EmbeddedFile','EmbeddedFiles','RichMedia',
                        'OpenAction','AA','AcroForm','XFA','SubmitForm','ImportData','URI'))


def decoded(value):
    if isinstance(value, bytes):
        for encoding in ('utf-8','utf-16','latin-1'):
            try:return value.decode(encoding)
            except UnicodeDecodeError:pass
    return str(value)


def inspect_pdf(path):
    import pdfplumber
    from pdfminer.psparser import PSLiteral
    count=0
    def scan(value,depth=0):
        nonlocal count
        count+=1
        if count>100000 or depth>30:raise ValueError('PDF object inspection limit exceeded.')
        if isinstance(value,dict):
            if any(str(key) in PDF_UNSAFE for key in value):raise ValueError('PDF has active forms, actions or embedded content.')
            for key,item in value.items():scan(item,depth+1)
        elif isinstance(value,(list,tuple)):
            for item in value:scan(item,depth+1)
        elif isinstance(value,PSLiteral):
            if str(value.name) in PDF_UNSAFE:raise ValueError('PDF contains an active or embedded payload.')
        elif isinstance(value,(str,bytes)):
            text=decoded(value)
            if redact(text)!=text:raise ValueError('Potential credential in PDF content or metadata.')
        elif hasattr(value,'attrs'):
            scan(value.attrs,depth+1)
    with pdfplumber.open(path) as pdf:
        if pdf.doc.is_extractable is False or len(pdf.pages)>100:
            raise ValueError('PDF is protected or exceeds the 100-page transfer inspection limit.')
        scan(pdf.metadata)
        objects=set()
        for xref in pdf.doc.xrefs:
            objects.update(xref.get_objids())
            if len(objects)>50000:raise ValueError('PDF object count limit exceeded.')
        for oid in objects:scan(pdf.doc.getobj(oid))
        for page in pdf.pages:
            text=page.extract_text() or ''
            if len(text)>2_000_000 or redact(text)!=text:
                raise ValueError('PDF text contains potential credentials or exceeds the inspection limit.')
    return dict(approved=True,warning='No credentials or active payloads were detected in inspected PDF text/objects. Visual or unusually encoded secrets are not proven absent.')


def inspect_office(path,name):
    prefix={'.docx':'word/','.xlsx':'xl/','.pptx':'ppt/'}[Path(name).suffix.lower()]
    with zipfile.ZipFile(path) as archive:
        members=validate_archive(archive,{'entries':0,'expanded':0})
        names={item.filename for item in members}
        if '[Content_Types].xml' not in names or not any(n.startswith(prefix) for n in names):
            raise ValueError('Not a valid Office document package.')
        for item in members:
            name=item.filename.lower()
            if item.is_dir():continue
            if any(v in name for v in ('vbaproject','/embeddings/','/externallinks/','activex','customxml','oleobject')):
                raise ValueError('Office document contains executable, embedded, or external content.')
            if name.endswith(('.xml','.rels')):
                if item.file_size>8*1024*1024:raise ValueError('Office XML inspection limit exceeded.')
                data=archive.read(item)
                if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():raise ValueError('XML entities are unsupported.')
                root=ET.fromstring(data)
                text=' '.join(root.itertext())
                text+=' '+json.dumps([element.attrib for element in root.iter()],ensure_ascii=False)
                if redact(text)!=text:raise ValueError('Potential credential in Office content or relationships.')
                for element in root.iter():
                    if element.attrib.get('TargetMode','').lower()=='external':
                        raise ValueError('Office document contains external relationships.')
            elif '/media/' not in name and not name.startswith('docprops/thumbnail.'):
                raise ValueError('Unknown binary member in Office document.')
    return dict(approved=True,warning='No credentials, macros, embedded objects or external relationships were detected in Office XML. Image contents are not proven free of secrets.')


def pdf_page(path,page):
    import pdfplumber
    import pypdfium2 as pdfium
    from PIL import Image
    with pdfplumber.open(path) as pdf:
        if not 0<=page<len(pdf.pages):raise ValueError('PDF page does not exist.')
        text=pdf.pages[page].extract_text() or ''
        if redact(text)!=text:raise ValueError('Selected PDF page contains detected credentials; use sanitized text.')
    with pdfium.PdfDocument(path) as document:
        if not 0<=page<len(document):raise ValueError('PDF page does not exist.')
        pdfpage=document[page]
        try:
            width,height=pdfpage.get_width(),pdfpage.get_height()
            if not 0<width<100000 or not 0<height<100000:raise ValueError('Invalid PDF page dimensions.')
            bitmap=pdfpage.render(scale=min(2.,2400/max(width,height)))
            try:
                picture=bitmap.to_pil().convert('RGB')
                clean=Image.new('RGB',picture.size);clean.paste(picture)
                buffer=io.BytesIO();clean.save(buffer,'JPEG',quality=95)
                return dict(page=page+1,mime='image/jpeg',data=base64.b64encode(buffer.getvalue()).decode(),
                            warning='Page pixels rendered locally; hidden PDF metadata and attachments were excluded. Visual contents may contain private information.')
            finally:bitmap.close()
        finally:pdfpage.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',required=True);parser.add_argument('--name',required=True)
    parser.add_argument('--output',required=True);parser.add_argument('--operation',choices=('inspect','page'),required=True)
    parser.add_argument('--page',type=int,default=0)
    args=parser.parse_args()
    no_network()
    path=Path(args.source)
    if not path.is_file() or path.stat().st_size>25*1024*1024:raise ValueError('Source size limit exceeded.')
    suffix=Path(args.name).suffix.lower()
    if args.operation=='page':
        if suffix!='.pdf':raise ValueError('Page rendering requires a PDF.')
        result=pdf_page(path,args.page)
    elif suffix=='.pdf':result=inspect_pdf(path)
    elif suffix in ('.docx','.xlsx','.pptx'):result=inspect_office(path,args.name)
    else:raise ValueError('Unsupported binary transfer type.')
    Path(args.output).write_text(json.dumps(result),encoding='utf-8')


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit(2)
