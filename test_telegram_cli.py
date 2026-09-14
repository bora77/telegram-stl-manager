from test_support import configure_source
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
import threading
import socket
import subprocess
import fcntl
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from telegram_cli import TelegramCLI, CLIError, ExportProgress, parse_export as parse_source_export, validate_servers
from functools import partial
from test_support import TEST_SOURCE
parse_export = partial(parse_source_export, scope=TEST_SOURCE)
from subscription_store import SubscriptionStore

TOPIC='https://t.me/c/123456789/200'


class ServiceProcessTests(unittest.TestCase):
    @unittest.skipUnless((Path(__file__).parent/'.tools/tdl-stl/tdl').is_file(), 'Build the CLI to test its local proxy')
    def test_built_cli_forwards_flags_without_opening_login_storage(self):
        binary=Path(__file__).resolve().parent/'.tools/tdl-stl/tdl'
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);configure_source(root);state=root/'state';state.mkdir()
            listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);listener.bind(str(state/'service.sock'));listener.listen();listener.settimeout(5)
            self.addCleanup(listener.close)
            commands=[['files','--topic','200','--after-id','123','--output',str(root/'metadata.json')],
                      ['download','--topic','200','--message','100000','--document-id','42','--dc','4','--size','10',
                       '--filename','Artist 2026-09.zip','--output',str(root/'payload'),'--events',str(root/'events'),
                       '--cache',str(root/'cache'),'--quick-test','--reuse-server','4/media/127.0.0.1/443','--min-speed-mbps','20','--probe-only']]
            def receive():
                with listener.accept()[0] as conn:
                    request=json.loads(conn.makefile('rb').readline())
                    conn.sendall(json.dumps({'ok':True,'protocol':1,'source':str(root/'data/source.json')}).encode()+b'\n')
                    return request
            for args in commands:
                with self.subTest(command=args[0]),ThreadPoolExecutor(1) as pool:
                    pending=pool.submit(receive)
                    result=subprocess.run([str(binary),'--storage','type=bolt,path='+str(state/'data'),
                                           'stl','--source',str(root/'data/source.json'),*args],cwd=root,capture_output=True,text=True,timeout=5)
                    self.assertEqual(result.returncode,0,result.stderr)
                    request=pending.result(timeout=5)
                    self.assertEqual(request['args'][0],args[0]);self.assertIn('--topic=200',request['args'])
                    if args[0]=='files':self.assertIn('--after-id=123',request['args'])
                    else:
                        self.assertIn('--filename=Artist 2026-09.zip',request['args'])
                        self.assertIn('--probe-only=true',request['args']);self.assertIn('--quick-test=true',request['args'])
                        self.assertIn('--reuse-server=4/media/127.0.0.1/443',request['args'])
                        self.assertIn('--min-speed-mbps=20',request['args'])
            self.assertFalse(list((state/'data').rglob('*')), 'Proxy unexpectedly opened a login database')

    def test_scan_and_download_overlap_and_stop_only_cancels_its_own_request(self):
        from telegram_service import TelegramService,ServiceError
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);state=root/'state';state.mkdir()
            listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);listener.bind(str(state/'service.sock'));listener.listen()
            self.addCleanup(listener.close)
            entered={name:threading.Event() for name in ('files','download')};cancelled=threading.Event();release=threading.Event()
            threads=[]
            def respond(conn):
                with conn:
                    request=json.loads(conn.makefile('rb').readline());kind=request['args'][0]
                    if kind in entered:entered[kind].set()
                    if kind=='files':conn.recv(1);cancelled.set()
                    if kind=='download':release.wait(5)
                    conn.sendall(json.dumps({'ok':kind!='files','error':'Cancelled' if kind=='files' else '',
                                            'protocol':1,'source':str(root/'data/source.json')}).encode()+b'\n')
            def accept():
                for _ in range(4):
                    conn,_=listener.accept();t=threading.Thread(target=respond,args=(conn,));threads.append(t);t.start()
            server=threading.Thread(target=accept);server.start()
            service=TelegramService(root,state,lambda *args: (_ for _ in ()).throw(AssertionError('must reuse service')))
            stop=threading.Event()
            with (state/'application.lock').open('a') as legacy,ThreadPoolExecutor(2) as pool:
                fcntl.flock(legacy,fcntl.LOCK_EX|fcntl.LOCK_NB)
                download=pool.submit(service.run,['stl','download'],lambda:False,lambda:None,5,lambda:None)
                scan=pool.submit(service.run,['stl','files'],stop.is_set,lambda:None,5,lambda:None)
                try:
                    self.assertTrue(entered['download'].wait(2));self.assertTrue(entered['files'].wait(2))
                    stop.set()
                    with self.assertRaisesRegex(ServiceError,'Stopped'):scan.result(timeout=3)
                    self.assertTrue(cancelled.is_set());self.assertFalse(download.done())
                finally:release.set()
                download.result(timeout=3)
            server.join(timeout=2)
            for t in threads:t.join(timeout=2)

    def test_service_source_mismatch_cannot_start_a_job(self):
        from telegram_service import TelegramService,ServiceError
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);service=TelegramService(root,root,lambda *args:None)
            for response in ({'ok':True,'protocol':1,'source':'/wrong/source.json'},
                             {'ok':True,'protocol':2,'source':service.source}):
                with self.assertRaises(ServiceError):service.validate(response)


