"""Real local PDF rendering and binary preflight; no provider requests."""
import base64
import io
import tempfile
from pathlib import Path
import unittest
import zipfile

from context_visual import inspect_office, inspect_pdf, pdf_page


class ContextVisualTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def pdf(self,text='OPN 0030 WC 2CU0SA Run Hrs 1.50',pages=2):
        try:
            from reportlab.pdfgen import canvas
            import pdfplumber
            import pypdfium2
        except ImportError:self.skipTest('Optional document dependencies unavailable')
        path=self.root/'operations.pdf'
        output=canvas.Canvas(str(path))
        for page in range(pages):
            output.drawString(30,750,text+' / page '+str(page+1));output.showPage()
        output.save();return path
    def office(self,entries):
        path=self.root/'operations.docx'
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('[Content_Types].xml','<Types/>')
            archive.writestr('word/document.xml','<document><p>OPN 0030 WC 2CU0SA</p></document>')
            for name,data in entries.items():archive.writestr(name,data)
        return path
    def test_selected_pdf_page_renders_real_pixels_with_provenance(self):
        from PIL import Image
        path=self.pdf()
        result=pdf_page(path,1)
        self.assertEqual(result['page'],2)
        self.assertEqual(result['mime'],'image/jpeg')
        with Image.open(io.BytesIO(base64.b64decode(result['data']))) as image:
            self.assertGreater(image.width,100)
            self.assertFalse(image.getexif())
        with self.assertRaises(ValueError):pdf_page(path,5)
    def test_detected_pdf_credentials_refuse_raw_transfer_and_visual_page(self):
        path=self.pdf('api_key="sk-sensitive-test-value-123456789"',1)
        with self.assertRaises(ValueError):inspect_pdf(path)
        with self.assertRaises(ValueError):pdf_page(path,0)
    def test_plain_pdf_inspection_retains_honest_visual_limit(self):
        result=inspect_pdf(self.pdf())
        self.assertTrue(result['approved'])
        self.assertIn('not proven absent',result['warning'])
    def test_office_preflight_rejects_macros_external_relationships_credentials(self):
        cases=[{'word/vbaProject.bin':b'bad'},
               {'word/_rels/document.xml.rels':'<Relationships><Relationship TargetMode="External" Target="https://example.com"/></Relationships>'},
               {'word/secret.xml':'<p>api_key="sk-sensitive-test-value-123456789"</p>'},
               {'word/embeddings/payload.bin':b'bad'},
               {'word/unknown.bin':b'bad'}]
        for entries in cases:
            with self.subTest(entries=list(entries)):
                with self.assertRaises(ValueError):inspect_office(self.office(entries),'operations.docx')
    def test_plain_office_preflight(self):
        self.assertTrue(inspect_office(self.office({}),'operations.docx')['approved'])


if __name__=='__main__':unittest.main()
