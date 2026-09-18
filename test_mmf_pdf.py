import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from pypdf import PdfReader,PdfWriter
from pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject
from mmf_pdf import clean_pdf
from mmf_repack import repack
from release_images import command

STAMP=b'q 1 0 0 1 5 5 cm .475 .475 .475 rg 1 1 1 RG .1 w BT /F0 6 Tf 2 Tr 0 0 Td [(Buyer order 123)] TJ ET Q\n'
BODY=b'BT /F0 12 Tf 40 300 Td (Assembly instructions and artist credit) Tj ET\n'


def pdf(path,content):
    writer=PdfWriter();page=writer.add_blank_page(width=400,height=500)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F0'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(content);page[NameObject('/Contents')]=writer._add_object(stream)
    writer.add_metadata({'/Title':'Original title','/Author':'Original artist'})
    writer.write(path)


class PDFTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def test_order_stamp_removed_but_body_and_metadata_preserved(self):
        path=self.root/'guide.pdf';pdf(path,BODY+STAMP)
        result=clean_pdf(path);reader=PdfReader(path)
        self.assertTrue(result['changed']);self.assertNotIn('Buyer order',reader.pages[0].extract_text())
        self.assertIn('Assembly instructions and artist credit',reader.pages[0].extract_text())
        self.assertEqual(reader.metadata.author,'Original artist');self.assertEqual(reader.metadata.title,'Original title')
        before=path.read_bytes();self.assertFalse(clean_pdf(path)['changed']);self.assertEqual(before,path.read_bytes())
    def test_unrecognized_white_footer_and_image_draw_are_untouched(self):
        path=self.root/'guide.pdf';pdf(path,BODY+b'1 1 1 rg BT 20 21.5 Td (Artist credit) Tj ET\nq 1 0 0 1 5 5 cm /I1 Do Q')
        before=path.read_bytes();self.assertFalse(clean_pdf(path)['changed']);self.assertEqual(before,path.read_bytes())
    def test_known_badge_and_its_footer_removed(self):
        path=self.root/'badge.pdf';pdf(path,BODY+b'0 g 18.43 42.52 m 40 42.52 l 40 10 l h f\n1 1 1 rg BT 20 21.5 Td (Buyer footer) Tj ET')
        result=clean_pdf(path);self.assertTrue(result['changed']);self.assertNotIn('Buyer footer',PdfReader(path).pages[0].extract_text())
    def test_repack_cleans_staged_pdf_and_verifies_changed_size(self):
        path=self.root/'guide.pdf';pdf(path,BODY+STAMP);original=path.read_bytes()
        source=self.root/'source.zip'
        with zipfile.ZipFile(source,'w') as archive:archive.writestr('Instructions/guide.pdf',original)
        original_archive=source.read_bytes();work=self.root/'work';outputs=repack(source,work,'pdf-test')
        extracted=self.root/'inspect';extracted.mkdir()
        command(['x','-y','-o'+str(extracted),'--',str(outputs[0])],self.root,lambda:False)
        self.assertNotIn('Buyer order',PdfReader(extracted/'Instructions/guide.pdf').pages[0].extract_text())
        self.assertEqual(source.read_bytes(),original_archive);self.assertEqual(path.read_bytes(),original)
        record=json.loads((work/'repack.json').read_text())['pdfs'][0]
        self.assertTrue(record['changed']);self.assertNotEqual(record['before']['sha256'],record['after']['sha256'])
    def test_corrupt_pdf_never_replaces_input(self):
        path=self.root/'bad.pdf';path.write_bytes(b'bad pdf');original=path.read_bytes()
        with self.assertRaises(Exception):clean_pdf(path)
        self.assertEqual(path.read_bytes(),original)
