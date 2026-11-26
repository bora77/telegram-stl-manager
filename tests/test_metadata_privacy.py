import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
import zlib
from PIL import Image, PngImagePlugin
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, TextStringObject
from app.metadata_privacy import clean_timestamps, jpeg_blocks, XMP, EXTENDED
from app.mmf_repack import repack
from app.release_images import command
from tests.test_mmf_pdf import pdf, BODY

PACKET=b'<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:d="http://ns.adobe.com/xap/1.0/"><d:Description d:CreateDate="2025-01-02T03:04:05" d:CreatorTool="Artist tool"><d:ModifyDate>2025-02-03</d:ModifyDate><d:rights>Artist copyright</d:rights></d:Description></x:xmpmeta>'

def exif():
    value=Image.Exif();value[306]='2025:01:02 03:04:05';value[315]='Artist';value[274]=6
    value[34665]={36867:'2025:01:02 03:04:05',37500:b'opaque camera dates',40962:16}
    value[34853]={29:'2025:01:02',1:'N'}
    return value

def pixels(path):
    with Image.open(path) as image:return image.convert('RGB').tobytes()

def segment(marker,body):return bytes((255,marker))+(len(body)+2).to_bytes(2,'big')+body

class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def test_jpeg_lossless_exif_xmp_iptc_and_attribution(self):
        for progressive in (False,True):
            path=self.root/'photo.jpg';Image.new('RGB',(16,12),'orange').save(path,exif=exif(),progressive=progressive,icc_profile=b'colour profile')
            iptc=b'\x1c\x02\x37\x00\x08'+b'20250102'+b'\x1c\x02\x74\x00\x06Artist'
            resource=b'Photoshop 3.0\0'+b'8BIM\x04\x04\0\0'+len(iptc).to_bytes(4,'big')+iptc+(b'\0' if len(iptc)%2 else b'')
            original=path.read_bytes();path.write_bytes(original[:2]+segment(0xe1,XMP+PACKET)+segment(0xed,resource)+original[2:]);before=pixels(path)
            scans=[raw for marker,body,raw in jpeg_blocks(path.read_bytes()) if marker is None]
            result=clean_timestamps(path);self.assertTrue(result['changed']);self.assertEqual(before,pixels(path))
            self.assertEqual(scans,[raw for marker,body,raw in jpeg_blocks(path.read_bytes()) if marker is None])
            with Image.open(path) as image:
                self.assertEqual(image.getexif()[315],'Artist');self.assertEqual(image.getexif()[274],6)
                self.assertEqual(image.info['icc_profile'],b'colour profile');self.assertNotIn(306,image.getexif())
                self.assertNotIn(36867,image.getexif().get_ifd(34665));self.assertNotIn(29,image.getexif().get_ifd(34853))
            self.assertIn(b'Artist copyright',path.read_bytes());self.assertNotIn(b'2025',path.read_bytes())
            self.assertFalse(clean_timestamps(path)['changed']);self.assertEqual(path.stat().st_mtime,0)
    def test_extended_xmp(self):
        import hashlib
        path=self.root/'extended.jpg';Image.new('RGB',(8,8)).save(path);original=path.read_bytes()
        guid=hashlib.md5(PACKET).hexdigest().upper().encode()
        main=b'<root xmlns:n="http://ns.adobe.com/xmp/note/" n:HasExtendedXMP="'+guid+b'"/>'
        extended=EXTENDED+guid+struct.pack('>II',len(PACKET),0)+PACKET
        path.write_bytes(original[:2]+segment(0xe1,XMP+main)+segment(0xe1,extended)+original[2:])
        before=pixels(path);self.assertTrue(clean_timestamps(path)['changed']);self.assertEqual(before,pixels(path));self.assertNotIn(guid,path.read_bytes());self.assertFalse(clean_timestamps(path)['changed'])
    def test_png_and_webp_lossless(self):
        for suffix in ('png','webp'):
            path=self.root/('photo.'+suffix);kwargs={'exif':exif()}
            if suffix=='png':
                metadata=PngImagePlugin.PngInfo();metadata.add_text('Creation Time','2025-01-02');metadata.add_itxt('XML:com.adobe.xmp',PACKET.decode(),zip=True);metadata.add_text('Copyright','Artist');metadata.add(b'tIME',struct.pack('>HBBBBB',2025,1,2,3,4,5));kwargs['pnginfo']=metadata
            else:kwargs.update(xmp=PACKET,lossless=True)
            Image.new('RGB',(16,12),'blue').save(path,**kwargs);before=pixels(path)
            self.assertTrue(clean_timestamps(path)['changed']);self.assertEqual(before,pixels(path));self.assertNotIn(b'2025',path.read_bytes())
            with Image.open(path) as image:
                self.assertEqual(image.getexif()[315],'Artist');self.assertEqual(image.getexif()[274],6)
                if suffix=='png':self.assertEqual(image.info['Copyright'],'Artist')
            self.assertFalse(clean_timestamps(path)['changed'])
    def test_no_metadata_bytes_unchanged_and_stop_safe(self):
        for suffix in ('jpg','png','webp'):
            path=self.root/('plain.'+suffix);Image.new('RGB',(8,8)).save(path);original=path.read_bytes()
            self.assertFalse(clean_timestamps(path)['changed']);self.assertEqual(original,path.read_bytes())
            with self.assertRaisesRegex(ValueError,'stopped'):clean_timestamps(path,lambda:True)
            self.assertEqual(original,path.read_bytes())
    def test_pdf_dates_attribution_content_and_xmp_without_type(self):
        path=self.root/'guide.pdf';pdf(path,BODY);writer=PdfWriter(clone_from=path)
        writer.add_metadata({'/CreationDate':'D:20250102030405','/ModDate':'D:20250203040506'})
        stream=DecodedStreamObject();stream.set_data(PACKET);writer._root_object[NameObject('/Metadata')]=writer._add_object(stream)
        writer.pages[0][NameObject('/PieceInfo')]=DictionaryObject({NameObject('/M'):TextStringObject('D:20250102030405')})
        writer.write(path);before=PdfReader(path).pages[0].get_contents().get_data()
        self.assertTrue(clean_timestamps(path)['changed']);reader=PdfReader(path)
        self.assertEqual(reader.pages[0].get_contents().get_data(),before);self.assertEqual(reader.metadata.author,'Original artist');self.assertIn('Assembly instructions',reader.pages[0].extract_text())
        self.assertNotIn('/CreationDate',reader.metadata);self.assertNotIn(b'2025',path.read_bytes());self.assertIn(b'Artist copyright',reader.trailer['/Root']['/Metadata'].get_data())
        self.assertFalse(clean_timestamps(path)['changed'])
    def test_signed_encrypted_and_malformed_inputs_retained(self):
        path=self.root/'signed.pdf';pdf(path,BODY);writer=PdfWriter(clone_from=path);writer.pages[0][NameObject('/Test')]=DictionaryObject({NameObject('/FT'):NameObject('/Sig')});writer.write(path);original=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'Signed'):clean_timestamps(path)
        self.assertEqual(path.read_bytes(),original)
        writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.encrypt('password');writer.write(path);original=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'Encrypted'):clean_timestamps(path)
        self.assertEqual(path.read_bytes(),original)
        path=self.root/'broken.png';path.write_bytes(b'bad data')
        with self.assertRaises(ValueError):clean_timestamps(path)
        self.assertEqual(path.read_bytes(),b'bad data');self.assertFalse(list(self.root.glob('.metadata-*')))
    def test_pack_original_unchanged_and_policy_switch_invalidates_cache(self):
        image=self.root/'photo.jpg';Image.new('RGB',(16,16)).save(image,exif=exif());original_image=image.read_bytes()
        source=self.root/'source.zip'
        with zipfile.ZipFile(source,'w') as archive:archive.writestr('photo.jpg',original_image)
        original=source.read_bytes();work=self.root/'work'
        for enabled in (False,True):
            stages=[];outputs=repack(source,work,'privacy-test',metadata_privacy=enabled,progress=lambda stage,pct:stages.append((stage,pct)))
            output=self.root/str(enabled);output.mkdir();command(['x','-y','-o'+str(output),'--',str(outputs[0])],self.root,lambda:False)
            with Image.open(output/'photo.jpg') as result:self.assertEqual(306 in result.getexif(),not enabled)
            receipt=json.loads((work/'repack.json').read_text());self.assertEqual(receipt['metadata_privacy'],enabled)
            if enabled:self.assertIn(('processing_metadata',100),stages)
        self.assertEqual(source.read_bytes(),original);self.assertEqual(image.read_bytes(),original_image)
