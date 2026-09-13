import io
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import struct

from release_images import extract_images, ExtractionError, MissingVolumeError, archive_error, complete_split_7z, listing, volume_key, flat_image_name
from file_delivery import deliver, DeliveryError


def damaged_image_zip(path):
    """One stale central-directory entry and one genuinely corrupt payload."""
    with zipfile.ZipFile(path,'w') as archive:
        for name,data in [('good.jpg',b'good image'),('reindexed.jpg',b'recovered image'),('broken.jpg',b'broken image')]:
            archive.writestr(name,data)
    with zipfile.ZipFile(path) as archive:
        broken=archive.getinfo('broken.jpg');central=archive.start_dir
    raw=bytearray(path.read_bytes());raw[broken.header_offset+30+len(broken.filename)]^=1
    while raw[central:central+4]==b'PK\x01\x02':
        nlen,xlen,clen=struct.unpack_from('<3H',raw,central+28)
        name=raw[central+46:central+46+nlen]
        if name==b'reindexed.jpg':
            offset=struct.unpack_from('<I',raw,central+42)[0]
            struct.pack_into('<I',raw,central+42,offset+1)
            struct.pack_into('<I',raw,central+24,len(b'recovered image')+3)
        central+=46+nlen+xlen+clen
    path.write_bytes(raw)


