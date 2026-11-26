import errno
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import Mock,patch
from app.network_recovery import retry_network,RecoveryStopped,record_response
from app.mmf_client import MMFClient,NetworkFailure,LoginRequired
from app.gumroad_client import GumroadClient
from app.telegram_cli import TelegramCLI,CLIError
from tests.test_mmf import Response


class NetworkRecoveryTests(unittest.TestCase):
    def test_real_http_disconnect_resumes_only_remaining_bytes(self):
        body=b'archive bytes '*10000;requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                offset=int(self.headers.get('Range','bytes=0-')[6:].split('-')[0]);requests.append((offset,self.headers.get('If-Range')))
                self.send_response(206 if offset else 200);self.send_header('Content-Type','application/octet-stream');self.send_header('ETag','"version-one"')
                self.send_header('Content-Length',str(len(body)-offset))
                if offset:self.send_header('Content-Range',f'bytes {offset}-{len(body)-1}/{len(body)}')
                self.end_headers()
                if len(requests)==1:
                    self.wfile.write(body[:17000]);self.wfile.flush();self.close_connection=True
                    self.connection.shutdown(socket.SHUT_RDWR);self.connection.close()
                else:self.wfile.write(body[offset:])
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as d,patch('app.mmf_client.BASE','http://127.0.0.1:'+str(server.server_port)),patch('app.network_recovery.wait_retry') as wait:
                client=MMFClient(Path(d)/'session');target=Path(d)/'archive.zip';item={'object_id':1,'archive_id':2,'size':len(body),'updated_at':'v1'}
                retry_network(lambda:client.download(item,target,lambda *args:None,lambda:False))
                self.assertEqual(target.read_bytes(),body);self.assertEqual(requests,[(0,None),(17000,'"version-one"')]);wait.assert_called_once()
        finally:server.shutdown();server.server_close();thread.join()

    def test_gumroad_disconnect_refreshes_link_and_resumes_without_cookie(self):
        class Dropped(Response):
            def __init__(self):super().__init__(b'abc',headers={'ETag':'"v1"'});self.calls=0
            def read(self,*args):
                self.calls+=1
                if self.calls>1:raise ConnectionResetError('network changed')
                return super().read(*args)
        with tempfile.TemporaryDirectory() as d:
            client=GumroadClient(Path(d)/'session');path=Path(d)/'archive.zip';opener=Mock()
            opener.open.side_effect=[Dropped(),Response(b'def',206,{'Content-Range':'bytes 3-5/6','ETag':'"v1"'})]
            with patch.object(client,'downloadables',return_value={'archives':[{'id':'f','url':'/r/token/product_files'}]}),patch.object(client,'metadata',return_value={'files':[{'url':'https://files.gumroad.com/file'}]}) as metadata,patch('urllib.request.build_opener',return_value=opener),patch('app.network_recovery.wait_retry'):
                retry_network(lambda:client.download({'object_id':'p','archive_id':'f','size':6},path,lambda *args:None,lambda:False))
            self.assertEqual(path.read_bytes(),b'abcdef');self.assertEqual(metadata.call_count,2)
            request=opener.open.call_args.args[0];self.assertEqual(request.get_header('Range'),'bytes=3-');self.assertNotIn('Cookie',request.headers)

    def test_changed_etag_is_never_appended_to_old_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'part';path.write_bytes(b'abc');item={'object_id':1,'archive_id':2,'size':6}
            record_response(path,item,0,Response(b'',headers={'ETag':'"old"'}))
            client=MMFClient(Path(d)/'session')
            with patch.object(client,'open',return_value=Response(b'def',206,{'Content-Range':'bytes 3-5/6','ETag':'"new"'})):
                with self.assertRaisesRegex(ValueError,'changed during resume'):client.download(item,path,lambda *args:None,lambda:False)
            self.assertEqual(path.read_bytes(),b'abc')

    def test_retries_bounded_and_auth_or_storage_errors_not_retried(self):
        operation=Mock(side_effect=NetworkFailure('Offline'))
        with patch('app.network_recovery.wait_retry'):
            with self.assertRaises(NetworkFailure):retry_network(operation)
        self.assertEqual(operation.call_count,5)
        for error in (LoginRequired('Login required'),OSError(errno.ENOSPC,'Disk full'),ValueError('Bad range')):
            operation=Mock(side_effect=error)
            with self.assertRaises(type(error)):retry_network(operation)
            operation.assert_called_once()

    def test_stop_cancels_reconnection_and_retry_after_is_respected(self):
        with self.assertRaises(RecoveryStopped):retry_network(lambda:None,lambda:True)
        operation=Mock(side_effect=NetworkFailure('Rate limited',120))
        with patch('app.network_recovery.wait_retry') as wait:
            with self.assertRaises(NetworkFailure):retry_network(operation)
            wait.assert_not_called();operation.assert_called_once()

    def test_telegram_download_reconnects_but_does_not_retry_integrity_errors(self):
        client=object.__new__(TelegramCLI);client._download=Mock(side_effect=[CLIError('Telegram service disconnected; staged data retained.'),Path('/tmp/retained-file')])
        events=[]
        with patch('app.network_recovery.wait_retry',side_effect=lambda delay,stopped,notify,attempt:notify(attempt,4,0)):
            self.assertEqual(client.download({},lambda *a:None,lambda:False,events.append),Path('/tmp/retained-file'))
        self.assertEqual(client._download.call_count,2);self.assertEqual(events[0]['event'],'network_reconnecting')
        client._download=Mock(side_effect=CLIError('LOCATION_INVALID'))
        with self.assertRaises(CLIError):client.download({},lambda *a:None,lambda:False)
        client._download.assert_called_once()

    def test_uncertain_telegram_write_is_not_replayed(self):
        from app.telegram_service import TelegramService,ServiceError
        with tempfile.TemporaryDirectory() as d:
            service=TelegramService(Path(d),Path(d),lambda *a:None);left,right=socket.socketpair();received=[]
            def peer():
                received.append(right.recv(65536));right.close()
            thread=threading.Thread(target=peer);thread.start()
            with patch.object(service,'ensure'),patch.object(service,'connect',return_value=left):
                with self.assertRaises(ServiceError):service.run(['stl','release-upload'],lambda:False,lambda:None,3,lambda:None)
            thread.join();self.assertEqual(len(received),1)
            self.assertEqual(json.loads(received[0])['args'],['release-upload'])

    def test_nas_cleanup_failure_keeps_original_error_and_local_source(self):
        from app.file_delivery import deliver
        import os
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'source';source.write_bytes(b'content');base=root/'nas';base.mkdir();original_unlink=os.unlink
            def unavailable(path,*args,**kwargs):
                if str(path).endswith('.partial'):raise OSError(errno.EIO,'Share disconnected')
                return original_unlink(path,*args,**kwargs)
            def dropped(*args):raise OSError(errno.ENETDOWN,'Network changed')
            with patch('app.file_delivery.os.unlink',side_effect=unavailable):
                with self.assertRaises(OSError) as error:deliver(source,base,'Artist','2026-11','archive.zip',progress=dropped)
            self.assertEqual(error.exception.errno,errno.ENETDOWN);self.assertEqual(source.read_bytes(),b'content')
            self.assertFalse((base/'Artist/2026-11/archive.zip').exists())
            result=deliver(source,base,'Artist','2026-11','archive.zip')
            self.assertEqual(result[0].read_bytes(),b'content')
