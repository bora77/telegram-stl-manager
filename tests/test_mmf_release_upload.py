import json
import fcntl
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from tests.test_support import configure_source
from app.subscription_store import SubscriptionStore
from app.mmf_manager import MMFManager
from app.mmf_release_prepare import save as save_plan, rows as plans
from app.mmf_release_upload import start, execute, attempts, save, status, drain, worker_lock
from app.telegram_cli import require_release_collage

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
    def test_replaced_archive_is_blocked_and_old_receipt_not_reused(self):
        from app.file_delivery import digest
        from app.mmf_release_upload import pad_source
        original=self.archive.read_bytes()
        old=self.begin();execute(self.manager,old,FakeCLI())
        self.plan['outputs'][0].update(size=len(original),sha256=digest(self.archive));save_plan(self.manager,self.plan)
        self.assertIsNotNone(pad_source(self.manager,self.plan['id']))
        self.plan['outputs'][0]['sha256']='0'*64;save_plan(self.manager,self.plan)
        self.assertIsNone(pad_source(self.manager,self.plan['id']))
        row=self.begin();client=FakeCLI()
        with self.assertRaisesRegex(ValueError,'checksum differs'):execute(self.manager,row,client)
        self.assertEqual(client.uploads,[])
        self.archive.write_bytes(b'dummy')
        with self.assertRaisesRegex(ValueError,'differs from its prepared'):self.begin()

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
        with patch('app.mmf_release_upload.subprocess.Popen'):
            result=start(self.manager,{'preparation_id':'prepared'})
        return next(r for r in attempts(self.manager) if r['id']==result['attempt_id'])
    def test_retry_allowed_for_every_previous_outcome(self):
        for state in ('complete','failed','deleted','interrupted'):
            first=self.begin();first['state']=state;save(self.manager,first)
            second=self.begin();self.assertNotEqual(first['id'],second['id']);self.assertGreater(second['number'],first['number'])
            second['state']='complete';save(self.manager,second)
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
        with patch('app.mmf_release_upload.subprocess.Popen') as launch:
            with self.assertRaisesRegex(Exception,'collage'):start(self.manager,{'preparation_id':'prepared'})
            launch.assert_not_called()
    def test_changed_destination_blocks_before_sending(self):
        row=self.begin();client=FakeCLI();client.destinations=lambda:{'destinations':[]}
        with self.assertRaisesRegex(Exception,'unavailable'):execute(self.manager,row,client)
        self.assertEqual(client.uploads,[])
    def test_stale_attempt_is_interrupted_not_permanently_busy(self):
        row=self.begin();row['state']='uploading';save(self.manager,row);view=status(self.manager)
        self.assertFalse(view['active']);self.assertEqual(view['attempts'][0]['state'],'interrupted')

    def test_waiting_upload_can_be_cancelled_and_resumed(self):
        row=self.begin()
        view=status(self.manager)
        self.assertEqual(view['attempts'][0]['state'],'queued')
        self.assertEqual(view['attempts'][0]['queue_position'],1)
        with patch('app.mmf_release_upload.subprocess.Popen') as launch:
            start(self.manager,{'resume_queue':True});launch.assert_called_once()
        with self.assertRaisesRegex(FileExistsError,'already'):self.begin()
        start(self.manager,{'cancel_queued':row['id']})
        self.assertEqual(attempts(self.manager)[0]['state'],'cancelled')
        self.assertTrue(self.archive.exists())

    def test_workflow_prepares_before_upload_and_keeps_bot_choice(self):
        key='a'*64
        choice={'mode':'bot','destination':'@example_bot','profile':'eve','creator':'Example','month':'2026-09'}
        with patch('app.mmf_release_upload.subprocess.Popen'):
            result=start(self.manager,{'workflow':{'release_key':key,'keys':['A']},'title':'Example','delivery':choice})
        order=[]
        def package(*args,**kwargs):
            self.assertTrue(kwargs['synchronous']);order.append('prepare');return {'preparation_id':'prepared'}
        def upload(owner,row):
            order.append('upload');self.assertEqual(row['delivery'],choice)
            self.assertEqual(row['preparation_id'],'prepared');row['state']='complete';save(owner,row)
        with worker_lock(self.manager).open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            with patch.object(self.manager,'state',return_value={'items':[{'release_key':key,'key':'A','completed':True}]}),patch('app.mmf_release_prepare.start',side_effect=package),patch('app.mmf_release_upload.execute',side_effect=upload):
                drain(self.manager,lock.fileno())
        self.assertEqual(order,['prepare','upload'])

    def test_shared_queue_orders_providers_and_pauses_after_failure(self):
        from app.gumroad_manager import GumroadManager
        other=GumroadManager(self.manager.store)
        plans(other)
        save_plan(other,{**self.plan,'id':'other'})
        with worker_lock(self.manager).open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            with patch('app.mmf_release_upload.subprocess.Popen') as launch:
                first=start(self.manager,{'preparation_id':'prepared'})
                second=start(other,{'preparation_id':'other'})
                launch.assert_not_called()
            self.assertEqual(status(other)['attempts'][0]['queue_position'],2)
            calls=[]
            def failing(owner,row):
                calls.append(owner.provider);raise ValueError('Connection lost')
            with patch('app.mmf_release_upload.execute',side_effect=failing):drain(self.manager,lock.fileno())
            self.assertEqual(calls,['mmf'])
            self.assertEqual(attempts(self.manager)[0]['state'],'failed')
            self.assertEqual(attempts(other)[0]['state'],'queued')
            def success(owner,row):
                calls.append(owner.provider);row['state']='complete';save(owner,row)
            with patch('app.mmf_release_upload.execute',side_effect=success):drain(self.manager,lock.fileno())
            self.assertEqual(calls,['mmf','gumroad'])
            self.assertEqual(attempts(other)[0]['state'],'complete')

