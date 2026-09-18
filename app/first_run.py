"""Browser-based initial account, source and destination setup; no shell commands."""
import contextlib,fcntl,json,os,pty,re,select,subprocess,sys,threading,time
from pathlib import Path
from app.source_scope import SourceScope, load_source
from app.telegram_cli import CLI_STATE

ANSI=re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)')

def parse_toc(data,scope):
    if data.get('id')!=scope.chat_id:raise ValueError('Table of Contents belongs to a different source.')
    rows={}
    for message in data.get('messages',[]):
        raw=message.get('raw',{})
        reply=raw.get('ReplyTo') or {}
        parent=reply.get('ReplyToTopID') or reply.get('ReplyToMsgID')
        if raw.get('ID')!=scope.toc_message_id and not (reply.get('ForumTopic') and parent==scope.toc_message_id):continue
        if raw.get('PeerID')!={'ChannelID':scope.chat_id}:raise ValueError('Unverified Table of Contents source.')
        text=raw.get('Message','');encoded=text.encode('utf-16-le')
        for entity in raw.get('Entities',[]):
            url=entity.get('URL');start=entity.get('Offset');length=entity.get('Length')
            if not url or type(start)!=int or type(length)!=int or start<0 or length<=0:continue
            name=encoded[start*2:(start+length)*2].decode('utf-16-le').strip()
            if scope.topic_id(url) and name and url!=scope.home_url:rows[url]={'name':name,'topic_url':url,'within_approved_group':True}
    if not rows:raise ValueError('No creator topic links were found in that TOC. Check the message link.')
    return {'source':{'name':'Configured Table of Contents','url':scope.home_url},'creators':sorted(rows.values(),key=lambda x:x['name'].casefold())}

def parse_share_path(value):
    """Accept pasted UNC folders, including Explorer's trailing separator."""
    if not isinstance(value,str):raise ValueError('Enter a Windows network path.')
    unc=value.strip().rstrip('\\')
    parts=unc[2:].split('\\')
    if not unc.startswith('\\\\') or len(parts)<2 or any(p in ('','.','..') or '/' in p or any(ord(c)<32 for c in p) for p in parts):
        raise ValueError('Enter a Windows network path such as \\\\server\\share\\Incoming.')
    return parts

class ShareResolutionRequired(ValueError):
    pass

