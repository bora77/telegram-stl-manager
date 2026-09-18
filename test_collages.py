"""Collage fixtures use temporary local folders; no Telegram or NAS operations."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image, ImageDraw

from collage_layout import FORMATS, LAYOUTS, geometry, render
from collages import CollageError, CollageStore, collage_filename
from subscription_store import SubscriptionStore
from test_support import configure_source


def fixture(root):
    store=SubscriptionStore(configure_source(root))
    base=root/'nas';images=base/'Example'/'2026-08'/'release_images';images.mkdir(parents=True)
    store.atomic_write(store.config_path,{'revision':0,'download_directory':str(base)})
    store.atomic_write(root/'data/creators.json',{'creators':[]})
    colors=['#ce6a76','#c49354','#6f9c84','#5287a5','#756ac7','#b65c91','#a4ac50','#4caaa2','#da8859']
    for n,color in enumerate(colors):
        size=(480,720) if n%3==0 else (800,480) if n%3==1 else (600,600)
        with Image.new('RGB',size,color) as image:
            draw=ImageDraw.Draw(image);draw.rectangle((0,0,size[0]-1,size[1]-1),outline='white',width=12)
            draw.text((35,35),'Image '+str(n+1),fill='black',font_size=42)
            image.save(images/f'image-{n+1}.jpg')
    return store,images


class LayoutTests(unittest.TestCase):
    def test_image_shaped_rows_do_not_make_a_tiny_strip_in_portrait_output(self):
        sizes=[(480,720),(800,480),(600,600)]*3
        for shape in FORMATS:
            heights=[r[3]-r[1] for _,r in geometry(sizes,'rows',shape)]
            self.assertLess(max(heights)/min(heights),3)

    def test_every_shape_layout_and_slot_count_has_bounded_separate_cells(self):
        for count in range(2,10):
            sizes=[(300,500),(800,300),(200,200),(400,900),(750,450),(100,600),(1000,200),(300,800),(500,400)][:count]
            for shape,(w,h) in FORMATS.items():
                for layout in LAYOUTS:
                    for title in (True,False):
                        with self.subTest(count=count,shape=shape,layout=layout,title=title):
                            cells=geometry(sizes,layout,shape,title)
                            self.assertEqual(sorted(i for i,_ in cells),list(range(count)))
                            for i,(x0,y0,x1,y1) in cells:
                                self.assertTrue(0<=x0<x1<=w);self.assertTrue(0<=y0<y1<=h)
                                for j,(a,b,c,d) in cells:
                                    if i!=j:self.assertFalse(min(x1,c)>max(x0,a)+.01 and min(y1,d)>max(y0,b)+.01)

    def test_full_images_keep_corner_markers_transparency_and_exif_rotation(self):
        with tempfile.TemporaryDirectory() as temp:
            sources=[]
            for n in range(5):
                image=Image.new('RGB' if n==0 else 'RGBA',(300,500) if n%2 else (600,300),'#449966')
                draw=ImageDraw.Draw(image);w,h=image.size
                for rect,color in [((0,0,49,49),'red'),((w-50,0,w-1,49),'blue'),((0,h-50,49,h-1),'yellow'),((w-50,h-50,w-1,h-1),'white')]:draw.rectangle(rect,fill=color)
                path=Path(temp)/f'{n}.png'
                if n==0:
                    path=path.with_suffix('.jpg');exif=Image.Exif();exif[274]=6;image.save(path,quality=100,subsampling=0,exif=exif);size=(h,w)
                else:
                    draw.rectangle((w//2-10,h//2-10,w//2+10,h//2+10),fill=(0,0,0,0));image.save(path);size=image.size
                image.close();sources.append({'name':path.name,'width':size[0],'height':size[1],'open':lambda p=path:p.open('rb')})
            settings={'layout':'grid','shape':'landscape','theme':'dark','title':''}
            for preview in (False,True):
                output=Path(temp)/f'result-{preview}.jpg';report=render(sources,settings,output,preview=preview)
                with Image.open(output) as result:
                    self.assertEqual(result.size,(1280,720) if preview else (3840,2160))
                    scale=result.width/3840
                    for n,rect in geometry([(s['width'],s['height']) for s in sources],'grid'):
                        # Obtain the geometry with the same optional-title setting.
                        rect=dict(geometry([(s['width'],s['height']) for s in sources],'grid',title=False))[n]
                        x0,y0,x1,y1=[round(v*scale) for v in rect];sw,sh=sources[n]['width'],sources[n]['height']
                        fit=min((x1-x0)/sw,(y1-y0)/sh);left=x0+((x1-x0)-sw*fit)/2;top=y0+((y1-y0)-sh*fit)/2
                        colors=[(255,0,0),(0,0,255),(255,255,0),(255,255,255)]
                        if n==0:colors=[colors[2],colors[0],colors[3],colors[1]]
                        for (x,y),color in zip([(20,20),(sw-20,20),(20,sh-20),(sw-20,sh-20)],colors):
                            pixel=result.getpixel((round(left+x*fit),round(top+y*fit)))
                            self.assertLess(max(abs(a-b) for a,b in zip(pixel,color)),35)
                        if n:
                            pixel=result.getpixel((round(left+sw*fit/2),round(top+sh*fit/2)))
                            self.assertLess(max(pixel),25)
                    self.assertEqual(report['width'],result.width)


class CollageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store,self.images=fixture(self.root);self.collages=CollageStore(self.store)
        self.opened=self.collages.index('Example','2026-08')
        self.payload={**self.opened['selection'],'release_id':self.opened['release']['id'],'revision':0}
        self.payload['images']=[i['id'] for i in self.opened['images'][:6]]

    def start(self,preview):
        with patch('collages.subprocess.Popen') as spawn:job=self.collages.start_export({'preview_id':preview['id']})
        spawn.assert_called_once();return job

    def test_artist_prefixed_and_named_release_folders_open_and_export(self):
        old=self.images.parent
        renamed=old.with_name('Example 2026-08');old.rename(renamed)
        welcome=renamed.with_name('Example Welcome Pack')/'release_images';welcome.mkdir(parents=True)
        (welcome/'welcome.jpg').write_bytes((renamed/'release_images/image-1.jpg').read_bytes())
        hidden=renamed.with_name('.previous_versions')/'release_images';hidden.mkdir(parents=True)
        linked=renamed.with_name('Linked release');linked.symlink_to(renamed,target_is_directory=True)
        self.assertEqual(self.collages.months('Example'),['Example 2026-08','Example Welcome Pack'])
        opened=self.collages.index('Example','Example 2026-08')
        self.assertEqual(len(opened['images']),9)
        payload={**opened['selection'],'release_id':opened['release']['id'],'revision':0,'images':[i['id'] for i in opened['images'][:6]]}
        job=self.start(self.collages.preview(payload));self.collages.run_export(job['id'])
        result=self.collages.export_status(job['id']);self.assertEqual(result['state'],'completed',result['message'])
        self.assertTrue((renamed/'Example-2026-08.jpg').is_file());self.assertFalse(old.exists())
        self.assertEqual(len(self.collages.index('Example','Example Welcome Pack')['images']),1)
        self.assertEqual(collage_filename({'folder':'Example','month':'Example Welcome Pack'}),'Example-Welcome Pack.jpg')
        for bad in ('../escape','Example/2026-08','.previous_versions'):
            with self.assertRaises(CollageError):self.collages.index('Example',bad)

    def test_index_draft_and_layout_do_not_export_or_modify_originals(self):
        before={p.name:(p.stat().st_mtime_ns,hashlib.sha256(p.read_bytes()).hexdigest()) for p in self.images.iterdir()}
        self.assertEqual(self.collages.months('Example'),['2026-08'])
        self.assertEqual(self.opened['selection']['images'],[None]*6)
        partial={**self.payload,'images':[self.payload['images'][0],None,None,None,None,None]}
        self.assertEqual(len(self.collages.layout(partial)['cells']),6)
        self.assertEqual(self.collages.save_selection(partial),{'revision':1})
        self.assertEqual(self.collages.index('Example','2026-08')['selection']['images'],partial['images'])
        with self.assertRaises(FileExistsError):self.collages.save_selection(partial)
        with self.assertRaises(CollageError):self.collages.preview(partial)
        self.assertFalse((self.images.parent/'release_collages').exists())
        self.assertEqual(before,{p.name:(p.stat().st_mtime_ns,hashlib.sha256(p.read_bytes()).hexdigest()) for p in self.images.iterdir()})

    def test_manual_export_replaces_named_jpeg_and_keeps_previous_version_locally(self):
        archive=self.images.parent/'Example 2026-08.7z';archive.write_bytes(b'Existing release archive')
        preview=self.collages.preview(self.payload)
        self.assertFalse(list(self.images.parent.glob('*.jpg')))
        self.assertFalse((self.images.parent/'release_collages').exists())
        job=self.start(preview)
        self.assertFalse(list(self.images.parent.glob('*.jpg')))
        self.assertFalse((self.images.parent/'release_collages').exists())
        with self.assertRaises(FileExistsError):self.start(preview)
        self.collages.run_export(job['id']);result=self.collages.export_status(job['id'])
        self.assertEqual(result['state'],'completed',result['message'])
        data,name=self.collages.result(job['id'])
        self.assertEqual(name,'Example-2026-08.jpg')
        self.assertEqual(collage_filename({'folder':'- Artist Name','month':'2026-08'}),'Artist Name-2026-08.jpg')
        with Image.open(io.BytesIO(data)) as image:self.assertEqual(image.size,(3840,2160))
        self.assertEqual(hashlib.sha256(data).hexdigest(),result['sha256'])
        self.assertEqual(result['bytes'],len(data));self.assertFalse((self.collages.cache/(job['id']+'-output.jpg')).exists())
        second=self.start(self.collages.preview({**self.payload,'theme':'light'}));self.collages.run_export(second['id'])
        self.assertEqual(self.collages.export_status(second['id'])['state'],'completed')
        self.assertEqual(second['filename'],name);self.assertEqual(len(list(self.images.parent.glob('*.jpg'))),1)
        replacement,_=self.collages.result(second['id']);self.assertNotEqual(replacement,data)
        self.assertEqual((self.images.parent/name).read_bytes(),replacement)
        self.assertEqual(self.collages.result(job['id']),(data,name))
        self.assertTrue(self.collages.export_status(job['id'])['backup_available'])
        self.assertEqual(self.collages.version_path(result['sha256']).read_bytes(),data)
        self.assertFalse((self.images.parent/'release_collages').exists())
        self.assertEqual(archive.read_bytes(),b'Existing release archive')
        (self.images.parent/name).unlink()
        self.assertEqual(self.collages.export_status(job['id'])['state'],'completed')
        with self.assertRaises(CollageError):self.collages.result(second['id'])
        self.assertEqual(self.collages.result(job['id']),(data,name))

    def test_older_saved_versions_remain_accessible_and_can_move_beside_archives(self):
        job=self.start(self.collages.preview(self.payload));self.collages.run_export(job['id'])
        expected,name=self.collages.result(job['id'])
        old=self.images.parent/'release_collages';old.mkdir()
        current=self.images.parent/name;current.rename(old/name)
        self.assertEqual(self.collages.result(job['id']),(expected,name))
        (old/name).rename(current)
        self.assertEqual(self.collages.result(job['id']),(expected,name))

    def test_changed_original_after_preview_blocks_export_even_if_metadata_is_restored(self):
        preview=self.collages.preview(self.payload);original=self.images/'image-1.jpg';info=original.stat();data=bytearray(original.read_bytes());data[-20]^=1;original.write_bytes(data);os.utime(original,ns=(info.st_atime_ns,info.st_mtime_ns))
        job=self.start(preview);self.collages.run_export(job['id'])
        self.assertEqual(self.collages.export_status(job['id'])['state'],'failed')
        self.assertIn('content changed',self.collages.export_status(job['id'])['message'])
        self.assertFalse((self.images.parent/'release_collages').exists())

    def test_delivery_retry_reuses_verified_stage_and_does_not_overwrite(self):
        preview=self.collages.preview(self.payload);job=self.start(preview)
        with patch('collages.deliver',side_effect=OSError('Share unavailable')):self.collages.run_export(job['id'])
        self.assertEqual(self.collages.export_status(job['id'])['state'],'failed')
        stage=self.collages.cache/(job['id']+'-output.jpg');self.assertTrue(stage.exists())
        with patch('collages.subprocess.Popen'):retry=self.collages.start_export({'retry_id':job['id']})
        with patch('collages.render',side_effect=AssertionError('Must reuse rendered output')):self.collages.run_export(job['id'])
        self.assertEqual(self.collages.export_status(job['id'])['state'],'completed');self.assertEqual(retry['filename'],job['filename'])
        other=self.start(preview);target=self.images.parent/other['filename'];target.write_bytes(b'Existing user file')
        self.collages.run_export(other['id']);self.assertEqual(self.collages.export_status(other['id'])['state'],'failed');self.assertEqual(target.read_bytes(),b'Existing user file')

    def test_failed_replacement_restores_previous_collage_and_retry_keeps_backup(self):
        original=self.start(self.collages.preview(self.payload));self.collages.run_export(original['id']);old,name=self.collages.result(original['id'])
        job=self.start(self.collages.preview({**self.payload,'theme':'light'}))
        with patch('collages.deliver',side_effect=OSError('Share unavailable')):self.collages.run_export(job['id'])
        self.assertEqual(self.collages.export_status(job['id'])['state'],'failed')
        self.assertEqual((self.images.parent/name).read_bytes(),old)
        self.assertEqual(self.collages.result(original['id']),(old,name))
        with patch('collages.subprocess.Popen'):self.collages.start_export({'retry_id':job['id']})
        with patch('collages.render',side_effect=AssertionError('Reuse staged output')):self.collages.run_export(job['id'])
        self.assertEqual(self.collages.export_status(job['id'])['state'],'completed')
        self.assertNotEqual((self.images.parent/name).read_bytes(),old)
        self.assertFalse(list(self.images.parent.glob('.collage-*')))
        self.assertFalse((self.collages.cache/(job['id']+'-replacement.json')).exists())

    def test_interruption_during_replacement_recovers_on_explicit_retry(self):
        original=self.start(self.collages.preview(self.payload));self.collages.run_export(original['id']);old,name=self.collages.result(original['id'])
        job=self.start(self.collages.preview({**self.payload,'theme':'light'}))
        with patch('collages.deliver',side_effect=KeyboardInterrupt),self.assertRaises(KeyboardInterrupt):self.collages.run_export(job['id'])
        self.assertFalse((self.images.parent/name).exists())
        self.assertEqual(self.collages.result(original['id']),(old,name))
        with self.collages.db() as db:db.execute('UPDATE exports SET heartbeat=0 WHERE id=?',(job['id'],))
        self.assertEqual(self.collages.export_status(job['id'])['state'],'interrupted')
        with patch('collages.subprocess.Popen'):self.collages.start_export({'retry_id':job['id']})
        self.collages.run_export(job['id']);self.assertEqual(self.collages.export_status(job['id'])['state'],'completed')
        self.assertNotEqual((self.images.parent/name).read_bytes(),old)
        self.assertEqual(self.collages.result(original['id']),(old,name))
        self.assertFalse(list(self.images.parent.glob('.collage-*')))

    def test_interruption_after_new_file_arrives_can_complete_the_same_save(self):
        from file_delivery import deliver
        original=self.start(self.collages.preview(self.payload));self.collages.run_export(original['id']);old,name=self.collages.result(original['id'])
        job=self.start(self.collages.preview({**self.payload,'theme':'light'}))
        def delivered_then_interrupted(*args,**kwargs):
            deliver(*args,**kwargs)
            raise KeyboardInterrupt
        with patch('collages.deliver',side_effect=delivered_then_interrupted),self.assertRaises(KeyboardInterrupt):self.collages.run_export(job['id'])
        newer=(self.images.parent/name).read_bytes();self.assertNotEqual(newer,old)
        with self.collages.db() as db:db.execute('UPDATE exports SET heartbeat=0 WHERE id=?',(job['id'],))
        self.assertEqual(self.collages.export_status(job['id'])['state'],'interrupted')
        with patch('collages.subprocess.Popen'):self.collages.start_export({'retry_id':job['id']})
        self.collages.run_export(job['id']);self.assertEqual(self.collages.export_status(job['id'])['state'],'completed')
        self.assertEqual(self.collages.result(job['id']),(newer,name))
        self.assertEqual(self.collages.result(original['id']),(old,name))
        self.assertFalse(list(self.images.parent.glob('.collage-*')))

    def test_symlinks_unsupported_images_cross_release_and_changed_root_are_rejected(self):
        (self.images/'linked.jpg').symlink_to(self.images/'image-1.jpg');(self.images/'active.svg').write_text('<svg/>');(self.images/'broken.jpg').write_bytes(b'broken')
        indexed=self.collages.index('Example','2026-08');self.assertTrue(indexed['warnings'])
        for record in indexed['images']:
            if record['name'] in ('active.svg','broken.jpg'):
                self.assertTrue(record['reason'])
                with self.assertRaises(CollageError):self.collages.thumbnail(record['id'])
        with self.assertRaises(ValueError):self.collages.months('../Example')
        with self.assertRaises(ValueError):self.collages.index('Example','../../elsewhere')
        self.payload['images'][1]=self.payload['images'][0]
        with self.assertRaises(CollageError):self.collages.preview(self.payload)
        other=self.images.parent.parent/'2026-07'/'release_images';other.mkdir(parents=True);(other/'a.jpg').write_bytes((self.images/'image-1.jpg').read_bytes())
        foreign=self.collages.index('Example','2026-07')['images'][0]['id'];self.payload['images'][1]=foreign
        with self.assertRaises(CollageError):self.collages.save_selection(self.payload)
        original=self.images/'image-1.jpg';original.unlink();original.symlink_to(other/'a.jpg')
        with self.assertRaises(ValueError):self.collages.thumbnail(self.payload['images'][0])
        newbase=self.root/'other';newbase.mkdir();self.store.atomic_write(self.store.config_path,{'revision':1,'download_directory':str(newbase)})
        with self.assertRaises(CollageError):self.collages.release(self.payload['release_id'])

    def test_stale_export_is_recoverable_and_thumbnail_has_correct_orientation(self):
        path=self.images/'rotated.jpg'
        with Image.new('RGB',(600,300),'red') as image:
            exif=Image.Exif();exif[274]=6;image.save(path,exif=exif)
        record=next(i for i in self.collages.index('Example','2026-08')['images'] if i['name']=='rotated.jpg')
        self.assertEqual((record['width'],record['height']),(300,600))
        with Image.open(io.BytesIO(self.collages.thumbnail(record['id']))) as thumb:self.assertEqual(thumb.size,(150,300))
        job=self.start(self.collages.preview(self.payload))
        with self.collages.db() as db:db.execute('UPDATE exports SET heartbeat=0 WHERE id=?',(job['id'],))
        self.assertEqual(self.collages.export_status(job['id'])['state'],'interrupted')


if __name__=='__main__':unittest.main()