class BotCLI(FakeCLI):
    def __init__(self,confirm=True):super().__init__();self.confirm=confirm;self.completed=False;self.pad_messages=[];self.events=[]
    def incoming(self,message,buttons=None):
        m={'id':len(self.messages)+1,'raw':{'PeerID':{'UserID':456},'Out':False,'Message':message}}
        if buttons:m['raw']['ReplyMarkup']={'Rows':[{'Buttons':buttons}]}
        self.messages.append(m)
    def _run(self,args,*rest,**kwargs):
        import base64
        from datetime import datetime
        self.events.append(args[1])
        if args[1] in ('release-upload','release-history') and str(args[args.index('--chat')+1])=='-123':
            bot_messages=self.messages;self.messages=self.pad_messages
            try:return super()._run(args,*rest,**kwargs)
            finally:self.pad_messages=self.messages;self.messages=bot_messages
        if args[1]=='release-forward':
            source=next(m for m in self.pad_messages if m['id']==args[args.index('--message')+1])
            forwarded=json.loads(json.dumps(source));forwarded['id']=len(self.messages)+1;forwarded['raw']['PeerID']={'UserID':456}
            self.messages.append(forwarded)
            if forwarded['raw']['Media'].get('Document'):self.incoming('Your upload looks complete',[{'Text':'Complete','Data':'Y29tcGxldGU='}])
            return
        if args[1]=='release-bot':
            action=args[args.index('--action')+1];value=args[args.index('--value')+1]
            if action=='info':Path(args[args.index('--output')+1]).write_text(json.dumps({'id':456,'bot':True,'title':'Test bot'}))
            elif action=='text':self.incoming('Select creator',[{'Text':'Example'},{'Text':'🟥 This Month','Data':base64.b64encode(b'month').decode()}])
            elif base64.b64decode(value)==b'month':self.incoming('Upload a release for Example. The filename should start with: Example - '+datetime.now().strftime('%y-%m'))
            else:
                self.completed=True
                if self.confirm:self.incoming('Upload done and saved.')
            return
        if args[1]=='release-history':
            Path(args[args.index('--output')+1]).write_text(json.dumps({'id':456,'messages':self.messages}));return
        super()._run(args,*rest,**kwargs)
        self.messages[-1]['raw']['PeerID']={'UserID':456}
        if '--photo' not in args:self.incoming('Your upload looks complete',[{'Text':'Complete','Data':'Y29tcGxldGU='}])

