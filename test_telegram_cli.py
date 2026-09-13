from test_support import configure_source
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from telegram_cli import TelegramCLI, CLIError, ExportProgress, parse_export as parse_source_export, validate_servers
from functools import partial
from test_support import TEST_SOURCE
parse_export = partial(parse_source_export, scope=TEST_SOURCE)
from subscription_store import SubscriptionStore

TOPIC='https://t.me/c/123456789/200'


def exported(name='Artist 2026-09.zip',size=10):
    return {'id':123456789,'messages':[{'id':100000,'file':name,'raw':{
        'ID':100000,'PeerID':{'ChannelID':123456789},
        'ReplyTo':{'ForumTopic':True,'ReplyToMsgID':200,'ReplyToTopID':0},
        'Media':{'Document':{'ID':42,'Size':size,'DCID':4,'Attributes':[{'FileName':name}]}}}}]}


class CLIParseTests(unittest.TestCase):
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
                def run(args,work,stopped,tick,timeout):
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


class CLIProcessTests(unittest.TestCase):
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
            with patch.object(client,'_run',side_effect=AssertionError('must reuse verified completion')):
                self.assertEqual(client.download(item,lambda *a:None,lambda:False),source)

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
