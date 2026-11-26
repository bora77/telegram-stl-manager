import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from app.collage_auto import choose,features,grouping,start,execute,model_name
from app.collages import CollageStore
from tests.test_collages import fixture


def candidate(i,group,confidence=3,name=None):
    # Distinct pixel signatures prevent the synthetic quota test becoming a
    # duplicate-removal test by accident.
    image=Image.new('RGB',(400,500),(i*41%256,i*97%256,i*137%256));stream=io.BytesIO();image.save(stream,format='PNG')
    feature=features(stream.getvalue());feature['dhash']=(1<<(i%30+1))-1
    return {'id':str(i),'name':name or f'model-{i}.png','group':group,'confidence':confidence,'reason':'fixture','uncertain':confidence<2,'sha256':str(i),**feature}


class SelectionTests(unittest.TestCase):
    def test_quota_coverage_and_nine_image_limit(self):
        inputs=[candidate(i,'model-'+str(i//3)) for i in range(18)]
        result=choose(inputs)
        from collections import Counter
        counts=Counter(i['group'] for i in result['selected'])
        self.assertLessEqual(len(result['images']),9);self.assertEqual(len(counts),6)
        self.assertTrue(all(n<=2 for n in counts.values()))

    def test_exact_and_near_duplicates_are_removed(self):
        a=candidate(2,'same');b={**a,'id':'duplicate'};c={**a,'id':'near','sha256':'different-encoding'}
        result=choose([a,b,c]);self.assertEqual(result['duplicates_removed'],2);self.assertEqual(len(result['images']),1)

    def test_similar_background_does_not_hide_small_model_difference(self):
        from PIL import ImageDraw
        inputs=[]
        for index in range(2):
            image=Image.new('RGB',(400,400),'#121212');draw=ImageDraw.Draw(image)
            draw.rectangle((100,100,300,300),fill='#555555')
            if index:draw.rectangle((190,190,220,220),fill='white')
            stream=io.BytesIO();image.save(stream,format='PNG')
            inputs.append({**candidate(index,'tile-'+str(index)),**features(stream.getvalue())})
        self.assertEqual(choose(inputs)['duplicates_removed'],0)

    def test_color_group_shot_preferred_to_scale_diagram(self):
        a=candidate(1,'model',name='scale.png');b=candidate(2,'model',name='combo.png');b['color']=1
        result=choose([a,b]);self.assertEqual(result['images'][0],b['id'])
        self.assertTrue(result['selected'][0]['quality'].startswith('Group-shot'))

    def test_repeatable_random_fallback_and_shuffle(self):
        inputs=[candidate(i,'unknown:'+str(i),0) for i in range(20)]
        first=choose(inputs,0)['images'];self.assertEqual(first,choose(inputs,0)['images'])
        self.assertNotEqual(first,choose(inputs,123)['images'])
        self.assertEqual(choose(inputs,0)['uncertain'],9)

    def test_model_archive_and_collection_folder_evidence(self):
        image={'id':'image','name':'Untitled_001.jpg'}
        source=[{'model':'teknical','archive':'Teknical-Renders.zip','broad':False,'member':''}]
        self.assertEqual(grouping(image,source,set())[:2],('teknical',3))
        source=[{'model':'worldship','archive':'Render_Worldship.zip','broad':True,'member':'Ground_Tiles/1.jpg'}]
        self.assertEqual(grouping(image,source,set())[:2],('worldship / Ground_Tiles',2))
        self.assertEqual(model_name('Norse_Medic_Unsupported.zip'),model_name('Norse_Medic_supported.zip'))

    def test_filename_matches_models_inside_shared_image_archive(self):
        image={'id':'image','name':'hash-1--WizardV2.jpg'}
        result=grouping(image,[],{'wizard','castle prefab v 2'})
        self.assertEqual(result[:2],('wizard',2))


class AutoJobTests(unittest.TestCase):
    def test_job_does_not_modify_draft_or_export_collage_and_reuses_features(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);store,images=fixture(root);collages=CollageStore(store);opened=collages.index('Example','2026-08')
            before=opened['selection'];payload={'release_id':opened['release']['id'],'image_ids':[i['id'] for i in opened['images'] if not i['reason']],'seed':0}
            with patch('app.collage_auto.subprocess.Popen') as launch:job=start(collages,payload)
            launch.assert_called_once();execute(collages,job)
            self.assertEqual(job['state'],'complete',job.get('message'));self.assertGreater(len(job['result']['images']),0)
            self.assertEqual(collages.index('Example','2026-08')['selection'],before)
            self.assertFalse((images.parent/'Example-2026-08.jpg').exists())
            with patch('app.collage_auto.features',side_effect=AssertionError('Features should be cached')):execute(collages,job)
            self.assertEqual(job['state'],'complete')

    def test_unknown_or_duplicate_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            store,_=fixture(Path(d));collages=CollageStore(store);opened=collages.index('Example','2026-08');image=opened['images'][0]['id']
            for ids in ([],[image,image],['0'*64]):
                with self.assertRaises(ValueError):start(collages,{'release_id':opened['release']['id'],'image_ids':ids})

    def test_source_change_is_not_hidden_by_cached_analysis(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);store,images=fixture(root);collages=CollageStore(store);opened=collages.index('Example','2026-08');record=opened['images'][0]
            with patch('app.collage_auto.subprocess.Popen'):job=start(collages,{'release_id':opened['release']['id'],'image_ids':[record['id']]})
            execute(collages,job);self.assertEqual(job['state'],'complete')
            (images/record['relative']).write_bytes(b'changed')
            execute(collages,job);self.assertEqual(job['state'],'failed')
