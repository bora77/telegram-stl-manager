import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from test_support import configure_source
from subscription_store import SubscriptionStore
from mmf_manager import MMFManager
from mmf_release_prepare import save as save_plan, rows as plans
from mmf_release_upload import start,execute,attempts,save,status
from telegram_cli import require_release_collage

class FakeCLI:
    def __init__(self,missing_photo=False):self.messages=[];self.uploads=[];self.missing_photo=missing_photo
    def destinations(self):return {'destinations':[{'id':'-123','title':'Example Pad'}]}
    def close(self):pass
    def _run(self,args,*rest,**kwargs):
        if args[:2]==['stl','release-history']:
            Path(args[args.index('--output')+1]).write_text(json.dumps({'id':123,'messages':self.messages}));return
        photo='--photo' in args;path=Path(args[args.index('--path')+1]);self.uploads.append(path.name)
        if not photo:require_release_collage(path)
        assert args[:2]==['stl','release-upload']
        Path(args[args.index('--progress')+1]).write_text(json.dumps({'uploaded':path.stat().st_size,'speed_mbps':5}))
        kwargs.get('tick',lambda:None)()
        if photo and self.missing_photo:return
        self.messages.append({'id':len(self.messages)+1,'file':path.name,'raw':{'PeerID':{'ChatID':123},'Out':True,'Message':args[args.index('--caption')+1],'Media':{'Photo':{'ID':1}} if photo else {'Document':{'Size':path.stat().st_size}}}})

class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=configure_source(Path(self.temp.name));self.manager=MMFManager(SubscriptionStore(root));store=self.manager.store
        store.atomic_write(store.config_path,{'revision':0,'download_directory':str(root),'release_pad_destination':'-123'})
        self.folder=root/'- Example'/'Example 2026-09';self.folder.mkdir(parents=True)
        self.archive=self.folder/'Example 2026-09.7z';self.archive.write_bytes(b'fake archive for transfer test')
        self.collage=self.folder/'Example-2026-09.jpg';Image.new('RGB',(64,64)).save(self.collage)
        plans(self.manager)
        self.plan={'id':'prepared','state':'complete','title':'Example 2026-09','directory':str(self.folder),'base':str(root),'folder':'- Example','release_folder':self.folder.name,'outputs':[{'path':str(self.archive)}]};save_plan(self.manager,self.plan)
    def begin(self):
        with patch('mmf_release_upload.subprocess.Popen'):
            result=start(self.manager,{'preparation_id':'prepared'})
        return next(r for r in attempts(self.manager) if r['id']==result['attempt_id'])
    def test_retry_allowed_for_every_previous_outcome(self):
        for state in ('complete','failed','deleted','interrupted'):
            first=self.begin();first['state']=state;save(self.manager,first)
            second=self.begin();self.assertNotEqual(first['id'],second['id']);self.assertGreater(second['number'],first['number'])
    def test_collage_is_sent_first_and_each_delivery_recorded(self):
        row=self.begin();client=FakeCLI();execute(self.manager,row,client)
        self.assertEqual(client.uploads,[self.collage.name,self.archive.name]);self.assertEqual(row['state'],'complete');self.assertEqual(len(row['messages']),2);self.assertEqual(row['bytes'],row['total'])
        self.assertTrue(self.archive.exists());self.assertTrue(self.collage.exists())
        execute(self.manager,self.begin(),FakeCLI())
        self.assertEqual(len(attempts(self.manager)),2)
    def test_unverified_collage_blocks_archive_and_allows_retry(self):
        row=self.begin();client=FakeCLI(missing_photo=True)
        with self.assertRaisesRegex(Exception,'verify delivery'):execute(self.manager,row,client)
        self.assertEqual(client.uploads,[self.collage.name]);self.assertEqual(row['state'],'failed')
        self.assertNotEqual(row['id'],self.begin()['id'])
    def test_missing_collage_blocks_start(self):
        self.collage.unlink()
        with patch('mmf_release_upload.subprocess.Popen') as launch:
            with self.assertRaisesRegex(Exception,'collage'):start(self.manager,{'preparation_id':'prepared'})
            launch.assert_not_called()
    def test_changed_destination_blocks_before_sending(self):
        row=self.begin();client=FakeCLI();client.destinations=lambda:{'destinations':[]}
        with self.assertRaisesRegex(Exception,'unavailable'):execute(self.manager,row,client)
        self.assertEqual(client.uploads,[])
    def test_stale_attempt_is_interrupted_not_permanently_busy(self):
        self.begin();view=status(self.manager)
        self.assertFalse(view['active']);self.assertEqual(view['attempts'][0]['state'],'interrupted')

if __name__=='__main__':unittest.main()
