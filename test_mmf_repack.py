import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from mmf_repack import repack, archive_name, VOLUME_BYTES
from release_images import listing, command, ExtractionError

class RepackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def source(self):
        source=self.root/'Artist Original Release.zip'
        with zipfile.ZipFile(source,'w') as archive:
            archive.writestr('Supported/Original Model.stl',os.urandom(12000))
            archive.writestr('Unsupported/Original Model.stl',os.urandom(9000))
        return source
    def test_names_small_archive_and_receipt(self):
        source=self.source();outputs=repack(source,self.root/'work','version')
        self.assertEqual([p.name for p in outputs],['Artist Original Release.7z'])
        _,entries=listing(outputs[0],self.root,lambda:False)
        self.assertEqual({e['name'] for e in entries},{'Supported/Original Model.stl','Unsupported/Original Model.stl'})
        with patch('mmf_repack.command',side_effect=AssertionError('should reuse verified outputs')):
            self.assertEqual(repack(source,self.root/'work','version'),outputs)
        self.assertTrue(source.exists());self.assertEqual(VOLUME_BYTES,4000*1024*1024)
    def test_split_volumes_and_integrity(self):
        outputs=repack(self.source(),self.root/'work','version',volume_bytes=4096)
        self.assertGreater(len(outputs),1)
        self.assertTrue(all(p.stat().st_size<=4096 for p in outputs))
        self.assertEqual(outputs[0].name,'Artist Original Release.7z.001')
        command(['t','-p-','--',str(outputs[0])],self.root,lambda:False)
    def test_split_source_is_repacked_as_one_archive(self):
        source=self.source();split=repack(source,self.root/'split','v',volume_bytes=4096)
        outputs=repack(split[0],self.root/'merged','v2')
        self.assertEqual(outputs[0].name,'Artist Original Release.7z')
        _,entries=listing(outputs[0],self.root,lambda:False);self.assertEqual(len(entries),2)
    def test_corrupt_source_and_stop_keep_original(self):
        source=self.root/'bad.zip';source.write_bytes(b'not an archive')
        with self.assertRaises(ExtractionError):repack(source,self.root/'bad-work','v')
        self.assertTrue(source.exists())
        source=self.source()
        with self.assertRaises(ExtractionError):repack(source,self.root/'stop-work','v',stopped=lambda:True)
        self.assertTrue(source.exists())
    def test_unsafe_member_is_confined(self):
        source=self.root/'unsafe.zip'
        with zipfile.ZipFile(source,'w') as z:z.writestr('../../escape.stl',b'solid')
        outputs=repack(source,self.root/'safe','v')
        _,entries=listing(outputs[0],self.root,lambda:False)
        self.assertTrue(entries[0]['name'].startswith('__renamed_'))
        self.assertFalse((self.root/'escape.stl').exists())
    def test_archive_names(self):
        self.assertEqual(archive_name('Original.part01.rar'),'Original.7z')
        self.assertEqual(archive_name('Original.7z.001'),'Original.7z')
        self.assertEqual(archive_name('Original.tar.gz'),'Original.7z')

if __name__=='__main__':unittest.main()