class FirstRun:
    def __init__(self,store):
        self.store=store;self.root=store.root;self.lock=threading.RLock();self.child=None;self.master=None;self.phase='connected' if (self.root/'data/telegram-connected').exists() else 'idle';self.challenge=0;self.answer_pending=False;self.guard=None
        self.setup_required=not (self.root/'data/setup-complete').exists() and ((self.root/'data/setup-required').exists() or not self.configured_catalog())
    def configured_catalog(self):
        try:
            load_source(self.root)
            return bool(json.loads((self.root/'data/creators.json').read_text()).get('creators'))
        except (OSError,ValueError):return False
    def missing_setup(self):
        if not self.setup_required:return []
        missing=[]
        if self.phase!='connected':missing.append('Connect your Telegram account.')
        if not self.configured_catalog():missing.append('Import artists from your Table of Contents.')
        settings=self.store.config()
        destination=Path(settings['download_directory'])
        try:available=destination.is_dir() and os.access(destination,os.R_OK|os.W_OK|os.X_OK)
        except OSError:available=False
        if not available:missing.append('Choose an available download folder with read and write access.')
        if not (self.root/'data/setup-settings-reviewed').exists():missing.append('Review all configuration settings and press Save configuration.')
        if settings.get('mmf_beta_enabled') and not (self.root/'data/mmf/session.cookies').is_file():missing.append('Connect MyMiniFactory, or turn off its Beta feature and save.')
        return missing
    def complete(self,mmf=None):
        with self.lock,self.idle():
            missing=self.missing_setup()
            if missing:raise ValueError(' '.join(missing))
            if self.store.config().get('mmf_beta_enabled') and mmf is not None and mmf.get('auth_required',False):raise ValueError('Reconnect MyMiniFactory before finishing setup.')
            from app.file_delivery import mount_identity
            mount_identity(self.store.config()['download_directory'])
            (self.root/'data/setup-complete').touch(mode=0o600)
            self.setup_required=False
        return self.state()
    def command(self,*args):
        return [str(self.root/'.tools/tdl-stl/tdl'),'--storage','type=bolt,path='+str(CLI_STATE/'data'),'-n','telegram-stl-trial',*args]
    @contextlib.contextmanager
    def idle(self):
        with contextlib.ExitStack() as stack:
            for name in ('operation.lock','worker.lock','organizer-worker.lock','mmf/worker.lock'):
                path=self.root/'data'/name;path.parent.mkdir(parents=True,exist_ok=True)
                f=stack.enter_context(path.open('a'))
                try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:raise ValueError('Wait for running tasks to finish before changing setup.')
            yield
    def state(self):
        try:source=load_source(self.root).home_url
        except ValueError:source=''
        return {'setup_required':self.setup_required,'missing_setup':self.missing_setup(),'application':'telegram-stl-manager','version':(self.root/'VERSION').read_text().strip() if (self.root/'VERSION').exists() else 'unknown','source':source,'login':self.phase,'challenge':self.challenge,'share_supported':Path('/etc/telegram-stl-managed').exists(),'share':json.loads((self.root/'data/setup-share.json').read_text()) if (self.root/'data/setup-share.json').exists() else {},'creators':len(json.loads((self.root/'data/creators.json').read_text()).get('creators',[]))}
    def start_login(self):
        with self.lock,self.idle():
            if self.child and self.child.poll() is None:raise ValueError('Telegram sign-in is already open.')
            if (CLI_STATE/'service.sock').exists():raise ValueError('Stop and reopen the manager before changing an existing Telegram account.')
            CLI_STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
            guard=(CLI_STATE/'application.lock').open('a')
            try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:guard.close();raise ValueError('Telegram is busy.')
            self.guard=guard;self.master,slave=pty.openpty()
            try:self.child=subprocess.Popen(self.command('login','-T','code'),stdin=slave,stdout=slave,stderr=slave,start_new_session=True,env={**os.environ,'TERM':'xterm','NO_COLOR':'1'})
            finally:os.close(slave)
            self.phase='connecting';self.answer_pending=False
            threading.Thread(target=self._read_login,daemon=True).start()
            return self.state()
    def _read_login(self):
        buffer='';deadline=time.monotonic()+300
        try:
            while self.child.poll() is None and time.monotonic()<deadline:
                if not select.select([self.master],[],[],.2)[0]:continue
                try:chunk=os.read(self.master,8192).decode('utf-8','replace')
                except OSError:break
                # Survey requests the cursor position while rendering its input.
                if '\x1b[6n' in chunk:os.write(self.master,b'\x1b[1;1R')
                buffer=(buffer+ANSI.sub('',chunk))[-16000:]
                matches=list(re.finditer(r'Enter your phone number:|Enter 2FA Password:|Enter Code:',buffer))
                with self.lock:
                    if matches:
                        prompt=matches[-1].group();phase='password' if 'Password' in prompt else 'code' if 'Code' in prompt else 'phone'
                        if not self.answer_pending or phase!=self.phase:self.phase=phase;self.challenge+=1;self.answer_pending=True
                        buffer=buffer[matches[-1].end():]
            if self.child.poll() is None:self.child.terminate()
            result=self.child.wait(timeout=10)
            self.phase='connected' if result==0 else 'failed'
            if result==0:(self.root/'data/telegram-connected').touch(mode=0o600)
        except Exception:
            self.phase='failed'
            if self.child and self.child.poll() is None:self.child.kill();self.child.wait()
        finally:
            if self.master is not None:os.close(self.master);self.master=None
            if self.guard:self.guard.close();self.guard=None
            self.answer_pending=False
    def answer(self,payload):
        with self.lock:
            value=payload.get('value')
            if not self.answer_pending or payload.get('challenge')!=self.challenge:raise ValueError('The sign-in prompt changed. Please try again.')
            if not isinstance(value,str) or not value or len(value)>4096 or any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError('Enter a valid sign-in response.')
            os.write(self.master,value.encode()+b'\r');self.answer_pending=False
            return self.state()
    def configure_source(self,payload):
        match=re.fullmatch(r'https://t\.me/c/([1-9]\d*)/([1-9]\d*)',payload.get('url','').strip())
        if not match:raise ValueError('Paste the private Telegram Table of Contents message link.')
        scope=SourceScope(*map(int,match.groups()))
        with self.lock,self.idle():
            if self.child and self.child.poll() is None:raise ValueError('Finish Telegram sign-in first.')
            try:previous=load_source(self.root)
            except ValueError:previous=None
            if previous and previous!=scope:raise ValueError('This installation already uses a different source. Use a separate installation to keep histories apart.')
            if (CLI_STATE/'service.sock').exists():raise ValueError('Restart the manager before importing the Table of Contents.')
            CLI_STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
            with (CLI_STATE/'application.lock').open('a') as guard:
                fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
                output=self.root/'data/setup-toc.json'
                result=subprocess.run(self.command('chat','export','-c',str(scope.chat_id),'--topic',str(scope.toc_message_id),'-T','id','-i',f'{scope.toc_message_id},2147483646','--raw','--all','-o',str(output)),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120)
                if result.returncode:raise ValueError('TOC could not be read. Check your Telegram login, membership and link.')
                catalog=parse_toc(json.loads(output.read_text()),scope)
                self.store.atomic_write(self.root/'data/source.json',{'chat_id':scope.chat_id,'toc_message_id':scope.toc_message_id})
                self.store.atomic_write(self.root/'data/creators.json',catalog)
                subprocess.run([sys.executable,str(self.root/'tools/build-catalog.py')],check=True,stdout=subprocess.DEVNULL)
        return self.state()
    def share(self,payload):
        if not Path('/etc/telegram-stl-managed').exists():raise ValueError('Network-share setup is available in the Windows installation package.')
        parts=parse_share_path(payload.get('path',''))
        with self.idle():
            request={'action':'connect','server':parts[0],'share':parts[1],**{k:payload.get(k,'') for k in ('username','password','domain')}}
            result=subprocess.run(['sudo','-n','/usr/bin/python3','/usr/local/lib/telegram-stl/share-helper.py'],input=json.dumps(request),text=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=70)
            reply=json.loads(result.stdout or '{}')
            if result.returncode:
                error=ShareResolutionRequired if reply.get('code')=='nas_resolution_required' else ValueError
                raise error(reply.get('error','Share connection failed.'))
            destination=Path(reply['mount']).joinpath(*parts[2:])
            settings=self.store.config();settings['download_directory']=str(destination);settings['download_storage']='network'
            self.store.save_config(settings)
            self.store.atomic_write(self.root/'data/setup-share.json',{'path':'\\\\'+'\\'.join(parts),'username':payload.get('username',''),'domain':payload.get('domain','')})
            return {'destination':str(destination)}
