import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from app.stl_cleaner import clean_stl,clean_cotton,validate_stl,STLCleaningError,TRI
from app.mmf_repack import repack
from app.release_images import command


def mesh():
    vertices=[(0,0,0),(10,0,0),(0,10,0),(0,0,10)]
    faces=[(0,2,1),(0,1,3),(0,3,2),(1,2,3)]
    return b'solid binary header'.ljust(80,b'\0')+struct.pack('<I',4)+b''.join(TRI.pack(0,0,1,*(c for i in f for c in vertices[i]),17) for f in faces)

class CleanerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.path=self.root/'sample.stl';self.path.write_bytes(mesh())
    def test_configuration_defaults_validation_and_persistence(self):
        from app.subscription_store import SubscriptionStore
        store=SubscriptionStore(self.root)
        self.assertEqual(store.config()['release_stl_cleaner'],'off')
        for value in ('unknown',True,None):
            with self.assertRaises(ValueError):store.save_config({'revision':0,'download_directory':str(self.root),'release_stl_cleaner':value})
        with self.assertRaisesRegex(ValueError,'not installed'):
            store.save_config({'revision':0,'download_directory':str(self.root),'release_stl_cleaner':'cotton','release_cotton_directory':str(self.root/'missing')})
        result=store.save_config({'revision':0,'download_directory':str(self.root),'release_stl_cleaner':'delicates'})
        self.assertEqual(result['release_stl_cleaner'],'delicates')
        self.assertEqual(SubscriptionStore(self.root).config()['release_stl_cleaner'],'delicates')

    def test_valid_binary_solid_header_and_shared_vertices(self):
        updates=[];result=clean_stl(self.path,updates.append)
        data=self.path.read_bytes();self.assertEqual(data[:80],bytes(80));self.assertEqual(validate_stl(self.path),4)
        points=set()
        for tri in TRI.iter_unpack(data[84:]):
            self.assertEqual(tri[:3],(0,0,0));self.assertEqual(tri[-1],0)
            for i in (3,6,9):points.add(tri[i:i+3])
        self.assertEqual(len(points),4)
        for point in points:
            reconstructed=tuple(point[a]+result['translation'][a] for a in range(3))
            self.assertTrue(any(all(abs(reconstructed[a]-old[a])<.00051 for a in range(3)) for old in [(0,0,0),(10,0,0),(0,10,0),(0,0,10)]))
        self.assertEqual(updates[0],0);self.assertEqual(updates[-1],100)
        self.assertEqual(updates,sorted(updates))
    def test_ascii_conversion(self):
        self.path.write_text('solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid x\n')
        clean_stl(self.path);self.assertEqual(validate_stl(self.path),1)
    def test_invalid_and_cancelled_files_are_unchanged(self):
        for value in (b'bad',bytes(84),mesh()[:-1],mesh()[:84]+TRI.pack(0,0,0,*([math.nan]*9),0)+mesh()[134:]):
            self.path.write_bytes(value)
            with self.assertRaises((ValueError,UnicodeError)):clean_stl(self.path)
            self.assertEqual(self.path.read_bytes(),value)
        self.path.write_bytes(mesh());cancel=[False]
        def progress(value):
            if value>=25:cancel[0]=True
        with self.assertRaises(STLCleaningError):clean_stl(self.path,progress,lambda:cancel[0])
        self.assertEqual(self.path.read_bytes(),mesh())
    def test_packaging_default_off_then_enabled_invalidates_cache(self):
        source=self.root/'source.zip'
        with zipfile.ZipFile(source,'w') as z:z.writestr('model.STL',mesh())
        original=source.read_bytes();work=self.root/'packed'
        output=repack(source,work,'identity',compression_level=0)[0]
        command(['x','-y','-o'+str(self.root/'off'),'--',str(output)],self.root,lambda:False)
        self.assertEqual((self.root/'off/model.STL').read_bytes(),mesh())
        events=[]
        output=repack(source,work,'identity',compression_level=0,stl_cleaner='delicates',cleaner_progress=lambda *args:events.append(args))[0]
        command(['x','-y','-o'+str(self.root/'on'),'--',str(output)],self.root,lambda:False)
        self.assertNotEqual((self.root/'on/model.STL').read_bytes(),mesh());self.assertEqual(validate_stl(self.root/'on/model.STL'),4)
        self.assertEqual(source.read_bytes(),original);self.assertTrue(events);self.assertEqual(events[-1][-1],100)
        receipt=json.loads((work/'repack.json').read_text());self.assertEqual(len(receipt['cleaned_stls']),1)
        with patch('app.mmf_repack.command',side_effect=AssertionError('Must reuse verified archive')):
            repack(source,work,'identity',compression_level=0,stl_cleaner='delicates')
    def test_nested_archives_fail_without_silent_skipping(self):
        source=self.root/'outer.zip'
        with zipfile.ZipFile(source,'w') as z:z.writestr('nested.zip',b'nested archive')
        original=source.read_bytes()
        with self.assertRaisesRegex(RuntimeError,'nested archives'):
            repack(source,self.root/'nested','nested',stl_cleaner='delicates')
        self.assertEqual(source.read_bytes(),original)

    def test_cotton_empty_output_rejected_and_stop_retains_source(self):
        binary=self.root/'fake-cotton';binary.write_text('#!/usr/bin/env python3\nimport sys\nopen(sys.argv[1],"wb").write(bytes(84))\n');binary.chmod(0o700)
        with self.assertRaisesRegex(STLCleaningError,'empty'):clean_cotton(self.path,binary)
        self.assertEqual(self.path.read_bytes(),mesh())
        with self.assertRaisesRegex(STLCleaningError,'stopped'):clean_cotton(self.path,binary,stopped=lambda:True)
        self.assertEqual(self.path.read_bytes(),mesh())

if __name__=='__main__':unittest.main()
