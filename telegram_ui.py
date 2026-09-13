"""Telegram Desktop controls confined to the existing isolated display and profile."""
import csv
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from difflib import SequenceMatcher
from file_size import display_size,matches_size
from source_scope import load_source

STATE=Path.home()/'.local/share/telegram-stl-desktop'
ENV=dict(os.environ,DISPLAY=':92',XAUTHORITY=str(STATE/'Xauthority'),OMP_THREAD_LIMIT='1')
ROOT=Path(__file__).resolve().parent
class UIError(RuntimeError):pass

def attachment_name_matches(actual, observed):
    """Tolerate the OCR spelling .72 for .7z without relaxing numeric identity."""
    def normalized(name):
        return re.sub(r'\.72(?=\.|$)', '.7z', name.strip(), flags=re.I).casefold()
    actual,observed=normalized(actual),normalized(observed)
    return (re.findall(r'\d+',actual)==re.findall(r'\d+',observed)
            and SequenceMatcher(None,actual,observed).ratio()>.8)

def attachment_link(link):
    # Telegram adds ?single when a document inside an album was opened directly.
    # It changes presentation, not the topic or attachment message identity.
    if not isinstance(link,str) or not re.fullmatch(re.escape(load_source().prefix)+r'\d+/\d+(?:\?single)?',link):
        raise UIError('Invalid attachment link or outside approved group.')
    return link.removesuffix('?single')