def exported(name='Artist 2026-09.zip',size=10):
    return {'id':123456789,'messages':[{'id':100000,'file':name,'raw':{
        'ID':100000,'PeerID':{'ChannelID':123456789},
        'ReplyTo':{'ForumTopic':True,'ReplyToMsgID':200,'ReplyToTopID':0},
        'Media':{'Document':{'ID':42,'Size':size,'DCID':4,'Attributes':[{'FileName':name}]}}}}]}


class CLIParseTests(unittest.TestCase):
    def test_image_document_metadata_is_kept_for_organizer_but_identifiable_for_download_filter(self):
        from release_images import is_image_attachment
        for name,mime in [('Artist 2026-09.JPG','application/octet-stream'),('Artist 2026-09 preview','image/jpeg'),('Artist 2026-09.SVGZ','')]:
            with self.subTest(name=name):
                data=exported(name);data['messages'][0]['raw']['Media']['Document']['MimeType']=mime
                files=parse_export(data,TOPIC)
                self.assertEqual(len(files),1);self.assertTrue(is_image_attachment(files[0]))
        for name in ('Artist 2026-09.7z.001','Artist 2026-09.part1.rar','Artist 2026-09.stl','Artist 2026-09.pdf'):
            data=exported(name);before=parse_export(data,TOPIC)
            data['messages'][0]['raw']['Media']['Document']['MimeType']='application/octet-stream'
            self.assertEqual(parse_export(data,TOPIC),before)
            self.assertFalse(is_image_attachment(before[0]))

    def test_exact_names_sizes_document_identity_and_nested_replies(self):
        data=exported();data['messages'][0]['raw']['ReplyTo'].update(ReplyToMsgID=123,ReplyToTopID=200)
        item=parse_export(data,TOPIC)[0]
        self.assertEqual((item['filename'],item['bytes_total'],item['document_id']),('Artist 2026-09.zip',10,42))
        self.assertEqual(item['message_url'],TOPIC+'/100000')
        photo=copy.deepcopy(data['messages'][0]);photo['id']=100001;photo['raw']['ID']=100001;photo['raw']['Media']={'Photo':{}}
        data['messages'].append(photo)
        self.assertEqual(len(parse_export(data,TOPIC)),1)

    def test_wrong_chat_topic_missing_scope_or_duplicate_fails_closed(self):
        mutations=[lambda d:d.update(id=999),
                   lambda d:d['messages'][0]['raw'].update(PeerID={'ChannelID':999}),
                   lambda d:d['messages'][0]['raw']['ReplyTo'].update(ReplyToMsgID=777),
                   lambda d:d['messages'][0]['raw'].pop('ReplyTo'),
                   lambda d:d['messages'].append(copy.deepcopy(d['messages'][0])),
                   lambda d:d['messages'][0]['raw']['Media']['Document'].update(Size=-1)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                data=exported();mutate(data)
                with self.assertRaises(CLIError):parse_export(data,TOPIC)
        with self.assertRaises(CLIError):parse_export(exported(),'https://t.me/c/999/200')

    def test_unsafe_filename_is_explicit_review_item(self):
        self.assertTrue(parse_export(exported('../escape.zip'),TOPIC)[0]['unsafe_filename'])

    def test_manual_server_must_be_advertised_for_correct_dc(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'data').mkdir()
            key='4/media/149.154.167.255/443'
            (root/'data/telegram-servers.json').write_text(json.dumps({'endpoints':[{'key':key,'dc':4}]}))
            self.assertEqual(validate_servers({'4':key,'2':'auto'},root),{'4':key})
            for value in ({'2':key},{'4':'4/media/127.0.0.1/80'},[] ):
                with self.assertRaises(ValueError):validate_servers(value,root)

    def test_server_choice_round_trip_does_not_start_a_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store=SubscriptionStore(configure_source(root))
            key='4/media/149.154.167.255/443'
            store.atomic_write(root/'data/telegram-servers.json',{'endpoints':[{'key':key,'dc':4}]})
            with patch('run_manager.subprocess.Popen') as launch:
                saved=store.save_config({'revision':0,'download_directory':str(root),'download_servers':{'4':key,'2':'auto'}})
                self.assertEqual(store.config()['download_servers'],{'4':key})
                store.save_config({'revision':saved['revision'],'download_directory':str(root)})
                self.assertEqual(store.config()['download_servers'],{'4':key})
                launch.assert_not_called()


class ExportProgressTests(unittest.TestCase):
    def test_complete_messages_arrive_once_across_partial_unicode_and_json(self):
        data=exported('Example café 2026-09.zip')
        second=copy.deepcopy(data['messages'][0]);second['id']+=1;second['raw']['ID']+=1
        data['messages'].append(second)
        raw=json.dumps(data,ensure_ascii=False).encode()
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'export.json';reader=ExportProgress(TOPIC,TEST_SOURCE);found=[]
            self.assertEqual(reader.read(path),[])
            with path.open('wb') as stream:
                for byte in raw:
                    stream.write(bytes([byte]));stream.flush()
                    found.extend(reader.read(path))
            self.assertEqual(found,parse_export(data,TOPIC))
            self.assertEqual(reader.read(path),[])

    def test_stream_rejects_wrong_chat_topic_and_duplicates(self):
        for change in ('chat','topic','duplicate'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                data=exported()
                if change=='chat':data['id']=999
                if change=='topic':data['messages'][0]['raw']['ReplyTo']['ReplyToMsgID']=999
                if change=='duplicate':data['messages']*=2
                path=Path(temp)/'export.json';path.write_text(json.dumps(data))
                with self.assertRaises(CLIError):ExportProgress(TOPIC,TEST_SOURCE).read(path)

    def test_progress_does_not_replace_complete_scan_receipt(self):
        for complete in (False,True):
            with self.subTest(complete=complete),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);store=SubscriptionStore(configure_source(root))
                store.atomic_write(root/'data/creators.json',{'creators':[{'topic_url':TOPIC,'within_approved_group':True}]})
                client=TelegramCLI(root=root,state=root/'state',binary='/bin/true');received=[]
                def run(args,work,stopped,tick,timeout,**kwargs):
                    output=Path(args[args.index('--output')+1]);output.write_text(json.dumps(exported()))
                    tick()
                    if complete:Path(str(output)+'.complete').write_text(json.dumps({'complete':True,'topic':200}))
                try:
                    with patch.object(client,'_run',side_effect=run):
                        if complete:self.assertEqual(client.list_files(TOPIC,on_files=received.extend),parse_export(exported(),TOPIC))
                        else:
                            with self.assertRaisesRegex(CLIError,'complete topic scan'):client.list_files(TOPIC,on_files=received.extend)
                    self.assertEqual(received,parse_export(exported(),TOPIC))
                finally:client.close()


class IncrementalScanTests(unittest.TestCase):
    def client(self, root):
        store=SubscriptionStore(configure_source(root))
        store.atomic_write(root/'data/creators.json',{'creators':[{'topic_url':TOPIC,'within_approved_group':True}]})
        client=TelegramCLI(root=root,state=root/'state',binary='/bin/true');self.addCleanup(client.close)
        return client

    def scan(self, client, data, cursor, *, after=0, complete=True, stopped=False, incremental=True):
        def run(args,work,stop,tick,timeout,**kwargs):
            actual=args[args.index('--after-id')+1] if '--after-id' in args else 0
            self.assertEqual(actual,after)
            output=Path(args[args.index('--output')+1]);output.write_text(json.dumps(data));tick()
            Path(str(output)+'.cursor').write_text(json.dumps({'topic':200,'after':after,'last_id':cursor}))
            if complete:Path(str(output)+'.complete').write_text(json.dumps({'topic':200,'complete':True}))
        with patch.object(client,'_run',side_effect=run):
            return client.list_files(TOPIC,stopped=lambda:stopped,incremental=incremental)

    def test_only_new_messages_are_requested_and_cached_pending_files_remain(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);client=self.client(root)
            first=self.scan(client,exported(),100005) # Newest message can be text without an attachment.
            newer=exported('Artist 2026-10.zip');newer['messages'][0]['id']=100010;newer['messages'][0]['raw']['ID']=100010
            second=self.scan(client,newer,100011,after=100005)
            self.assertEqual(second,first+parse_export(newer,TOPIC))
            third=self.scan(client,{'id':123456789,'messages':[]},100011,after=100011)
            self.assertEqual(third,second);self.assertEqual(client.last_scan['new_files'],0)
            self.assertEqual(client.last_scan['mode'],'incremental')

    def test_failed_or_stopped_scan_never_advances_the_cursor(self):
        from topic_catalog import TopicCatalog
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);client=self.client(root);self.scan(client,exported(),100000)
            before=TopicCatalog(root,TEST_SOURCE).load(TOPIC)
            for failure in ('receipt','stop','scope','boundary'):
                data={'id':123456789,'messages':[]}
                if failure=='scope':data=exported();data['id']=999
                if failure=='boundary':data=exported() # Old item must not appear in a delta.
                with self.subTest(failure=failure),self.assertRaises(CLIError):
                    self.scan(client,data,100100,after=100000,complete=failure!='receipt',stopped=failure=='stop')
                self.assertEqual(TopicCatalog(root,TEST_SOURCE).load(TOPIC),before)

    def test_complete_preview_refreshes_old_metadata_and_seeds_incremental_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);client=self.client(root)
            self.scan(client,exported(),100000,incremental=False)
            changed=exported(size=20)
            self.scan(client,changed,100001,incremental=False)
            result=self.scan(client,{'id':123456789,'messages':[]},100001,after=100001)
            self.assertEqual(result[0]['bytes_total'],20)

    def test_stale_scan_cannot_roll_back_a_newer_committed_cursor(self):
        from topic_catalog import TopicCatalog
        with tempfile.TemporaryDirectory() as temporary:
            catalog=TopicCatalog(temporary,TEST_SOURCE);first=parse_export(exported(),TOPIC)
            catalog.commit(TOPIC,0,100000,first)
            catalog.commit(TOPIC,100000,100100,[])
            catalog.commit(TOPIC,0,100000,first)
            self.assertEqual(catalog.load(TOPIC)['last_id'],100100)


