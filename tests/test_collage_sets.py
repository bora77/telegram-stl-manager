import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.collages import CollageStore
from app.collage_sets import finalize,paths,MANIFEST
from app.telegram_cli import require_release_collage,require_release_collages
from tests.test_collages import fixture


class CollageSetTests(unittest.TestCase):
    def test_named_collages_save_and_manifest_excludes_unselected_images(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);store,image_dir=fixture(root);collages=CollageStore(store);opened=collages.index('Example','2026-08')
            ids=[i['id'] for i in opened['images'] if not i['reason']];exports=[]
            for index,name in enumerate(('Model Set','Release Overview')):
                payload={**opened['selection'],'release_id':opened['release']['id'],'images':ids[index:index+2],
                         'card_name':name,'title':'Example · 2026-08 · '+name,'labels':{ids[index]:'Model name'}}
                preview=collages.preview(payload)
                with patch('app.collages.subprocess.Popen'):job=collages.start_export({'preview_id':preview['id']})
                collages.run_export(job['id']);self.assertEqual(collages.export_status(job['id'])['state'],'completed');exports.append(job['id'])
            result=finalize(collages,{'release_id':opened['release']['id'],'export_ids':exports})
            self.assertEqual(len(result['files']),2)
            directory=image_dir.parent
            self.assertTrue((directory/'Example-2026-08-Model Set.jpg').exists())
            (directory/'unrelated.jpg').write_bytes(b'not part of the set')
            self.assertEqual(len(require_release_collages(directory/'release.7z')),2)
            self.assertEqual(require_release_collage(directory/'release.7z').name,result['files'][0]['filename'])
            previous=(directory/MANIFEST).read_bytes()
            with self.assertRaises(ValueError):finalize(collages,{'release_id':opened['release']['id'],'export_ids':[exports[0],exports[0]]})
            self.assertEqual((directory/MANIFEST).read_bytes(),previous)
            (directory/result['files'][1]['filename']).write_bytes(b'changed')
            with self.assertRaises(ValueError):paths(directory)

    def test_cards_and_labels_persist_without_export(self):
        with tempfile.TemporaryDirectory() as d:
            store,images=fixture(Path(d));collages=CollageStore(store);opened=collages.index('Example','2026-08');image=opened['images'][0]['id']
            draft={**opened['selection'],'images':[image],'labels':{image:'A model'}}
            cards=[{'id':'a'*32,'name':'One model','enabled':True,'draft':draft},{'id':'b'*32,'name':'Another','enabled':False,'draft':draft}]
            collages.save_selection({**draft,'release_id':opened['release']['id'],'revision':0,'cards':cards,'active_card':'a'*32})
            loaded=collages.index('Example','2026-08')['selection']
            self.assertEqual(len(loaded['cards']),2);self.assertEqual(loaded['cards'][0]['draft']['labels'][image],'A model')
            self.assertFalse(loaded['cards'][1]['enabled']);self.assertFalse((images.parent/MANIFEST).exists())