class TelegramUI:
    def __init__(self):
        os.environ.update(DISPLAY=':92',XAUTHORITY=str(STATE/'Xauthority'))
        import gi
        gi.require_version('Gdk','3.0')
        from gi.repository import Gdk
        self.Gdk=Gdk
        self.window=Gdk.get_default_root_window()
        dimensions=(self.window.get_width(),self.window.get_height()) if self.window else None
        if dimensions not in ((1280,900),(2560,1800)):raise UIError('The isolated Telegram display must be running at 2560 × 1800 with 200% interface scale.')
        self.scale=2 if dimensions==(2560,1800) else 1
        self.temp=tempfile.TemporaryDirectory(prefix='stl-ui-')
        self.clip=subprocess.Popen(['python3',str(ROOT/'clipboard-marker.py')],env=ENV,stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    def close(self):
        self.clip.terminate();self.clip.wait(timeout=5);self.temp.cleanup()
    def xdo(self,*args):
        args=list(args)
        for i,arg in enumerate(args):
            if arg=='mousemove':args[i+1:i+3]=[round(float(value)*self.scale) for value in args[i+1:i+3]]
        return subprocess.check_output(['xdotool',*map(str,args)],env=ENV,text=True,timeout=10).strip()
    def guard(self):
        # Qt popup menus own input focus while Openbox reports no active window.
        # Prefer the actual input recipient; never fall back past another app.
        for command in ('getwindowfocus','getactivewindow'):
            try:
                window=self.xdo(command)
                props=subprocess.check_output(['xprop','-id',window,'WM_CLASS'],env=ENV,text=True,stderr=subprocess.DEVNULL,timeout=5)
            except (subprocess.CalledProcessError,subprocess.TimeoutExpired):continue
            classes=re.findall(r'"([^"]+)"',props)
            if classes:
                if any(name.casefold() in ('telegram','telegramdesktop') for name in classes):return
                raise UIError('Another app has focus on the isolated desktop; no click was sent.')
        raise UIError('Telegram focus could not be verified on the isolated desktop. Check that its window is open.')
    def click(self,x,y,button=1):
        self.guard()
        self.xdo('mousemove',x,y,'click',button);time.sleep(.25)
    def key(self,*keys):
        self.guard()
        self.xdo('key','--clearmodifiers',*keys);time.sleep(.2)
    def lines(self,box=(503,25,777,830),psm='11'):
        from PIL import Image
        x,y,w,h=box
        path=Path(self.temp.name)/'screen.png'
        self.Gdk.pixbuf_get_from_window(self.window,x*self.scale,y*self.scale,w*self.scale,h*self.scale).savev(str(path),'png',[],[])
        with Image.open(path) as im:im.convert('L').resize((w*3,h*3),Image.Resampling.LANCZOS).point(lambda p:0 if p<235 else 255).save(path)
        raw=subprocess.check_output(['tesseract',str(path),'stdout','--psm',psm,'tsv'],env=ENV,stderr=subprocess.DEVNULL,text=True,timeout=20)
        groups={}
        for r in csv.DictReader(io.StringIO(raw),delimiter='\t',quoting=csv.QUOTE_NONE):
            if r['level']=='5' and r['text'].strip():groups.setdefault((r['block_num'],r['par_num'],r['line_num']),[]).append(r)
        result=[]
        for words in groups.values():
            left=min(int(r['left']) for r in words);top=min(int(r['top']) for r in words)
            right=max(int(r['left'])+int(r['width']) for r in words);bottom=max(int(r['top'])+int(r['height']) for r in words)
            result.append({'text':' '.join(r['text'] for r in words),'x':x+(left+right)//6,'y':y+(top+bottom)//6,'left':x+left/3,'top':y+top/3})
        return sorted(result,key=lambda r:(r['y'],r['x']))
    def choose(self,text,box=(444,60,600,790),exact=False):
        for line in self.lines(box):
            if (line['text']==text if exact else re.sub(r'\W','',text.casefold()) in re.sub(r'\W','',line['text'].casefold())):
                self.click(line['x'],line['y']);return line
        self.key('Escape');raise UIError('Telegram control not found: '+text)
    def copy(self,x,y,label):
        self.clip.stdin.write('telegram-stl-pending\n');self.clip.stdin.flush()
        if self.clip.stdout.readline().strip()!='ready':raise UIError('Clipboard guard unavailable.')
        self.click(x,y,3);self.choose(label)
        self.clip.stdin.write('get\n');self.clip.stdin.flush()
        result=json.loads(self.clip.stdout.readline())
        if not result or result=='telegram-stl-pending':raise UIError('Telegram did not copy '+label)
        return result
    def reset_view(self):
        # Dismiss a native context menu and its enclosing Files/info dialog.
        # Use the approved TOC as home so the reset stays inside the allowed group.
        self.key('Escape');self.key('Escape')
        self._open_url(load_source().home_url)
        header=''.join(re.sub(r'\W','',row['text'].casefold()) for row in self.lines((503,25,550,54)))
        if 'tableofcontentstoc' not in header:raise UIError('Could not reset Telegram to the approved Table of Contents. No attachment was opened.')
    def open_topic(self,url):
        self.validate_url(url)
        self.reset_view()
        if url!=load_source().home_url:self._open_url(url)
    @staticmethod
    def validate_url(url):
        if not isinstance(url,str) or not re.fullmatch(re.escape(load_source().prefix)+r'\d+(?:/\d+)?',url):raise UIError('Outside approved group.')
    def _open_url(self,url):
        self.validate_url(url)
        instance=None
        for line in subprocess.check_output(['flatpak','ps','--columns=instance,application,pid'],text=True).splitlines():
            parts=line.split()
            if len(parts)==3 and parts[1]=='org.telegram.desktop':
                try:cmd=Path('/proc/'+parts[2]+'/cmdline').read_bytes()
                except OSError:continue
                if str(STATE/'profile').encode() in cmd:instance=parts[0]
        if not instance:raise UIError('The isolated Telegram instance is not running.')
        subprocess.run(['flatpak','enter',instance,'env','DISPLAY=:92','XAUTHORITY='+str(STATE/'Xauthority'),'QT_QPA_PLATFORM=xcb','LANG=C.UTF-8','/app/bin/Telegram','-workdir',str(STATE/'profile'),'--',url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=True)
        time.sleep(1)
    def open_files(self,topic):
        self.open_topic(topic)
        self.click(679,41)
        lines=self.lines((444,60,392,740))
        if not any(str(load_source().chat_id)+'/'+topic.split('/')[-1] in re.sub(r'\s+','',l['text']) for l in lines):
            self.key('Escape');raise UIError('Topic identity could not be verified in Telegram.')
        file_line=next((l for l in lines if re.fullmatch(r'[\d,]+ files?',l['text'])),None)
        if not file_line:self.key('Escape');return 0
        count=int(file_line['text'].split()[0].replace(',',''))
        self.click(file_line['x'],file_line['y']);return count
    def file_rows(self):
        lines=self.lines((538,164,288,690))
        rows=[]
        for i,line in enumerate(lines):
            if re.search(r'\s[KMGT]?B$',line['text'],re.I) and i:
                prev=lines[i-1]
                if 0<line['y']-prev['y']<38 and prev['y']>180:
                    rows.append(dict(prev,size_text=line['text'],date_text=lines[i+1]['text'] if i+1<len(lines) and lines[i+1]['y']-line['y']<40 else ''))
        return rows
    def scroll_files(self,steps=5):
        self.xdo('mousemove',800,700,'click','--repeat',steps,'--delay',65,5);time.sleep(.5)
    def restore_files_page(self,page):
        # Discrete wheel events preserve the same scroll distance. Batch them
        # without the per-page OCR settling delay; callers verify the final rows.
        remaining=page*5
        while remaining:
            steps=min(100,remaining)
            self.guard()
            self.xdo('mousemove',800,700,'click','--repeat',steps,'--delay',10,5)
            remaining-=steps
        time.sleep(.6)
    def resolve_row(self,row,topic):
        self.click(row['x'],row['y'],3);self.choose('Go To Message');time.sleep(.6)
        # The jumped-to message is highlighted. Match the visible filename, then
        # require its copied link to belong to this exact topic before transfer.
        rows=self.lines()
        candidates=[r for r in rows if attachment_name_matches(r['text'],row['text'])]
        if len(candidates)!=1:raise UIError(f"Attachment cannot be uniquely identified after Go To Message: {row['text']} ({len(candidates)} candidates).")
        pos=candidates[0]
        filename=self.copy(pos['x'],pos['y'],'Copy Filename')
        if not attachment_name_matches(filename,row['text']):raise UIError('Copied filename does not match the selected attachment.')
        link=attachment_link(self.copy(pos['x'],pos['y'],'Copy Message Link'))
        if not re.fullmatch(re.escape(topic)+r'/\d+',link):raise UIError('Attachment link is outside the selected topic.')
        return {'filename':filename,'message_url':link,'source_message_id':int(link.split('/')[-1]),'size_text':row['size_text'],'position':pos}
    def download_arrow(self,x,y):
        pixels=self.Gdk.pixbuf_get_from_window(self.window,(x-20)*self.scale,(y-20)*self.scale,41*self.scale,50*self.scale)
        if self.scale!=1:pixels=pixels.scale_simple(41,50,2)
        data=pixels.get_pixels();stride=pixels.get_rowstride();channels=pixels.get_n_channels()
        def white(px,py):
            if not (0<=px<41 and 0<=py<50):return False
            return min(data[py*stride+px*channels:py*stride+px*channels+3])>220
        for cx in (19,20,21):
            for tip in range(18,41):
                stem=sum(white(cx,tip-d) for d in range(12))
                left=any(white(cx-6+dx,tip-6+dy) for dx in (-1,0,1) for dy in (-1,0,1))
                right=any(white(cx+6+dx,tip-6+dy) for dx in (-1,0,1) for dy in (-1,0,1))
                if stem>=10 and left and right and not white(cx,tip+4) and not white(cx-5,tip-10) and not white(cx+5,tip-10):return True
        return False
    def document_size(self,pos):
        lines=self.lines((623,max(164,int(pos.get('top',pos['y']-6))+15),160,34),psm='6')
        for line in lines:
            expected=display_size(line['text'])
            if expected:return expected
        return None
    def local_download_complete(self,local,pos,expected):
        if not local.is_file() or not matches_size(local.stat().st_size,expected):return False
        if subprocess.run(['fuser','-s',str(local)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:return False
        displayed=self.document_size(pos)
        return displayed is not None and matches_size(local.stat().st_size,displayed) and not self.download_arrow(593,pos['y'])
    def ready_download_control(self,pos,stopped):
        # Copy Message Link shows a toast across the document. Wait for it to
        # clear before inspecting the arrow and size; never click through it.
        self.xdo('mousemove',1100,155)
        deadline=time.monotonic()+6
        while True:
            if stopped():raise UIError('Stopped by request. No download was started.')
            if self.download_arrow(593,pos['y']):
                expected=self.document_size(pos)
                if expected is not None:return expected
            if time.monotonic()>=deadline:
                raise UIError('Download arrow or file size is not visible after waiting for Telegram notifications. No file was opened.')
            time.sleep(.3)
    def download(self,item,progress,stopped):
        import shutil
        filename=item['filename']
        if Path(filename).name!=filename or filename in ('.','..'):raise UIError('Unsafe attachment filename.')
        local=STATE/'profile/tdata/temp_data'/filename
        if shutil.disk_usage(STATE).free<6*1024**3:raise UIError('Local staging needs at least 6 GiB free.')
        self.open_topic(item['message_url'])
        candidates=[r for r in self.lines((623,164,500,690)) if attachment_name_matches(filename,r['text'])]
        if len(candidates)!=1:raise UIError(f'Download target is not uniquely visible: {filename} ({len(candidates)} candidates).')
        pos=candidates[0]
        if attachment_link(self.copy(pos['x'],pos['y'],'Copy Message Link'))!=item['message_url']:raise UIError('Download target identity changed.')
        if self.copy(pos['x'],pos['y'],'Copy Filename')!=filename:raise UIError('Download filename changed.')
        # Clicking the round download control invokes Telegram's own downloader.
        # Never click a loaded document: that would launch its associated app.
        if local.exists():raise UIError('An existing local Telegram file needs review before reuse: '+filename)
        expected=self.ready_download_control(pos,stopped)
        progress(0,None,expected['bytes'])
        self.click(593,pos['y']+8)
        previous=-1;stable=0;last_change=time.monotonic()
        while True:
            time.sleep(1)
            size=local.stat().st_size if local.exists() else 0
            busy=local.exists() and subprocess.run(['fuser','-s',str(local)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
            progress(size,None,expected['bytes'])
            if stopped():
                if busy:self.click(593,pos['y']+8)
                raise UIError('Stopped by request; local data retained.')
            if size!=previous:stable=0;last_change=time.monotonic()
            else:stable+=1
            previous=size
            if stable>=3 and self.local_download_complete(local,pos,expected):
                progress(size,size,expected['bytes']);return local
            if time.monotonic()-last_change>300:raise UIError('Telegram transfer made no progress for five minutes; local data retained.')
            if shutil.disk_usage(STATE).free<1024**3:
                if busy:self.click(593,pos['y']+8)
                raise UIError('Local staging is nearly full; run stopped.')
