"""Download only the PDF layout/table models; verify actual offline extraction."""
import argparse
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def sample_pdf():
    # Small native-text PDF independent of extra PDF-generation libraries.
    stream = b'BT /F1 20 Tf 50 700 Td (Vision document test 42) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    data, offsets = b'%PDF-1.4\n', [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data)); data += f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref = len(data)
    data += f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    data += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    return data+f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--options', required=True)
    parser.add_argument('--worker', required=True)
    parser.add_argument('--download', action='store_true')
    args=parser.parse_args()
    options=json.loads(Path(args.options).read_text(encoding='utf-8-sig'))
    if args.download and options['profile']=='Full':
        from docling.utils.model_downloader import download_models
        path=Path(options['artifactsPath']); path.mkdir(parents=True, exist_ok=True)
        kwargs={key:False for key in inspect.signature(download_models).parameters if key.startswith('with_')}
        kwargs.update(output_dir=path, with_layout=True, with_tableformer=True, progress=True)
        download_models(**kwargs)
    import markitdown, docx, pptx, openpyxl, pdfplumber, pypdfium2, pytesseract
    from PIL import Image, ImageDraw
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder)
        image=Image.new('RGB',(700,110),'white')
        ImageDraw.Draw(image).text((20,25),'Vision document test 42',fill='black',font_size=32)
        image.save(root/'ocr.png')
        (root/'test.pdf').write_bytes(sample_pdf())
        (root/'test.html').write_text('<h1>Vision document test 42</h1>',encoding='utf-8')
        for name in ('ocr.png','test.pdf','test.html'):
            result=root/'result.json'
            subprocess.run([sys.executable,args.worker,'--source',str(root/name),'--name',name,'--output',str(result),'--options',args.options,'--diagnostics'],check=True,timeout=1200)
            data=json.loads(result.read_text(encoding='utf-8'))
            content=' '.join(a.get('text','') for a in data['artifacts'])
            if '42' not in content: raise RuntimeError('Extraction self-test did not recover expected content: '+name)
            if name=='test.pdf' and options['profile']=='Full' and 'Docling' not in data['tools']:
                raise RuntimeError('Offline Docling layout self-test failed; activation canceled.')
        # Verify the Java fallback executable and jar, including compatibility.
        subprocess.run([options['javaPath'],'-Xmx512m','-jar',options['tikaPath'],'--text',str(root/'test.html')],
                       check=True,timeout=120,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    print('PASS: local PDF, OCR, HTML/MarkItDown, Office imports, and Apache Tika'+(' / offline Docling' if options['profile']=='Full' else ''))


if __name__=='__main__': main()