class ExtractionTests(unittest.TestCase):
    def test_recovery_rebuilds_zip_index_skips_bad_crc_and_continues_nested_images(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);nested=root/'damaged.zip';damaged_image_zip(nested)
            archive=root/'release.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.write(nested,'damaged.zip');z.writestr('last.jpg',b'last image');z.writestr('model.stl',b'model')
            original=archive.read_bytes();warnings=[]
            with self.assertRaises(ExtractionError):extract_images(archive,root/'strict')
            images=extract_images(archive,root/'recover',warnings=warnings)
            self.assertEqual({p.read_bytes() for p in images},{b'good image',b'recovered image',b'last image'})
            self.assertTrue(any('recovered the damaged ZIP index' in w for w in warnings))
            self.assertTrue(any('broken.jpg' in w and 'skipped' in w for w in warnings))
            self.assertEqual(archive.read_bytes(),original)
            self.assertFalse(list((root/'recover').rglob('model.stl')))
            self.assertFalse(list((root/'recover/output').rglob('broken.jpg')))

    def test_corrupt_7z_member_does_not_discard_other_verified_images(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);good=root/'good.jpg';good.write_bytes(b'good image payload')
            bad=root/'bad.jpg';bad.write_bytes(b'unique bad image payload')
            archive=root/'release.7z'
            subprocess.run(['7z','a','-mx0',str(archive),str(good),str(bad)],stdout=subprocess.DEVNULL,check=True)
            raw=bytearray(archive.read_bytes());position=raw.index(b'unique bad image payload');raw[position]^=1;archive.write_bytes(raw)
            warnings=[];images=extract_images(archive,root/'recover',warnings=warnings)
            self.assertEqual([p.read_bytes() for p in images],[good.read_bytes()])
            self.assertTrue(any('bad.jpg' in w for w in warnings))

    def test_unreadable_nested_image_archive_warns_but_cancellation_and_paths_still_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=root/'release.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('bad.zip',b'not an archive');z.writestr('good.jpg',b'good image')
            warnings=[];images=extract_images(archive,root/'recover',warnings=warnings)
            self.assertEqual([p.read_bytes() for p in images],[b'good image']);self.assertTrue(any('bad.zip' in w for w in warnings))
            unsafe=root/'unsafe.zip'
            with zipfile.ZipFile(unsafe,'w') as z:z.writestr('../outside.jpg',b'bad')
            with self.assertRaises(ExtractionError):extract_images(unsafe,root/'unsafe-work',warnings=[])
            damaged=root/'damaged.zip';damaged_image_zip(damaged);cancelled=False
            def progress(stage,*args):
                nonlocal cancelled
                if stage=='recovering':cancelled=True
            with self.assertRaisesRegex(ExtractionError,'Stopped'):
                extract_images(damaged,root/'cancel',progress,lambda:cancelled,warnings=[])

    def test_nested_7z_with_zip_extension_extracts_despite_format_warning(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);image=root/'preview.jpg';image.write_bytes(b'original preview')
            subprocess.run(['7z','a',str(root/'nested.7z'),str(image)],stdout=subprocess.DEVNULL,check=True)
            with zipfile.ZipFile(root/'release.zip','w') as archive:
                archive.write(root/'nested.7z','nested.zip')
            images=extract_images(root/'release.zip',root/'work')
            self.assertEqual([p.read_bytes() for p in images],[image.read_bytes()])

    def test_format_warning_does_not_hide_invalid_paths_or_member_metadata(self):
        text=('Open WARNING: Cannot open the file as [zip] archive\nType = 7z\n----------\n'
              'Path = ../preview.jpg\nSize = 12\n\n\nWarnings: 1\n')
        for malformed in (text,text.replace('../preview.jpg','preview\ninjected.jpg'),
                          text.replace('../preview.jpg','preview.jpg').replace('Size = 12','Size = 12\nUnexpected content')):
            with patch('release_images.command',return_value=malformed):
                with self.assertRaises(ExtractionError):listing(Path('release.zip'),Path('.'),lambda:False)

    def test_missing_volume_is_reported_only_from_archive_header(self):
        source=Path('inputs/Example 2026-09 1.rar')
        text='Type = Rar5\nERROR = Missing volume : Example 2026-09 2.rar\n----------\nPath = models.zip\nSize = 12\n'
        error=archive_error(text,source)
        self.assertIsInstance(error,MissingVolumeError)
        self.assertEqual(error.archive,source)
        self.assertEqual(error.missing,'Example 2026-09 2.rar')
        for invalid in (text.replace('Example 2026-09 2.rar','../elsewhere.rar'),
                        'Type = Rar5\n----------\n'+text):
            self.assertNotIsInstance(archive_error(invalid,source),MissingVolumeError)

    def test_rar_part_separators_and_numbers_group_without_changing_release_name(self):
        for separator in ('.','_','-',' '):
            with self.subTest(separator=separator):
                self.assertEqual(volume_key('Example 2026-01'+separator+'part01.rar'),('example 2026-01.rar',1))
                self.assertEqual(volume_key('Example 2026-01'+separator+'PART2.RAR'),('example 2026-01.rar',2))
        self.assertEqual(volume_key('Example 2026-01.rar'),('example 2026-01.rar',0))
        self.assertEqual(volume_key('Example 2026-01.7z.002'),('example 2026-01.7z',2))
        self.assertEqual(volume_key('Example 2026-01.r00'),('example 2026-01.rar',1))
        self.assertEqual(volume_key('Example partisans 2026-01.rar'),('example partisans 2026-01.rar',0))

    def test_existing_flat_image_names_survive_new_rar_grouping(self):
        for separator in ('.','_','-',' '):
            archive='Example 2026-01'+separator+'part01.rar'
            legacy_key='example 2026-01.rar' if separator=='.' else archive.casefold()
            suffix=hashlib.sha256((legacy_key+'/gallery/preview.jpg').encode()).hexdigest()[:12]
            self.assertEqual(flat_image_name(archive,'gallery/preview.jpg'),'preview__'+suffix+'.jpg')

    def test_rar_listing_with_empty_final_metadata_is_valid(self):
        text=('Type = Rar5\n----------\n'
              'Path = images/preview.jpg\nFolder = -\nSize = 12\nNT Security = \n\n'
              'Path = Models\nFolder = +\nSize = 0\nNT Security = \n\n')
        with patch('release_images.command',return_value=text):
            header,entries=listing(Path('release.rar'),Path('.'),lambda:False)
        self.assertEqual(entries,[{'name':'images/preview.jpg','size':12}])
        # A newline in a member name still fails instead of being swallowed.
        malformed=text.replace('images/preview.jpg','images/preview\ninjected.jpg')
        with patch('release_images.command',return_value=malformed):
            with self.assertRaises(ExtractionError):listing(Path('release.rar'),Path('.'),lambda:False)

    def test_nested_archives_and_same_image_names_preserve_original_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=root/'release.zip';nested=io.BytesIO()
            with zipfile.ZipFile(nested,'w') as z:
                z.writestr('preview.jpg',b'nested image')
                z.writestr('model.stl',b'nested model')
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('one/preview.jpg',b'first image')
                z.writestr('two/preview.jpg',b'second image')
                z.writestr('model.stl',b'model')
                z.writestr('more.zip',nested.getvalue())
            original=archive.read_bytes()
            images=extract_images(archive,root/'work')
            self.assertEqual({p.read_bytes() for p in images},{b'first image',b'second image',b'nested image'})
            self.assertEqual(len(images),3)
            self.assertFalse(list((root/'work').rglob('*.stl')))
            self.assertEqual(archive.read_bytes(),original)

    def test_traversal_links_and_duplicate_paths_are_rejected(self):
        for kind in ['traversal','link','duplicate']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);archive=root/'unsafe.zip'
                with zipfile.ZipFile(archive,'w') as z:
                    if kind=='traversal':z.writestr('../outside.jpg',b'bad')
                    elif kind=='link':
                        info=zipfile.ZipInfo('link.jpg');info.create_system=3;info.external_attr=0o120777<<16
                        z.writestr(info,'/etc/passwd')
                    else:
                        z.writestr('image.jpg',b'a');z.writestr('IMAGE.jpg',b'b')
                with self.assertRaises(ExtractionError):extract_images(archive,root/'work')
                self.assertFalse((root/'outside.jpg').exists())

    def test_no_images_and_cancel(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=root/'release.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('model.stl',b'model')
            self.assertEqual(extract_images(archive,root/'work'),[])
            with self.assertRaises(ExtractionError):extract_images(archive,root/'cancel',stopped=lambda:True)
            self.assertTrue(archive.exists())

    def test_split_archive_requires_all_parts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'image.png';source.write_bytes(os.urandom(14000))
            subprocess.run(['7z','a','-v4k','-mx0',str(root/'release.7z'),str(source)],stdout=subprocess.DEVNULL,check=True)
            parts=sorted(root.glob('release.7z.*'))
            self.assertGreater(len(parts),1)
            images=extract_images(parts[0],root/'work')
            self.assertEqual(images[0].read_bytes(),source.read_bytes())
            parts[-1].rename(root/'missing-part')
            with self.assertRaises(ExtractionError):extract_images(parts[0],root/'incomplete')

    def test_delivery_rejects_image_directory_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'image.jpg';source.write_bytes(b'image')
            month=root/'Artist/2026-09';month.mkdir(parents=True)
            outside=root/'outside';outside.mkdir();(month/'release_images').symlink_to(outside,target_is_directory=True)
            with self.assertRaises(DeliveryError):deliver(source,root,'Artist','2026-09','image.jpg',subdirectories=('release_images',))
            self.assertEqual(list(outside.iterdir()),[])

    def test_split_lengths_must_match_inner_header_and_all_numbered_parts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);first=root/'release.7z.001';first.write_bytes(b'1234')
            second=root/'release.7z.002';second.write_bytes(b'56')
            header='\n--\nType = Split\nVolumes = 2\nTotal Physical Size = 6\n--\nType = 7z\nPhysical Size = 6\n'
            self.assertTrue(complete_split_7z(first,header))
            second.write_bytes(b'5')
            with self.assertRaises(ExtractionError):complete_split_7z(first,header)
            second.write_bytes(b'56');second.rename(root/'release.7z.003')
            with self.assertRaises(ExtractionError):complete_split_7z(first,header)

    def test_corrupt_selected_image_is_rejected_without_testing_model_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'preview.jpg';source.write_bytes(os.urandom(6000))
            subprocess.run(['7z','a','-mx0','-v4k',str(root/'release.7z'),str(source)],stdout=subprocess.DEVNULL,check=True)
            first=root/'release.7z.001'
            with first.open('r+b') as out:
                out.seek(100);byte=out.read(1);out.seek(100);out.write(bytes([byte[0]^255]))
            with self.assertRaises(ExtractionError):extract_images(first,root/'work')
            self.assertTrue(first.exists())


if __name__=='__main__':unittest.main()
