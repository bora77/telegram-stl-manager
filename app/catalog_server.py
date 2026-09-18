#!/usr/bin/env python3
"""Serve only the private catalog preview and its evidence on localhost."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import json
import io
from urllib.parse import unquote, urlsplit, parse_qs
from app.subscription_store import SubscriptionStore
from app.folder_organizer import Organizer
from app.organizer_apply import start_application
from app.telegram_cli import TelegramCLI, CLIError, server_view
from app.collages import CollageStore
from app.artist_profiles import ArtistProfiles
from app.file_delivery import DeliveryError
import fcntl
import subprocess
import os

ROOT = Path(__file__).resolve().parent.parent
STORE = SubscriptionStore(ROOT)
PROFILES = ArtistProfiles(ROOT)
from app.mmf_manager import MMFManager
MMF = MMFManager(STORE)
from app.first_run import FirstRun
SETUP = FirstRun(STORE)
from app.availability import Availability
AVAILABILITY = Availability(STORE)

from app.updater import Updater

def update_busy():
    if AVAILABILITY.running or (SETUP.child and SETUP.child.poll() is None):return True
    queue=STORE.queue()
    if queue.get('active') or queue.get('organizer_active') or MMF.busy():return True
    for name in ('worker.lock','organizer-worker.lock','mmf/worker.lock','mmf/upload.lock','collages/worker.lock'):
        path=ROOT/'data'/name
        if path.exists():
            with path.open('a') as lock:
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:return True
    return False

UPDATER=Updater(ROOT,update_busy)

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
        if SETUP.setup_required and path not in ('/config','/config/','/configuration.html','/favicon.svg'):
            self.send_response(303);self.send_header('Location','/config#setup-checklist');self.end_headers();return None
        if path == "/":
            self.path = "/catalog.html"
        elif path in ('/config','/config/'):
            self.path='/configuration.html'
        elif path in ('/organize','/organize/'):
            self.path='/organizer.html'
        elif path in ('/mmf','/mmf/'):
            self.path='/mmf.html'
        elif path in ('/collages','/collages/'):
            self.path='/collages.html'
        elif path in ('/whats-new','/whats-new/'):
            self.path='/whats-new.html'
        elif path not in ("/catalog.html", "/favicon.svg", "/data/creators.json", "/data/creators.csv") and not re.fullmatch(r"/data/toc-links/page-\d{3}\.png", path):
            self.send_error(404)
            return None
        served_path=urlsplit(self.path).path
        if served_path.endswith('.html'):
            try:
                content=(self.root/served_path.lstrip('/')).read_text()
                enabled=self.store.config().get('mmf_beta_enabled') is True
                if enabled:content=content.replace('id="mmf-nav-group" hidden','id="mmf-nav-group"')
            except OSError:
                self.send_error(404);return None
            body=content.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            return io.BytesIO(body)
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
        if path=='/api/updates':
            self.reply_json(UPDATER.status());return
        if SETUP.setup_required and path.startswith('/api/') and path not in ('/api/setup','/api/config','/api/mmf/status'):
            self.reply_json({'error':'Complete initial setup in Configuration first.','setup_required':True},403);return
        if path=='/api/setup':
            self.reply_json(SETUP.state());return
        if path in ('/api/artist-profiles','/api/artist-profiles/image'):
            try:
                name=parse_qs(urlsplit(self.path).query).get('name',[''])[0]
                profiles=PROFILES
                if path.endswith('/image'):
                    data,mime=profiles.image(name)
                    self.send_response(200);self.send_header('Content-Type',mime)
                    self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                else:self.reply_json(profiles.info(name))
            except (ValueError,OSError) as error:self.reply_json({'error':str(error)},400)
            return
        if path in ('/api/mmf','/api/mmf/status'):
            result=MMF.state()
            if path.endswith('/status'):
                result={k:v for k,v in result.items() if k not in ('items','folders','creators')}
            self.reply_json(result);return
        if path=='/api/availability':
            self.reply_json(AVAILABILITY.status());return
        if path=='/api/tasks':
            try:self.reply_json(self.store.task_status())
            except (OSError,ValueError):self.reply_json({'error':'Task status is temporarily unavailable.'},503)
            return
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
            self.reply_json(Organizer(self.store).display_status());return
        if path in ('/api/subscriptions','/api/queue','/api/config'):
            try:
                self.reply_json(self.store.config_view() if path.endswith('config') else self.store.read() if path.endswith('subscriptions') else self.store.queue())
            except (OSError,ValueError):
                self.reply_json({'error':'Saved settings could not be read.'},500)
            return
        super().do_GET()

    def do_POST(self):
        with UPDATER.gate:
            if UPDATER.reserved and self.path not in ('/api/stop','/api/mmf/stop','/api/organizer/stop'):
                self.reply_json({'error':'An update is pending. Wait for it to finish before starting new tasks.'},409);return
            self.handle_post()

    def handle_post(self):
        host=self.headers.get('Host')
        if not self.valid_host() or self.headers.get('Origin') != 'http://'+host:
            self.reply_json({'error':'This action must come from the local catalog.'},403);return
        if SETUP.setup_required and not self.path.startswith('/api/updates/') and self.path not in ('/api/setup/complete','/api/setup/login','/api/setup/answer','/api/setup/source','/api/setup/share','/api/config','/api/mmf/login','/api/release-pad/destinations','/api/servers/refresh','/api/servers/retest'):
            self.reply_json({'error':'Complete initial setup in Configuration first.','setup_required':True},403);return
        if self.path not in ('/api/updates/check','/api/updates/install','/api/updates/settings','/api/release-pad/destinations','/api/artist-profiles','/api/setup/complete','/api/setup/login','/api/setup/answer','/api/setup/source','/api/setup/share','/api/mmf/login','/api/mmf/save','/api/mmf/check','/api/mmf/download','/api/mmf/redownload','/api/mmf/resume','/api/mmf/stop','/api/mmf/prepare','/api/mmf/images','/api/mmf/package','/api/mmf/upload','/api/mmf/released','/api/availability/check','/api/subscriptions','/api/config','/api/run','/api/run/resume','/api/run/ignore','/api/stop','/api/organizer/preview','/api/organizer/stop','/api/organizer/apply','/api/organizer/resume','/api/organizer/repair','/api/servers/refresh','/api/servers/retest','/api/collages/open','/api/collages/selection','/api/collages/layout','/api/collages/preview','/api/collages/export'):
            self.reply_json({'error':'Unknown action.'},404);return
        if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
            self.reply_json({'error':'Expected JSON.'},415);return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0 < length <= (7_000_000 if self.path=='/api/artist-profiles' else 1_000_000):raise ValueError('Invalid request size.')
            payload=json.loads(self.rfile.read(length))
            if not isinstance(payload,dict):raise ValueError('Expected an object.')
            if self.path.startswith('/api/updates/'):
                action=self.path.rsplit('/',1)[1]
                self.reply_json(UPDATER.save(payload) if action=='settings' else UPDATER.start(action));return
            if self.path=='/api/release-pad/destinations':
                client=TelegramCLI(root=self.root)
                try:
                    result=client.destinations()
                    self.store.atomic_write(self.root/'data/release-pad-destinations.json',result)
                    self.reply_json(result)
                finally:client.close()
                return
            if self.path=='/api/artist-profiles':
                self.reply_json(PROFILES.save(payload));return
            if self.path.startswith('/api/setup/'):
                action=self.path.rsplit('/',1)[1]
                result=SETUP.complete(MMF) if action=='complete' else SETUP.start_login() if action=='login' else SETUP.answer(payload) if action=='answer' else SETUP.configure_source(payload) if action=='source' else SETUP.share(payload)
                self.reply_json(result);return
            if self.path=='/api/mmf/released':
                from app.mmf_release_prepare import confirm_released
                self.reply_json(confirm_released(MMF,payload));return
            if self.path=='/api/mmf/upload':
                from app.mmf_release_upload import start
                self.reply_json(start(MMF,payload));return
            if self.path in ('/api/mmf/prepare','/api/mmf/images','/api/mmf/package'):
                from app.mmf_release_prepare import start, start_images
                self.reply_json(start_images(MMF,payload,from_mmf=self.path.endswith('/images')) if self.path.endswith(('/prepare','/images')) else start(MMF,payload));return
            if self.path.startswith('/api/mmf/'):
                action=self.path.rsplit('/',1)[1]
                result=(MMF.login(payload) if action=='login' else MMF.save(payload) if action=='save' else MMF.stop() if action=='stop' else MMF.start(action,due=payload.get('due') is True,release=payload.get('release_key')))
                self.reply_json(result);return
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
            if self.path=='/api/availability/check':
                self.reply_json(AVAILABILITY.start(force=payload.get("force") is True));return
            if self.path.startswith('/api/organizer'):
                organizer=Organizer(self.store)
                if self.path.endswith(('/apply','/resume','/repair')):
                    self.reply_json(start_application(self.store,payload,resume=self.path.endswith(('/resume','/repair')),repair=self.path.endswith('/repair')));return
                self.reply_json(organizer.start(payload) if self.path.endswith('/preview') else organizer.stop());return
            result=(self.store.runs.start(payload) if self.path=='/api/run' else self.store.runs.resume(payload) if self.path=='/api/run/resume' else self.store.runs.ignore_corrupt(payload) if self.path=='/api/run/ignore' else self.store.runs.stop() if self.path=='/api/stop' else self.store.save_config(payload) if self.path.endswith('config') else self.store.save(payload))
            if self.path=='/api/config' and SETUP.setup_required:(self.root/'data/setup-settings-reviewed').touch(mode=0o600)
            self.reply_json(result)
        except subprocess.TimeoutExpired:
            self.reply_json({'error':'The connection timed out. Check network access and try again.'},504)
        except FileExistsError as error:
            self.reply_json({'error':str(error)},409)
        except (ValueError,TypeError,CLIError,DeliveryError) as error:
            self.reply_json({'error':str(error)},400)
        except OSError:
            message='Could not read or save collage files. Check the download folder and reopen the release.' if self.path.startswith('/api/collages/') else 'Could not save subscriptions. Existing settings were preserved.'
            self.reply_json({'error':message},500)

    def end_headers(self):
        self.send_header("Cache-Control", "private, max-age=300" if urlsplit(self.path).path=='/api/artist-profiles/image' else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

if __name__ == "__main__":
    bind = os.environ.get("TELEGRAM_STL_BIND", "127.0.0.1")
    if bind not in ("127.0.0.1", "0.0.0.0"):
        raise ValueError("Unsupported web server bind address.")
    ThreadingHTTPServer((bind, int(os.environ.get("TELEGRAM_STL_PORT","6093"))), Handler).serve_forever()