class BotUploadTests(UploadTests):
    def test_failed_channel_upload_never_starts_bot(self):
        row=self.begin_bot();client=BotCLI();original=client._run
        def run(args,*rest,**kwargs):
            if args[1]=='release-upload':raise ValueError('channel unavailable')
            return original(args,*rest,**kwargs)
        client._run=run
        with self.assertRaisesRegex(ValueError,'channel unavailable'):execute(self.manager,row,client)
        self.assertNotIn('release-bot',client.events)
        self.assertNotIn('release-forward',client.events)

    def test_stale_complete_button_refreshes_before_retry(self):
        from app.mmf_release_flow import BotConversation
        client=BotCLI();client.incoming('Ready',[{'Text':'Complete','Data':'Y29tcGxldGU='}])
        original=client._run;callbacks=[]
        def run(args,*rest,**kwargs):
            if args[args.index('--action')+1]=='callback':
                callbacks.append(args[args.index('--message')+1])
                if len(callbacks)==1:
                    client.incoming('Ready now',[{'Text':'Complete','Data':'Y29tcGxldGU='}])
                    raise ValueError('rpc error code 400: MESSAGE_ID_INVALID')
            return original(args,*rest,**kwargs)
        client._run=run
        conversation=BotConversation(client,Path(self.temp.name),{'profile':'eve','destination':'@example_bot'},lambda label:client.messages,lambda *args:None)
        conversation.finish()
        self.assertEqual(callbacks,[1,2]);self.assertTrue(client.completed)

    def test_completion_retry_sends_no_files_or_start_command(self):
        row=self.begin_bot();client=BotCLI();original=client._run
        def fail_completion(args,*rest,**kwargs):
            if args[1]=='release-bot' and args[args.index('--value')+1]=='Y29tcGxldGU=':raise ValueError('temporary completion error')
            return original(args,*rest,**kwargs)
        client._run=fail_completion
        with self.assertRaisesRegex(ValueError,'temporary completion'):execute(self.manager,row,client)
        self.assertTrue(all(f['state']=='complete' for f in row['files']))
        self.assertTrue(next(r for r in status(self.manager)['attempts'] if r['id']==row['id'])['can_finish'])
        with patch('app.mmf_release_upload.subprocess.Popen'):
            start(self.manager,{'retry_completion':row['id']})
        row=next(r for r in attempts(self.manager) if r['id']==row['id']);client.uploads=[]
        def only_completion(args,*rest,**kwargs):
            self.assertNotIn(args[1],('release-upload','release-forward'))
            if args[1]=='release-bot':self.assertNotEqual(args[args.index('--action')+1],'text')
            return original(args,*rest,**kwargs)
        client._run=only_completion
        execute(self.manager,row,client)
        self.assertEqual(row['state'],'complete');self.assertEqual(client.uploads,[])

    def test_completion_retry_refuses_changed_conversation(self):
        row=self.begin_bot();client=BotCLI();execute(self.manager,row,client)
        row['state']='failed';row['completion_only']=True
        client.messages.append({'id':len(client.messages)+1,'raw':{'PeerID':{'UserID':456},'Out':True,'Message':'/start'}})
        client.completed=False;client.uploads=[]
        with self.assertRaisesRegex(ValueError,'conversation has changed'):execute(self.manager,row,client)
        self.assertFalse(client.completed);self.assertEqual(client.uploads,[])

    def test_existing_pad_release_is_forwarded_without_local_files(self):
        self.check_pad_forwarding(False)

    def test_forward_with_visible_attribution_stops_before_archive(self):
        self.check_pad_forwarding(True)

    def check_pad_forwarding(self,attributed):
        pad=self.begin();execute(self.manager,pad,FakeCLI())
        self.archive.unlink();self.collage.unlink()
        row=self.begin_bot();client=BotCLI();forwarded=[]
        original=client._run
        def run(args,*rest,**kwargs):
            if args[1]!='release-forward':return original(args,*rest,**kwargs)
            self.assertEqual(args[args.index('--from')+1],pad['destination'])
            message_id=args[args.index('--message')+1]
            index=next(n for n,m in enumerate(pad['messages']) if m['message_id']==message_id)
            forwarded.append(index)
            client.messages.append({'id':len(client.messages)+1,'file':pad['messages'][index]['filename'],
                'raw':{'PeerID':{'UserID':456},'Out':True,'Message':pad['title'],
                       'FwdFrom':{'Flags':0,'Imported':False,'SavedOut':False,'FromID':None,'FromName':'','Date':0,
                                  'ChannelPost':0,'PostAuthor':'','SavedFromPeer':None,'SavedFromMsgID':0,
                                  'SavedFromID':None,'SavedFromName':'','SavedDate':0,'PsaType':''},
                       'Media':{'Photo':{'ID':1}} if index==0 else {'Document':{'Size':pad['files'][index]['size']}}}})
            if attributed:client.messages[-1]['raw']['FwdFrom']['FromName']='Original sender'
            if index:client.incoming('Ready',[{'Text':'Complete','Data':'Y29tcGxldGU='}])
        client._run=run
        with patch('app.mmf_release_upload.shutil.copyfile',side_effect=AssertionError('No staging')):
            if attributed:
                with self.assertRaisesRegex(Exception,'verify the forwarded'):execute(self.manager,row,client)
                self.assertEqual(forwarded,[0]);self.assertFalse(client.completed)
                self.assertEqual(client.uploads,[])
                self.assertNotIn('publication',plans(self.manager)[0])
                return
            execute(self.manager,row,client)
        self.assertEqual(forwarded,[0,1]);self.assertEqual(client.uploads,[])
        self.assertEqual(row['source_upload_id'],pad['id'])
        self.assertEqual(row['state'],'complete');self.assertTrue(client.completed)
        self.assertEqual(plans(self.manager)[0]['publication']['method'],'bot_confirmation')

    def test_forward_failure_never_falls_back_to_upload_or_publication(self):
        pad=self.begin();execute(self.manager,pad,FakeCLI())
        row=self.begin_bot();client=BotCLI();original=client._run
        def run(args,*rest,**kwargs):
            if args[1]=='release-forward':raise ValueError('Source message unavailable')
            return original(args,*rest,**kwargs)
        client._run=run
        with self.assertRaisesRegex(ValueError,'Source message unavailable'):execute(self.manager,row,client)
        self.assertEqual(client.uploads,[]);self.assertFalse(client.completed)
        self.assertNotIn('publication',plans(self.manager)[0]);self.assertEqual(row['state'],'failed')

    def test_creator_spacing_and_case_use_bot_spelling_in_prompt(self):
        from app.mmf_release_flow import BotConversation
        from datetime import datetime
        month=datetime.now().strftime('%Y-%m')
        choice={'profile':'eve','creator':'ex AMPLE','month':month,'destination':'@example_bot'}
        client=BotCLI()
        conversation=BotConversation(client,Path(self.temp.name),choice,lambda label:client.messages,lambda *args:None)
        conversation.begin()
        self.assertEqual(choice['creator'],'Example')
        self.assertIn('Upload a release for Example',conversation.last['raw']['Message'])
        self.assertEqual(conversation.upload_filename('Release Addendum 2.7z.001'),'Example - '+month[2:]+' - Release Addendum 2.7z.001')

    def test_ambiguous_creator_rows_stop_before_callback(self):
        from app.mmf_release_flow import BotConversation
        from datetime import datetime
        from unittest.mock import Mock
        choice={'profile':'eve','creator':'Example','month':datetime.now().strftime('%Y-%m')}
        rows=[{'Buttons':[{'Text':name},{'Text':'This Month','Data':'bW9udGg='}]} for name in ('Example','EX AMPLE')]
        message={'id':1,'raw':{'ReplyMarkup':{'Rows':rows}}}
        conversation=BotConversation(None,None,choice,None,lambda *args:None)
        conversation.checkpoint=Mock();conversation.command=Mock()
        conversation.wait=lambda predicate,label:message if predicate(message) else None
        with self.assertRaisesRegex(ValueError,'ambiguous'):conversation.begin()
        conversation.command.assert_called_once_with('text','/start')

    def begin_bot(self):
        from datetime import datetime
        self.plan.update(keys=['A'],release_key='release');save_plan(self.manager,self.plan)
        choice={'mode':'bot','destination':'@example_bot','profile':'eve','creator':'Example','month':datetime.now().strftime('%Y-%m')}
        with patch('app.mmf_release_upload.subprocess.Popen'):
            result=start(self.manager,{'preparation_id':'prepared','delivery':choice})
        return next(r for r in attempts(self.manager) if r['id']==result['attempt_id'])
    def test_verified_bot_conversation_publishes_only_after_confirmation(self):
        row=self.begin_bot();client=BotCLI();execute(self.manager,row,client)
        self.assertTrue(client.completed);self.assertEqual(row['state'],'complete')
        self.assertEqual(plans(self.manager)[0]['publication']['method'],'bot_confirmation')
        self.assertEqual(row['destination'],'456')
        self.assertEqual(client.uploads,[self.collage.name,self.archive.name])
        self.assertEqual(client.events.count('release-forward'),2)
        self.assertLess(max(i for i,e in enumerate(client.events) if e=='release-upload'),client.events.index('release-bot'))
    def test_bot_complete_without_success_does_not_publish(self):
        row=self.begin_bot();client=BotCLI(False)
        ticks=iter([0,0,0,0,0,0,0,0,0,0,1000])
        with patch('app.mmf_release_flow.time.monotonic',side_effect=lambda:next(ticks,1000)),patch('app.mmf_release_flow.time.sleep'):
            with self.assertRaisesRegex(Exception,'expected reply'):execute(self.manager,row,client)
        self.assertNotIn('publication',plans(self.manager)[0]);self.assertEqual(row['state'],'failed')

if __name__=='__main__':unittest.main()
