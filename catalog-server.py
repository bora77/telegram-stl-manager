#!/usr/bin/env python3
"""Serve only the private catalog preview and its evidence on localhost."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import json
from urllib.parse import unquote, urlsplit, parse_qs
from subscription_store import SubscriptionStore
from folder_organizer import Organizer
from organizer_apply import start_application
from telegram_cli import TelegramCLI, CLIError, server_view
from collages import CollageStore
from file_delivery import DeliveryError
import fcntl

ROOT = Path(__file__).resolve().parent
STORE = SubscriptionStore(ROOT)

class Handler(SimpleHTTPRequestHandler):
    store = STORE
    root = ROOT
    @property
    def collages(self):
        if not hasattr(self.store,'collages'):self.store.collages=CollageStore(self.store)
        return self.store.collages
    def valid_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.root), **kwargs)

    def send_head(self):
        if not self.valid_host():
            self.send_error(403)
            return None
        path = unquote(urlsplit(self.path).path)
        if path == "/":
            self.path = "/catalog.html"
        elif path in ('/config','/config/'):
            self.path='/configuration.html'
        elif path in ('/organize','/organize/'):
            self.path='/organizer.html'
        elif path in ('/collages','/collages/'):
            self.path='/collages.html'
        elif path not in ("/catalog.html", "/favicon.svg", "/data/creators.json", "/data/creators.csv") and not re.fullmatch(r"/data/toc-links/page-\d{3}\.png", path):
            self.send_error(404)
            return None
        return super().send_head()

    def reply_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.valid_host():
            self.send_error(403);return
        path=urlsplit(self.path).path
        if path.startswith('/api/collages/'):
            try:
                query=parse_qs(urlsplit(self.path).query)
                if path=='/api/collages/months':self.reply_json({'months':self.collages.months(query.get('folder',[''])[0])});return
                if path=='/api/collages/export':self.reply_json(self.collages.export_status(query.get('id',[''])[0]));return
                parts=path.split('/')
                if len(parts)==5 and parts[3] in ('thumbnail','image','preview','result'):
                    filename=None
                    if parts[3]=='preview':
                        self.collages.preview_data(parts[4]);data=(self.collages.cache/(parts[4]+'-preview.jpg')).read_bytes()
                    elif parts[3]=='result':data,filename=self.collages.result(parts[4])
                    else:data=self.collages.thumbnail(parts[4],large=parts[3]=='image')
                    self.send_response(200);self.send_header('Content-Type','image/jpeg');self.send_header('Content-Length',str(len(data)))
                    if filename:self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
                    self.end_headers();self.wfile.write(data);return
                self.reply_json({'error':'Unknown collage request.'},404)
            except (ValueError,OSError,DeliveryError) as error:self.reply_json({'error':str(error)},400)
            return
        if path == '/api/organizer':
            self.reply_json(Organizer(self.store).status());return
        if path in ('/api/subscriptions','/api/queue','/api/config'):
            try:
                self.reply_json(self.store.config_view() if path.endswith('config') else self.store.read() if path.endswith('subscriptions') else self.store.queue())
            except (OSError,ValueError):
                self.reply_json({'error':'Saved settings could not be read.'},500)
            return
        super().do_GET()

    def do_POST(self):
        host=self.headers.get('Host')
        if not self.valid_host() or self.headers.get('Origin') != 'http://'+host:
            self.reply_json({'error':'This action must come from the local catalog.'},403);return
        if self.path not in ('/api/subscriptions','/api/config','/api/run','/api/run/resume','/api/stop','/api/organizer/preview','/api/organizer/stop','/api/organizer/apply','/api/organizer/resume','/api/organizer/repair','/api/servers/refresh','/api/servers/retest','/api/collages/open','/api/collages/selection','/api/collages/layout','/api/collages/preview','/api/collages/export'):
            self.reply_json({'error':'Unknown action.'},404);return
        if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
            self.reply_json({'error':'Expected JSON.'},415);return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 1_000_000:raise ValueError('Invalid request size.')
            payload=json.loads(self.rfile.read(length))
            if not isinstance(payload,dict):raise ValueError('Expected an object.')
            if self.path.startswith('/api/collages/'):
                action=self.path.rsplit('/',1)[1]
                if action=='open':result=self.collages.index(payload.get('folder'),payload.get('month'))
                elif action=='selection':result=self.collages.save_selection(payload)
                elif action=='layout':result=self.collages.layout(payload)
                elif action=='preview':result=self.collages.preview(payload)
                else:result=self.collages.start_export(payload)
                self.reply_json(result);return
            if self.path.startswith('/api/servers/'):
                with (self.root/'data/operation.lock').open('a') as operation:
                    fcntl.flock(operation,fcntl.LOCK_EX)
                    queue=self.store.queue()
                    if queue['active'] or queue['organizer_active']:raise FileExistsError('Wait for the current Telegram task to finish.')
                    if self.path.endswith('/retest'):
                        self.store.atomic_write(self.root/'data/telegram-server-speeds.json',{})
                    else:
                        client=TelegramCLI(root=self.root)
                        try:client.refresh_servers()
                        finally:client.close()
                    self.reply_json(server_view(self.root));return
            if self.path.startswith('/api/organizer/'):
                organizer=Organizer(self.store)
                if self.path.endswith(('/apply','/resume','/repair')):
                    self.reply_json(start_application(self.store,payload,resume=self.path.endswith(('/resume','/repair')),repair=self.path.endswith('/repair')));return
                self.reply_json(organizer.start(payload) if self.path.endswith('/preview') else organizer.stop());return
            result=(self.store.runs.start(payload) if self.path=='/api/run' else self.store.runs.resume(payload) if self.path=='/api/run/resume' else self.store.runs.stop() if self.path=='/api/stop' else self.store.save_config(payload) if self.path.endswith('config') else self.store.save(payload))
            self.reply_json(result)
        except FileExistsError as error:
            self.reply_json({'error':str(error)},409)
        except (ValueError,TypeError,CLIError,DeliveryError) as error:
            self.reply_json({'error':str(error)},400)
        except OSError:
            message='Could not read or save collage files. Check the download folder and reopen the release.' if self.path.startswith('/api/collages/') else 'Could not save subscriptions. Existing settings were preserved.'
            self.reply_json({'error':message},500)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 6093), Handler).serve_forever()
