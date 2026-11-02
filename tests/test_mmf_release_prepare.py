import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from PIL import Image
from app.mmf_manager import MMFManager, releases
from app.mmf_release_prepare import start, rows, execute, preparation_status
from app.mmf_repack import POLICY_VERSION
from app.file_delivery import digest
from app.release_images import listing
from app.subscription_store import SubscriptionStore

class PrepareTests(unittest.TestCase):
    def test_confirmation_schedules_cleanup_without_reading_archive_bytes(self):
        from app.mmf_release_prepare import save,confirm_released
        from app.mmf_release_upload import attempts,save as save_upload
        with tempfile.TemporaryDirectory() as d:
            manager=MMFManager(SubscriptionStore(Path(d)));rows(manager);attempts(manager)
            plan={'id':'p','state':'complete','keys':['A'],'items':[{'key':'A'}],'outputs':[{'path':'release.7z','size':10,'sha256':'hash'}]}
            save(manager,plan)
            save_upload(manager,{'id':'u','preparation_id':'p','state':'complete','files':[{'name':'collage.jpg'},{'name':'release.7z','size':10,'sha256':'hash'}]})
            with patch('app.mmf_release_prepare.Popen') as launch,patch('app.mmf_release_prepare.cleanup_published_sources',side_effect=AssertionError('Must not clean in HTTP request')):
                payload={'preparation_id':'p','attempt_id':'u','confirmed':True}
                self.assertTrue(confirm_released(manager,payload)['recorded'])
                confirm_released(manager,payload)
                launch.assert_called_once()
                self.assertIn('cleanup:p',launch.call_args.args[0])
            saved=rows(manager)[0]
            self.assertTrue(saved['publication']);self.assertEqual(saved['source_cleanup']['state'],'queued')

    def test_published_versions_supersede_failed_workflow_but_not_new_or_active_work(self):
        from app.mmf_release_prepare import save,release_lifecycle
        from app.mmf_release_upload import save as save_upload
        with tempfile.TemporaryDirectory() as d:
            manager=MMFManager(SubscriptionStore(Path(d)));rows(manager)
            save(manager,{'id':'package','number':0,'state':'complete','release_key':'release','title':'Release','directory':d,'keys':['A'],'publication':{'keys':['A']}})
            attempt={'id':'attempt','preparation_id':None,'state':'failed','title':'Release','created_at':1,'workflow':{'release_key':'release','keys':['A']}}
            preparation_status(manager)
            save_upload(manager,attempt)
            items=[{'key':'A','release_key':'release'}]
            summary=preparation_status(manager)
            self.assertEqual(len(summary['release']['packages']),1)
            self.assertTrue(release_lifecycle(items,summary)['release']['finished'])
            attempt['workflow']['keys']=['B'];save_upload(manager,attempt)
            self.assertFalse(release_lifecycle(items,preparation_status(manager))['release']['finished'])
            attempt['workflow']['keys']=['A'];attempt['state']='queued';save_upload(manager,attempt)
            self.assertFalse(release_lifecycle(items,preparation_status(manager))['release']['finished'])

    def test_cleanup_requires_publication_and_preserves_release_and_new_sources(self):
        from app.mmf_release_prepare import cleanup_published_sources,save
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manager=MMFManager(SubscriptionStore(root));directory=root/'Artist'/'Artist 2025-01'
            sources=directory/'MMF sources';sources.mkdir(parents=True)
            original=sources/'source.zip';original.write_bytes(b'source')
            newer=sources/'new.zip';newer.write_bytes(b'new')
            archive=directory/'release.7z';archive.write_bytes(b'packed')
            images=directory/'release_images';images.mkdir();(images/'preview.png').write_bytes(b'image')
            receipt=lambda p:{'path':str(p),'size':p.stat().st_size,'sha256':digest(p)}
            item={'key':'A','repack':{'source_keys':['A'],'outputs':[receipt(original)]}}
            plan={'id':'cleanup','base':d,'folder':'Artist','release_folder':directory.name,'items':[item],'keys':['A'],'outputs':[receipt(archive)]}
            rows(manager);save(manager,plan)
            with manager.db() as db:db.execute('INSERT INTO completed VALUES (?,?)',('A',json.dumps(item)))
            cleanup_published_sources(manager,plan);self.assertTrue(original.exists())
            plan['publication']={'keys':['A']}
            original.write_bytes(b'changed');cleanup_published_sources(manager,plan)
            self.assertTrue(original.exists());self.assertEqual(plan['source_cleanup']['state'],'warning')
            original.write_bytes(b'source');cleanup_published_sources(manager,plan)
            self.assertFalse(original.exists());self.assertTrue(newer.exists());self.assertTrue(archive.exists());self.assertTrue((images/'preview.png').exists())
            with manager.db() as db:record=json.loads(db.execute('SELECT data FROM completed WHERE key=?',('A',)).fetchone()[0])
            self.assertEqual(record['source_cleanup']['state'],'complete')
            cleanup_published_sources(manager,plan);self.assertEqual(plan['source_cleanup']['state'],'complete')
            self.assertEqual(plan['source_cleanup']['checked_files'],1) # Sources are already gone on retry.
            self.assertEqual(plan['source_cleanup']['bytes'],archive.stat().st_size)
            self.assertEqual(plan['source_cleanup']['removed_files'],1)

    def test_verification_reports_incremental_bytes_and_still_checks_hash(self):
        from app.mmf_release_prepare import verified
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'archive';path.write_bytes(b'x'*(3*1024*1024+7))
            receipt={'size':path.stat().st_size,'sha256':digest(path)};updates=[]
            verified(path,receipt,d,updates.append)
            self.assertEqual(len(updates),4);self.assertEqual(updates[-1],receipt['size'])
            with self.assertRaisesRegex(ValueError,'changed'):verified(path,{**receipt,'sha256':'0'*64},d,lambda n:None)

    def test_packaging_and_retry_exclude_removed_historical_archives(self):
        from app.mmf_release_prepare import save
        with tempfile.TemporaryDirectory() as d:
            manager=MMFManager(SubscriptionStore(Path(d)))
            old={'key':'old','release_key':'release','folder':'Artist','release_folder':'Artist 2025-01'}
            new={**old,'key':'new','completed':True}
            with patch.object(manager.store,'config',return_value={'download_directory':d}),patch.object(manager,'state',return_value={'items':[new]}),patch('app.mmf_release_prepare.downloaded',return_value=[old,new]),patch('app.mmf_release_prepare.require_release_collage'),patch('app.mmf_release_prepare.Popen'):
                start(manager,{'release_key':'release'});plan=rows(manager)[0]
                self.assertEqual(plan['keys'],['new'])
                plan.update(state='failed',items=[old,new],keys=['old','new']);save(manager,plan)
                start(manager,{'release_key':'release'});retried=rows(manager)[0]
                self.assertEqual(retried['id'],plan['id']);self.assertEqual(retried['keys'],['new'])
                self.assertEqual([i['key'] for i in retried['items']],['new'])

    def test_make_release_uses_cleaner_config_and_reports_progress(self):
        from tests.test_stl_cleaner import mesh
        from app.collages import collage_filename
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manager=MMFManager(SubscriptionStore(root));base=root/'nas'
            directory=base/'Artist'/'Artist Campaign';directory.mkdir(parents=True)
            archive=directory/'source.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('Original Model.stl',mesh())
            original=archive.read_bytes()
            item={'key':'A','creator_id':1,'creator':'Artist','folder':'Artist','object_id':1,'archive_id':1,'object_name':'Model','filename':archive.name,'release':'Campaign','release_id':'FRONTIER:50:1','size':archive.stat().st_size,'repack':{'id':'A','kind':'original_archives','outputs':[{'path':str(archive),'size':archive.stat().st_size,'sha256':digest(archive)}],'images':[]}}
            with manager.db() as db:db.execute('INSERT INTO completed VALUES (?,?)',('A',json.dumps(item)))
            releases([item]);Image.new('RGB',(64,64),'blue').save(directory/collage_filename({'folder':'Artist','month':directory.name}))
            images=directory/'release_images';images.mkdir()
            Image.new('RGB',(32,48),'red').save(images/'gallery.png')
            with patch.object(manager.store,'config',return_value={'download_directory':str(base),'release_stl_cleaner':'delicates','release_compression_level':0}),patch.object(manager,'state',return_value={'items':[]}),patch('app.mmf_release_prepare.Popen'),patch.object(manager,'progress',wraps=manager.progress) as progress:
                start(manager,{'release_key':item['release_key']});plan=rows(manager)[0]
                self.assertEqual(plan['stl_cleaner'],'delicates');execute(manager,plan)
                events=[c.kwargs for c in progress.call_args_list if c.kwargs.get('phase')=='cleaning_stls']
                self.assertTrue(events);self.assertEqual(events[-1]['cleaner_done'],1);self.assertEqual(events[-1]['cleaner_total'],1)
            self.assertEqual(archive.read_bytes(),original)
            _,members=listing(Path(plan['outputs'][0]['path']),root,lambda:False)
            self.assertIn('release_images/gallery.png',[m['name'] for m in members])
            receipt=json.loads((manager.directory/'release-preparation'/plan['id']/'packed/repack.json').read_text())
            self.assertEqual(len(receipt['cleaned_stls']),1)
            self.assertNotEqual(receipt['cleaned_stls'][0]['before']['sha256'],receipt['cleaned_stls'][0]['after']['sha256'])

    def test_release_router_reuses_package_and_checks_snapshot(self):
        from app.mmf_release_flow import start as route
        from app.mmf_release_prepare import save
        with tempfile.TemporaryDirectory() as d:
            manager=MMFManager(SubscriptionStore(Path(d)))
            item={'key':'A','release_key':'release','creator':'Artist','completed':True,'release_month':'2026-09','folder':'Artist','release_folder':'Artist 2026-09'}
            rows(manager);save(manager,{'id':'old','state':'complete','release_key':'release','keys':['A'],'number':0,'outputs':[{'path':'example.7z'}]})
            payload={'mode':'pad','release_key':'release','keys':['A']}
            with patch.object(manager.store,'config',return_value={'release_pad_destination':'-123','download_directory':d}),patch.object(manager,'state',return_value={'active':True,'items':[item]}),patch('app.mmf_release_upload.start',return_value={'started':True}) as upload,patch('app.mmf_release_prepare.start') as package,patch('app.telegram_cli.require_release_collage'):
                route(manager,payload);package.assert_not_called();self.assertEqual(upload.call_args.args[1]['preparation_id'],'old')
                with self.assertRaisesRegex(ValueError,'changed'):route(manager,{**payload,'keys':['B']})
                item['key']='B';route(manager,{**payload,'keys':['B']});package.assert_not_called()
                self.assertEqual(upload.call_args.args[1]['workflow']['keys'],['B'])
                self.assertEqual(upload.call_args.args[1]['delivery']['destination'],'-123')
                item['completed']=False
                with self.assertRaisesRegex(ValueError,'Download the release files'):route(manager,{**payload,'keys':['B']})
                route(manager,{**payload,'keys':['B'],'preparation_id':'old'})
                self.assertEqual(upload.call_args.args[1]['preparation_id'],'old')
                with self.assertRaisesRegex(ValueError,'no longer available'):route(manager,{**payload,'keys':['B'],'preparation_id':'missing'})

    def test_named_frontier_packages_and_addenda(self):
        from app.collages import collage_filename
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manager=MMFManager(SubscriptionStore(root));base=root/'nas'
            directory=base/'Artist'/'Artist Campaign';directory.mkdir(parents=True)
            def install(letter):
                archive=directory/(letter+'.zip')
                with zipfile.ZipFile(archive,'w') as z:z.writestr('Original Model.stl',letter)
                item={'key':letter,'creator_id':1,'creator':'Artist','folder':'Artist','object_id':ord(letter),'archive_id':ord(letter),'object_name':letter,'filename':archive.name,'release':'Campaign','release_id':'FRONTIER:50:1','size':archive.stat().st_size,'repack':{'id':letter,'kind':'original_archives','outputs':[{'path':str(archive),'size':archive.stat().st_size,'sha256':digest(archive)}],'images':[]}}
                with manager.db() as db:db.execute('INSERT INTO completed VALUES (?,?)',(letter,json.dumps(item)))
                releases([item]);return item
            first=install('A');key=first['release_key']
            with patch.object(manager.store,'config',return_value={'download_directory':str(base)}),patch.object(manager,'state',return_value={'items':[]}),patch('app.mmf_release_prepare.Popen'):
                with self.assertRaisesRegex(Exception,'collage'):start(manager,{'release_key':key})
                Image.new('RGB',(64,64),'blue').save(directory/collage_filename({'folder':'Artist','month':directory.name}))
                start(manager,{'release_key':key});plan=rows(manager)[0];execute(manager,plan)
                self.assertEqual(Path(plan['outputs'][0]['path']),directory/'Artist Campaign.7z')
                install('B');start(manager,{'release_key':key})
                addendum=next(p for p in rows(manager) if p['number']==1)
                self.assertEqual(addendum['keys'],['B']);execute(manager,addendum)
                self.assertEqual(Path(addendum['outputs'][0]['path']),directory/'Artist Campaign Addendum 1.7z')
                self.assertTrue((directory/'A.zip').exists());self.assertTrue((directory/'B.zip').exists())

    def test_manual_finish_without_package_and_later_additions(self):
        from app.mmf_release_prepare import finish_manually, release_lifecycle
        with tempfile.TemporaryDirectory() as d:
            manager=MMFManager(SubscriptionStore(Path(d)))
            items=[{'key':'A','release_key':'release','release_folder':'Artist 2026-09'}]
            payload={'release_key':'release','keys':['A'],'confirmed':True}
            with patch.object(manager,'state',return_value={'items':items}):
                with self.assertRaises(ValueError):finish_manually(manager,{**payload,'confirmed':False})
                with self.assertRaises(FileExistsError):finish_manually(manager,{**payload,'keys':[]})
                finish_manually(manager,payload);finish_manually(manager,payload)
            self.assertEqual(len(rows(manager)),1);self.assertEqual(manager.completed(),set())
            summary=preparation_status(manager)
            self.assertTrue(release_lifecycle(items,summary)['release']['finished'])
            self.assertEqual(summary['release']['number'],0)
            items.append({**items[0],'key':'B'})
            self.assertFalse(release_lifecycle(items,summary)['release']['finished'])
            with patch.object(manager,'state',return_value={'items':items}):finish_manually(manager,{**payload,'keys':['A','B']})
            self.assertEqual(preparation_status(manager)['release']['number'],1)
            self.assertTrue(release_lifecycle(items,preparation_status(manager))['release']['finished'])

    def test_manual_finish_closes_prepared_and_failed_packages(self):
        from app.mmf_release_prepare import finish_manually, save, release_lifecycle
        with tempfile.TemporaryDirectory() as d:
            manager=MMFManager(SubscriptionStore(Path(d)));rows(manager)
            for number,state in enumerate(['complete','failed']):
                save(manager,{'id':str(number),'number':number,'state':state,'title':'Release','directory':d,'release_key':'release','keys':[str(number)]})
            items=[{'key':str(n),'release_key':'release','release_folder':'Artist 2026-09'} for n in range(2)]
            with patch.object(manager,'state',return_value={'items':items}):finish_manually(manager,{'release_key':'release','keys':['0','1'],'confirmed':True})
            summary=preparation_status(manager)
            self.assertTrue(release_lifecycle(items,summary)['release']['finished'])
            self.assertNotIn('pending',summary['release'])
            self.assertTrue(summary['release']['packages'][0]['published'])

    def test_initial_addendum_retry_and_changed_version(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manager=MMFManager(SubscriptionStore(root));base=root/'nas';folder=base/'Artist'/'Artist 2025-01';folder.mkdir(parents=True)
            def install(keys):
                archive=folder/'download.zip'
                with zipfile.ZipFile(archive,'w') as z:
                    for key in keys:z.writestr(key+'/model.stl',key.encode())
                manifest={'id':'manifest'+''.join(keys),'release_name':'Artist 2025-01','outputs':[{'path':str(archive),'size':archive.stat().st_size,'sha256':digest(archive)}],'images':[],'source_prefixes':{key:key for key in keys}}
                items=[]
                for key in keys:
                    item={'key':key,'creator_id':1,'creator':'Artist','folder':'Artist','object_id':ord(key[0]),'archive_id':ord(key[0]),'object_name':key,'filename':key+'.zip','release':'January 2025','release_id':'old','size':1,'updated_at':key,'completed_at':len(key),'repack':manifest}
                    items.append(item)
                    with manager.db() as db:db.execute('INSERT OR REPLACE INTO completed VALUES (?,?)',(key,json.dumps(item)))
                releases(items);return items
            items=install(['A','B']);key=items[0]['release_key']
            with patch.object(manager.store,'config',return_value={'download_directory':str(base)}),patch.object(manager,'state',return_value={'items':[]}),patch('app.mmf_release_prepare.Popen'),patch('app.mmf_release_prepare.fetch_images',return_value=[]):
                collage=folder/'Artist-2025-01.jpg'
                with self.assertRaisesRegex(Exception,'collage'):start(manager,{'release_key':key})
                self.assertEqual(rows(manager),[])
                collage.write_bytes(b'broken jpeg')
                with self.assertRaisesRegex(Exception,'damaged or invalid'):start(manager,{'release_key':key})
                Image.new('RGB',(64,64)).save(collage)
                start(manager,{'release_key':key});plan=rows(manager)[0];self.assertEqual(plan['number'],0)
                collage.unlink()
                with patch('app.mmf_release_prepare.repack') as compress:
                    with self.assertRaisesRegex(Exception,'collage'):execute(manager,plan)
                    compress.assert_not_called()
                Image.new('RGB',(64,64)).save(collage)
                execute(manager,plan)
                self.assertEqual(preparation_status(manager)[key]['keys'],['A','B'])
                # A complete verified 7-Zip set needs no second compression pass.
                import copy
                reuse=copy.deepcopy(plan);reuse.update(id='reuse-test',title='Reuse test')
                for i in reuse['items']:
                    i['repack'].update(outputs=plan['outputs'],source_keys=['A','B'],compression_level=7,policy_version=POLICY_VERSION)
                with patch('app.mmf_release_prepare.repack',side_effect=AssertionError('Unnecessary compression')),patch('app.mmf_release_prepare.save'):
                    execute(manager,reuse)

                with self.assertRaisesRegex(ValueError,'No new'):start(manager,{'release_key':key})
                install(['A','B','C']);start(manager,{'release_key':key});plan=next(p for p in rows(manager) if p['number']==1)
                self.assertEqual(plan['keys'],['C']);self.assertTrue(plan['title'].endswith('Addendum 1'))
                # Failed retries reuse the same plan and number.
                start(manager,{'release_key':key});self.assertEqual(len(rows(manager)),2)
                execute(manager,plan);_,entries=listing(Path(plan['outputs'][0]['path']),root,lambda:False)
                self.assertEqual([e['name'] for e in entries],['C/model.stl'])
                install(['A','B','C2']);start(manager,{'release_key':key});plan=next(p for p in rows(manager) if p['number']==2)
                self.assertEqual(plan['keys'],['C2'])
                # Known but undownloaded files block a new release.
                with patch.object(manager,'state',return_value={'items':[{'release_key':key,'completed':False}]}):
                    with self.assertRaisesRegex(ValueError,'Download the available'):start(manager,{'release_key':key})

if __name__=='__main__':unittest.main()

class PublicationTests(unittest.TestCase):
    def test_publication_requires_confirmation_and_exact_uploaded_package(self):
        from app.mmf_release_prepare import save, confirm_released, release_lifecycle
        from app.mmf_release_upload import attempts, save as save_upload
        with tempfile.TemporaryDirectory() as d:
            manager=MMFManager(SubscriptionStore(Path(d)));rows(manager);attempts(manager)
            plan={'id':'p','number':0,'release_key':'month','state':'complete','keys':['v1'],
                  'title':'Artist 2025-01','directory':d,
                  'outputs':[{'path':d+'/release.7z','size':12,'sha256':'original'}]}
            save(manager,plan)
            upload={'id':'u','preparation_id':'p','state':'complete','created_at':1,
                    'files':[{'name':'collage.jpg'},{'name':'release.7z','size':12,'sha256':'original'}]}
            save_upload(manager,upload)
            items=[{'release_key':'month','key':'v1'}]
            def lifecycle():return release_lifecycle(items,preparation_status(manager))['month']
            self.assertFalse(lifecycle()['finished']) # Upload is not publication.
            payload={'preparation_id':'p','attempt_id':'u','confirmed':True}
            with self.assertRaises(ValueError):confirm_released(manager,{**payload,'confirmed':False})
            upload['state']='failed';save_upload(manager,upload)
            with self.assertRaises(ValueError):confirm_released(manager,payload)
            upload['state']='complete';upload['files'][1]['sha256']='dummy';save_upload(manager,upload)
            with self.assertRaisesRegex(ValueError,'differ'):confirm_released(manager,payload)
            upload['files'][1]['sha256']='original';save_upload(manager,upload)
            confirm_released(manager,payload)
            receipt=rows(manager)[0]['publication']
            confirm_released(manager,payload)
            self.assertEqual(rows(manager)[0]['publication'],receipt) # Idempotent.
            self.assertTrue(lifecycle()['finished'])
            # New files and changed versions in the SAME old month reopen it.
            for keys in (['v1','new'],['v2']):
                items[:]=[{'release_key':'month','key':key} for key in keys]
                state=lifecycle();self.assertFalse(state['finished']);self.assertTrue(state['published'])
                self.assertEqual(state['new_files'],1)
            save(manager,{**plan,'id':'p2','number':1,'title':'Artist 2025-01 Addendum 1','keys':['v2']})
            save_upload(manager,{**upload,'id':'u2','preparation_id':'p2'})
            confirm_released(manager,{'preparation_id':'p2','attempt_id':'u2','confirmed':True})
            self.assertTrue(lifecycle()['finished'])
            self.assertEqual(preparation_status(manager)['month']['published_keys'],['v1','v2'])


class CollageAvailabilityTests(unittest.TestCase):
    def test_button_requires_nonempty_image_content(self):
        from app.mmf_release_prepare import release_collages
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'Artist'/'Artist 2025-01';folder.mkdir(parents=True)
            items=[{'release_key':'r','folder':'Artist','release_folder':folder.name}]
            def ready():return release_collages(items,d)['r']['ready']
            self.assertFalse(ready())
            images=folder/'release_images';images.mkdir()
            self.assertFalse(ready())
            (images/'notes.txt').write_text('not an image')
            (images/'empty.jpg').touch()
            self.assertFalse(ready())
            outside=folder/'outside.jpg';Image.new('RGB',(32,32)).save(outside)
            (images/'linked.jpg').symlink_to(outside)
            self.assertFalse(ready())
            nested=images/'legacy';nested.mkdir();Image.new('RGB',(32,32)).save(nested/'preview.jpg')
            self.assertTrue(ready())

class PrepareImagesTests(unittest.TestCase):
    def test_prepare_extracts_without_collage_or_compression_and_reuses_images(self):
        from app.mmf_release_prepare import prepare_images, start_images
        import io
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manager=MMFManager(SubscriptionStore(root));base=root/'destination'
            folder=base/'Artist'/'Artist 2025-01';folder.mkdir(parents=True)
            source=folder/'download.zip';picture=io.BytesIO();Image.new('RGB',(32,32)).save(picture,format='PNG')
            with zipfile.ZipFile(source,'w') as archive:
                archive.writestr('images/preview.png',picture.getvalue());archive.writestr('model.stl','model')
            item={'key':'v1','creator_id':1,'creator':'Artist','folder':'Artist','object_id':1,'archive_id':1,'object_name':'Model','filename':'download.zip','release':'January 2025','size':source.stat().st_size,'repack':{'id':'manifest','images':[],'outputs':[{'path':str(source),'size':source.stat().st_size,'sha256':digest(source)}]}}
            with manager.db() as db:db.execute('INSERT INTO completed VALUES (?,?)',('v1',json.dumps(item)))
            releases([item]);key=item['release_key']
            with patch.object(manager.store,'config',return_value={'download_directory':str(base)}),patch('app.mmf_release_prepare.repack',side_effect=AssertionError('Prepare must not compress')),patch('app.mmf_release_prepare.Popen'):
                start_images(manager,{'release_key':key})
                prepare_images(manager,key)
                self.assertEqual(len(list((folder/'release_images').glob('*.png'))),1)
                self.assertFalse(list(folder.glob('*.7z')))
                self.assertFalse((folder/'Artist-2025-01.jpg').exists())
                self.assertEqual(rows(manager),[]) # No release/addendum number reserved.
                with patch('app.release_images.extract_images',side_effect=AssertionError('Images should be reused')):
                    prepare_images(manager,key)
