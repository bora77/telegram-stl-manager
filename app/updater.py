"""Explicit, verified Windows updates from GitHub releases; no bundled credentials."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
import tarfile
import zipfile

ASSET='Telegram-STL-Manager-Update.zip'
DEFAULT_REPOSITORY='bora77/telegram-stl-manager'

def version(value):
    if not re.fullmatch(r'\d+\.\d+\.\d+',str(value)):raise ValueError('Invalid update version.')
    return tuple(map(int,value.split('.')))

class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith('https://'):raise ValueError('Unsafe download redirect.')
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if urllib.parse.urlsplit(newurl).netloc!='api.github.com':redirected.remove_header('Authorization')
        return redirected

class Updater:
    def __init__(self,root,busy=lambda:False):
        self.root=Path(root);self.directory=self.root/'data/updates';self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.busy=busy;self.gate=threading.RLock();self.working=False;self.reserved=(self.root/'data/update-installing').exists()
        self.state={'phase':'idle','message':'Check for application updates.'};self.release=None
    def settings(self):
        try:return json.loads((self.directory/'settings.json').read_text())
        except FileNotFoundError:return {'repository':DEFAULT_REPOSITORY}
    def save(self,payload):
        settings=self.settings();repo=payload.get('repository',settings['repository']).strip()
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo):raise ValueError('Enter a GitHub owner/repository.')
        settings['repository']=repo
        if 'token' in payload and payload['token']:settings['token']=payload['token'].strip()
        if payload.get('clear_token'):settings.pop('token',None)
        self.write('settings.json',settings);self.release=None;self.state={'phase':'idle','message':'Update source saved.'}
        return self.status()
    def write(self,name,data):
        path=self.directory/name;tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data));tmp.chmod(0o600);tmp.replace(path)
    def status(self):
        if self.reserved and not self.working and not (self.root/'data/update-installing').exists():self.reserved=False
        settings=self.settings()
        return {**self.state,'current':(self.root/'VERSION').read_text().strip(),'repository':settings['repository'],'has_token':bool(settings.get('token')),'working':self.working,'reserved':self.reserved,'supported':Path('/etc/telegram-stl-managed').exists(),'available':self.release is not None,'last_checked':self.state.get('last_checked',0)}
    def request(self,url,binary=False):
        if not url.startswith('https://api.github.com/'):raise ValueError('Invalid GitHub asset URL.')
        headers={'User-Agent':'Telegram-STL-Manager-Updater','Accept':'application/octet-stream' if binary else 'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10'}
        token=self.settings().get('token')
        if token:headers['Authorization']='Bearer '+token
        try:return urllib.request.build_opener(GitHubRedirect()).open(urllib.request.Request(url,headers=headers),timeout=30)
        except urllib.error.HTTPError as error:
            if error.code==404:raise ValueError('No published update found, or this account cannot access the update repository.') from None
            raise ValueError(f'GitHub update request failed (HTTP {error.code}).') from None
        except urllib.error.URLError:raise ValueError('Update server unavailable. Check your internet connection and try again.') from None
    def check(self):
        repo=self.settings()['repository']
        with self.request(f'https://api.github.com/repos/{repo}/releases/latest') as response:
            release=json.loads(response.read(2_000_001))
        latest=release['tag_name'].removeprefix('v');version(latest)
        self.release=None
        if version(latest)>version((self.root/'VERSION').read_text().strip()):
            asset=next((a for a in release.get('assets',[]) if a['name']==ASSET),None)
            if not asset or not re.fullmatch(r'sha256:[a-f0-9]{64}',asset.get('digest') or ''):raise ValueError('This release has no verified Windows update package.')
            if not str(asset['url']).startswith(f'https://api.github.com/repos/{repo}/releases/assets/'):raise ValueError('Unexpected update asset.')
            self.release={'version':latest,'url':asset['url'],'sha256':asset['digest'][7:],'size':asset['size']}
        self.state={'phase':'available' if self.release else 'current','message':f'Update {latest} available.' if self.release else 'You have the latest version.','last_checked':time.time()}
    def start(self,action):
        with self.gate:
            if self.working:raise FileExistsError('An update operation is already running.')
            if action=='install':
                if not self.status()['supported']:raise ValueError('In-app installation currently supports managed Windows/WSL installations. Use the documented runtime or Docker update procedure on other platforms.')
                if not self.release:raise ValueError('Check for an update first.')
                self.reserved=True
            self.working=True
            def work():
                try:
                    if action=='check':self.check()
                    else:self.install()
                except Exception as error:
                    self.state={'phase':'error','last_checked':time.time(),'message':str(error) if isinstance(error,(ValueError,OSError)) else 'Update failed. Existing installation was retained.'}
                    self.reserved=False
                    (self.root/'data/update-installing').unlink(missing_ok=True)
                finally:self.working=False
            threading.Thread(target=work,daemon=True).start()
            return self.status()
    def install(self):
        release=dict(self.release)
        self.state={'phase':'waiting','message':'Waiting for active tasks to finish. New tasks are paused.'}
        while self.busy():time.sleep(2)
        target=self.directory/'package.zip';partial=target.with_suffix('.partial');checksum=hashlib.sha256();received=0
        self.state={'phase':'downloading','message':'Downloading update…','bytes':0,'total':release['size']}
        try:
            with self.request(release['url'],binary=True) as response,partial.open('wb') as output:
                while block:=response.read(1024*1024):
                    received+=len(block)
                    if received>release['size']:raise ValueError('Update exceeds its published size.')
                    output.write(block);checksum.update(block);self.state['bytes']=received
                output.flush();os.fsync(output.fileno())
            if received!=release['size'] or checksum.hexdigest()!=release['sha256']:raise ValueError('Update checksum failed. Nothing was installed.')
            partial.replace(target)
        finally:partial.unlink(missing_ok=True)
        self.state={'phase':'installing','message':'Starting the visible Windows updater. This page reconnects after restart.'}
        # The Windows helper must live outside WSL before it stops the distribution.
        powershell='/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
        local=subprocess.check_output([powershell,'-NoProfile','-Command','[Environment]::GetFolderPath("LocalApplicationData")'],text=True,timeout=30).strip()
        windows_dir=local+'\\TelegramSTLManager\\pending-update'
        linux_dir=Path(subprocess.check_output(['wslpath','-u',windows_dir],text=True,timeout=15).strip());linux_dir.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(target) as archive:
            expected={'Update.cmd','READ-ME.txt','support/Update.ps1','support/update.sh','support/Start.ps1','support/telegram-stl-linux-x64.tar.gz','support/telegram-stl-linux-x64.tar.gz.sha256'}
            prefix='Telegram-STL-Manager-Update/'
            names={i.filename.removeprefix(prefix) for i in archive.infolist()}
            if names!=expected or any(not i.filename.startswith(prefix) for i in archive.infolist()):raise ValueError('Unexpected update package layout.')
            if sum(i.file_size for i in archive.infolist())>2_000_000_000:raise ValueError('Update package is too large.')
            for info in archive.infolist():
                relative=info.filename[len(prefix):];path=linux_dir/relative;path.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(info) as incoming,path.open('wb') as out:
                    import shutil
                    shutil.copyfileobj(incoming,out)
        with tarfile.open(linux_dir/'support/telegram-stl-linux-x64.tar.gz') as runtime:
            if runtime.extractfile('telegram-stl/VERSION').read(100).decode().strip()!=release['version']:raise ValueError('Package version does not match its GitHub release.')
        (self.root/'data/update-installing').touch(mode=0o600)
        script=windows_dir+'\\support\\Update.ps1'
        # Base64 transports the command without shell interpolation of paths.
        import base64
        command="Start-Process powershell.exe -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \""+script.replace("'","''")+"\" -Confirmed'"
        encoded=base64.b64encode(command.encode('utf-16-le')).decode()
        subprocess.run([powershell,'-NoProfile','-EncodedCommand',encoded],check=True,timeout=30)