class CLIProcessTests(unittest.TestCase):
    def test_threshold_is_forwarded_only_for_automatic_real_transfers(self):
        for threshold,manual,probe in [(20,False,False),(0,False,False),(20,True,False),(20,False,True)]:
            with self.subTest(threshold=threshold,manual=manual,probe=probe),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);client=self.make_client(root,'')
                client.config={'server_speed_threshold_mbps':threshold,'download_servers':{'4':'chosen-server'} if manual else {}}
                def run(args,*a,**kw):
                    if threshold and not manual and not probe:self.assertEqual(args[args.index('--min-speed-mbps')+1],20)
                    else:self.assertNotIn('--min-speed-mbps',args)
                    raise CLIError('Test stops before transferring')
                with patch.object(client,'_run',side_effect=run),self.assertRaisesRegex(CLIError,'Test stops'):
                    client.download(parse_export(exported(),TOPIC)[0],lambda *a:None,lambda:False,probe_only=probe)

    def test_slowdown_notice_does_not_turn_off_active_download_monitoring(self):
        for kind in ('speed_retest_pending','speed_retest_queued'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);client=self.make_client(root,'');received=[];clock=time.monotonic()
                def run(args,work,stopped,tick,**kwargs):
                    output=Path(args[args.index('--output')+1]);events=Path(args[args.index('--events')+1])
                    events.write_text(json.dumps({'event':'download_start','total':10})+'\n')
                    with patch('telegram_cli.time.monotonic',return_value=clock):tick()
                    with events.open('a') as stream:stream.write(json.dumps({'event':kind,'threshold_bps':20e6,'low_seconds':120})+'\n')
                    with patch('telegram_cli.time.monotonic',return_value=clock+1):tick()
                    with patch('telegram_cli.time.monotonic',return_value=clock+201):tick() # Active limit is 300s, idle is 180s.
                    output.write_bytes(b'0123456789')
                    with events.open('a') as stream:stream.write(json.dumps({'event':'complete','bytes':10,'message_id':100000,'filename':'Artist 2026-09.zip','sha256':hashlib.sha256(b'0123456789').hexdigest()})+'\n')
                    with patch('telegram_cli.time.monotonic',return_value=clock+202):tick()
                with patch.object(client,'_run',side_effect=run):
                    result=client.download(parse_export(exported(),TOPIC)[0],lambda *a:None,lambda:False,status=received.append)
                self.assertEqual(result.read_bytes(),b'0123456789')
                self.assertIn(kind,[e['event'] for e in received])

    def test_mid_file_server_switch_preserves_progress_and_active_monitoring(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);client=self.make_client(root,'');received=[];progress=[];clock=time.monotonic()
            def run(args,work,stopped,tick,**kwargs):
                output=Path(args[args.index('--output')+1]);events=Path(args[args.index('--events')+1])
                for event in [{'event':'download_start','total':10}, {'event':'progress','bytes':4,'total':10},
                              {'event':'server_slow_retest','reason':'below_half_threshold'},
                              {'event':'server_testing','index':1,'count':2}, {'event':'server_selected','endpoint':'new'},
                              {'event':'download_resumed','bytes':6,'total':10,'endpoint':'new'}]:
                    with events.open('a') as stream:stream.write(json.dumps(event)+'\n')
                    with patch('telegram_cli.time.monotonic',return_value=clock):tick()
                # Resume must restore the active-transfer timeout after server events.
                with patch('telegram_cli.time.monotonic',return_value=clock+201):tick()
                output.write_bytes(b'0123456789')
                with events.open('a') as stream:
                    for event in [{'event':'progress','bytes':10,'total':10}, {'event':'complete','bytes':10,'message_id':100000,'filename':'Artist 2026-09.zip','sha256':hashlib.sha256(b'0123456789').hexdigest()}]:
                        stream.write(json.dumps(event)+'\n')
                with patch('telegram_cli.time.monotonic',return_value=clock+202):tick()
            with patch.object(client,'_run',side_effect=run):
                result=client.download(parse_export(exported(),TOPIC)[0],lambda done,total:progress.append(done),lambda:False,status=received.append)
            self.assertEqual(result.read_bytes(),b'0123456789')
            self.assertEqual(progress,[0,4,6,10,10])
            self.assertIn('download_resumed',[event['event'] for event in received])

    def test_invalid_mid_file_resume_progress_is_rejected(self):
        for done,total in [(3,10),(-1,10),(11,10),(True,10),(6,11),('6',10)]:
            with self.subTest(done=done,total=total),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);client=self.make_client(root,'')
                def run(args,work,stopped,tick,**kwargs):
                    events=Path(args[args.index('--events')+1])
                    events.write_text(''.join(json.dumps(event)+'\n' for event in [
                        {'event':'download_start','total':10}, {'event':'progress','bytes':4,'total':10},
                        {'event':'download_resumed','bytes':done,'total':total}]))
                    tick()
                with patch.object(client,'_run',side_effect=run),self.assertRaisesRegex(CLIError,'Invalid CLI resume progress'):
                    client.download(parse_export(exported(),TOPIC)[0],lambda *a:None,lambda:False)

    def test_release_server_comparison_is_once_per_month_artist_and_dc(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);client=self.make_client(root,'');client.config={'server_check_frequency':'release'};calls=[]
            def run(args,work,stopped,tick,**kwargs):
                calls.append(args)
                def arg(key):return args[args.index(key)+1]
                size=arg('--size');output=Path(arg('--output'))
                with output.open('wb') as stream:stream.truncate(size)
                events=[{'event':'server_selected','endpoint':'4/media/127.0.0.1/443'},
                        {'event':'complete','bytes':size,'message_id':arg('--message'),'filename':arg('--filename'),'sha256':'0'*64}]
                Path(arg('--events')).write_text(''.join(json.dumps(e)+'\n' for e in events));tick()
            with patch.object(client,'_run',side_effect=run),patch('telegram_cli.network_identity',return_value=('test',False)):
                for message,month,dc,size in [(1,'09',4,10),(2,'09',4,64*1024**2),(3,'09',4,10),
                                              (4,'10',4,64*1024**2),(5,'10',5,64*1024**2)]:
                    item=parse_export(exported('Artist 2026-'+month+' part '+str(message)+'.rar',size),TOPIC)[0]
                    item.update(source_message_id=message,message_url=TOPIC+'/'+str(message),dc_id=dc)
                    client.download(item,lambda *a:None,lambda:False)
            self.assertNotIn('--retest',calls[0]) # Tiny sample defers comparison.
            for index in (1,3,4):
                self.assertIn('--retest',calls[index]);self.assertIn('--quick-test',calls[index])
                self.assertNotIn('--reuse-server',calls[index])
            self.assertNotIn('--retest',calls[2]);self.assertIn('--reuse-server',calls[2])
            self.assertEqual(calls[2][calls[2].index('--reuse-server')+1],'4/media/127.0.0.1/443')

    def test_probe_never_changes_existing_staged_payload_or_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);client=self.make_client(root,"events.write_text(json.dumps({'event':'probe_complete'})+'\\n')\n")
            item=parse_export(exported(),TOPIC)[0];staged=root/'state/transfers/100000';staged.mkdir(parents=True)
            original={'payload.part':b'original bytes','complete.json':b'original receipt','events.jsonl':b'original events','item.json':b'original identity'}
            for name,data in original.items():(staged/name).write_bytes(data)
            client.download(item,lambda *a:None,lambda:False,probe_only=True,retest=True,quick_test=True)
            self.assertEqual({p.name:p.read_bytes() for p in staged.iterdir()},original)
            self.assertFalse(list((root/'state').glob('server-probe-*')))

    def test_manual_server_choice_does_not_trigger_release_comparison(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);client=self.make_client(root,'');client.config={'download_servers':{'4':'chosen-server'},'server_check_frequency':'release'}
            item=parse_export(exported(size=64*1024**2),TOPIC)[0]
            def run(args,*a,**kw):
                self.assertEqual(args[args.index('--server')+1],'chosen-server')
                self.assertNotIn('--retest',args);self.assertNotIn('--quick-test',args);self.assertNotIn('--reuse-server',args)
                raise CLIError('Test stops before transferring')
            with patch.object(client,'_run',side_effect=run),self.assertRaisesRegex(CLIError,'Test stops'):
                client.download(item,lambda *a:None,lambda:False)

    def test_default_reuses_recent_comparisons_without_forcing_a_new_test(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);client=self.make_client(root,'')
            item=parse_export(exported(size=64*1024**2),TOPIC)[0]
            def run(args,*a,**kw):
                self.assertNotIn('--retest',args);self.assertNotIn('--quick-test',args);self.assertNotIn('--reuse-server',args)
                raise CLIError('Test stops before transferring')
            with patch.object(client,'_run',side_effect=run),self.assertRaisesRegex(CLIError,'Test stops'):
                client.download(item,lambda *a:None,lambda:False)

    def test_clients_share_login_between_commands_and_waiting_can_be_cancelled(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);configure_source(root)
            first=TelegramCLI(root=root,state=root/'state',binary='/bin/true')
            second=TelegramCLI(root=root,state=root/'state',binary='/bin/true')
            self.addCleanup(first.close);self.addCleanup(second.close)
            entered=threading.Event();release=threading.Event();waiting=threading.Event()
            run=first._run_locked
            one=root/'one';one.mkdir();two=root/'two';two.mkdir()
            def held(*args):
                entered.set()
                if not release.wait(5):raise RuntimeError('CLI test timed out')
                run(*args)
            with patch.object(first,'_run_locked',side_effect=held),ThreadPoolExecutor(2) as pool:
                a=pool.submit(first._run,[],one,lambda:False)
                try:
                    self.assertTrue(entered.wait(2))
                    b=pool.submit(second._run,[],two,lambda:False,waiting=waiting.set)
                    self.assertTrue(waiting.wait(2));self.assertIsNone(second.child)
                finally:release.set()
                a.result(timeout=3);b.result(timeout=3)
            self.assertEqual(second.child.returncode,0)
            self.assertFalse(first.lock.closed) # Idle clients hold no CLI lock.
            cancelled=threading.Event();previous=second.child
            fcntl.flock(first.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            try:
                with self.assertRaisesRegex(CLIError,'Stopped while waiting'):
                    second._run([],two,cancelled.is_set,waiting=cancelled.set)
                self.assertIs(second.child,previous)
            finally:fcntl.flock(first.lock,fcntl.LOCK_UN)
            second._run([],two,lambda:False)
            self.assertEqual(second.child.returncode,0)

    def make_client(self,root,behavior):
        store=SubscriptionStore(configure_source(root))
        store.atomic_write(root/'data/creators.json',{'creators':[{'topic_url':TOPIC,'within_approved_group':True}]})
        binary=root/'fake-cli'
        binary.write_text('#!/usr/bin/env python3\n'+
            'import sys,json,time,hashlib\nfrom pathlib import Path\n'+
            "def arg(key):return sys.argv[sys.argv.index(key)+1]\n"+
            "events=Path(arg('--events'));output=Path(arg('--output'))\n"+
            behavior)
        binary.chmod(0o700)
        client=TelegramCLI(root=root,state=root/'state',binary=binary)
        self.addCleanup(client.close)
        return client

    def test_exact_progress_and_completed_file_reused_without_new_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            client=self.make_client(root,"""
with events.open('w') as e:
 def emit(value):e.write(json.dumps(value)+'\\n');e.flush()
 emit({'event':'download_start','total':10})
 output.write_bytes(b'0123456789')
 emit({'event':'progress','bytes':4,'total':10})
 time.sleep(.3)
 emit({'event':'complete','bytes':10,'total':10,'message_id':100000,'filename':'Artist 2026-09.zip','sha256':hashlib.sha256(b'0123456789').hexdigest()})
""")
            item=parse_export(exported(),TOPIC)[0];samples=[]
            source=client.download(item,lambda done,total:samples.append((done,total)),lambda:False)
            self.assertIn((4,10),samples);self.assertEqual(samples[-1],(10,10))
            self.assertEqual(source.read_bytes(),b'0123456789')
            with patch.object(client,'_run',side_effect=AssertionError('must reuse verified completion')), \
                 patch('telegram_cli.shutil.disk_usage',return_value=type('Usage',(),{'free':0})()):
                self.assertEqual(client.download(item,lambda *a:None,lambda:False),source)

    def test_time_waiting_for_another_command_does_not_count_as_stalled_download(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            client=self.make_client(root,"""
events.write_text('')
time.sleep(.3)
output.write_bytes(b'0123456789')
events.write_text(json.dumps({'event':'complete','bytes':10,'message_id':100000,'filename':'Artist 2026-09.zip','sha256':hashlib.sha256(b'0123456789').hexdigest()})+'\\n')
""")
            run=client._run;later=time.monotonic()+1000
            def delayed(args,work,stopped,tick,timeout,waiting):
                with patch('telegram_cli.time.monotonic',return_value=later):
                    waiting()
                    return run(args,work,stopped,tick,timeout,waiting)
            with patch.object(client,'_run',side_effect=delayed):
                source=client.download(parse_export(exported(),TOPIC)[0],lambda *args:None,lambda:False)
            self.assertEqual(source.read_bytes(),b'0123456789')

    def test_full_apparent_size_without_completion_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);client=self.make_client(root,"output.write_bytes(b'0'*10)\nevents.write_text('')\n")
            with self.assertRaisesRegex(CLIError,'complete file'):
                client.download(parse_export(exported(),TOPIC)[0],lambda *a:None,lambda:False)

    def test_cancel_terminates_process_and_does_not_record_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);client=self.make_client(root,"events.write_text('')\noutput.write_bytes(b'partial')\ntime.sleep(30)\n")
            started=time.monotonic()
            with self.assertRaisesRegex(CLIError,'Stopped'):
                client.download(parse_export(exported(),TOPIC)[0],lambda *a:None,lambda:time.monotonic()-started>.3)
            self.assertLess(time.monotonic()-started,7)
            self.assertIsNotNone(client.child.poll())
            self.assertFalse((root/'state/transfers/100000/complete.json').exists())


if __name__=='__main__':unittest.main()
