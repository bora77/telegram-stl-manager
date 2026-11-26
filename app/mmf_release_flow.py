"""Explicit release routing and deterministic, reply-driven bot conversations."""
import json
import re
import time
import unicodedata
import calendar
from datetime import datetime
from pathlib import Path


def delivery(manager, payload):
    mode=payload.get('mode')
    if mode=='pad':
        target=manager.store.config().get('release_pad_destination','')
        if not re.fullmatch(r'-[1-9][0-9]{0,18}',target):
            raise ValueError('Choose Your Telegram Release Channel in Configuration first.')
        return {'mode':'pad','destination':target}
    if mode!='bot':raise ValueError('Choose a release destination.')
    bot=str(payload.get('bot','')).strip()
    if not re.fullmatch(r'@[A-Za-z][A-Za-z0-9_]{4,31}',bot):raise ValueError('Enter the bot’s @username.')
    profile=payload.get('profile','eve')
    if profile not in ('eve','custom'):raise ValueError('Choose a bot conversation.')
    result={'mode':'bot','destination':bot,'profile':profile,'creator':str(payload.get('creator','')).strip()}
    if profile=='custom':
        for field in ('before','after'):
            lines=str(payload.get(field,'')).strip().splitlines()
            if not lines or len(lines)>20:raise ValueError('Enter 1–20 conversation steps before and after uploading.')
            for line in lines:
                if not re.fullmatch(r'(send|click|wait): .{1,500}',line):raise ValueError('Each step must begin with send: , click: or wait: .')
            result[field]=lines
        success=str(payload.get('success','')).strip()
        if not 8<=len(success)<=500:raise ValueError('Enter the bot’s final success message (at least 8 characters).')
        result['success']=success
    return result


def start(manager,payload):
    from app import mmf_release_prepare as prepare, mmf_release_upload as upload
    if payload.get('mode')=='finished':return prepare.finish_manually(manager,payload)
    choice=delivery(manager,payload)
    state=manager.state(release_keys={payload.get('release_key')})
    group=[i for i in state['items'] if i['release_key']==payload.get('release_key')]
    if not group:raise ValueError('Choose an existing release.')
    if sorted({i['key'] for i in group})!=payload.get('keys'):raise ValueError('Release files changed. Reopen Make release and review them.')
    if not payload.get('preparation_id') and any(not i['completed'] for i in group):raise ValueError('Download the release files first.')
    choice['creator']=choice.get('creator') or group[0]['creator']
    choice['month']=group[0].get('release_month','')
    choice['release_kind']='welcome_pack' if re.search(r'\bwelcome[\s_-]*pack\b',group[0].get('release_folder',''),re.I) else 'monthly'
    history=[p for p in prepare.rows(manager) if p['release_key']==payload['release_key']]
    issued={k for p in history if p['state']=='complete' for k in p['keys']}
    fresh=any(i['key'] not in issued for i in group)
    available=[p for p in history if p['state']=='complete' and p.get('outputs')]
    selected=payload.get('preparation_id')
    if selected and not any(p['id']==selected for p in available):raise ValueError('The selected archive is no longer available.')
    def configure_append(number):
        choice['append']=number>0
        if choice['mode']=='bot' and choice['profile']=='eve' and choice['release_kind']=='monthly':
            if choice['append']:
                if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',choice['month'] or ''):raise ValueError('An addendum needs the original release month.')
            else:eve_month(choice['month'])
    if selected or available and not fresh and not any(p['state']!='complete' for p in history):
        plan=next(p for p in available if p['id']==selected) if selected else max(available,key=lambda p:p['number'])
        configure_append(plan['number'])
        result=upload.start(manager,{'preparation_id':plan['id'],'delivery':choice})
    else:
        pending=next((p for p in history if p['state']!='complete'),None)
        configure_append(pending['number'] if pending else max((p['number'] for p in history if p['state']=='complete'),default=-1)+1)
        from app.telegram_cli import require_release_collage
        require_release_collage(Path(manager.store.config()['download_directory'])/group[0]['folder']/group[0]['release_folder']/'release.7z')
        result=upload.start(manager,{'workflow':{'release_key':payload['release_key'],'keys':payload['keys']},'title':group[0]['release_folder'],'delivery':choice})
    if choice['mode']=='bot':manager.put('release_bot_settings',{k:v for k,v in choice.items() if k not in ('month','creator','release_kind','append')})
    return result


def eve_month(month):
    now=datetime.now();current=now.strftime('%Y-%m')
    previous=f'{now.year-1}-12' if now.month==1 else f'{now.year}-{now.month-1:02}'
    if month==current:return 'This Month'
    if month==previous:return 'Last Month'
    raise ValueError('The Eve flow supports this month, last month and Welcome Packs. For other older or named releases, configure Custom conversation with the bot’s exact buttons.')


class BotConversation:
    def __init__(self,client,work,choice,export,update):
        self.client=client;self.work=work;self.choice=choice;self.export=export;self.update=update
        self.baseline={};self.last=None

    def command(self,action,value='',message=0):
        self.client._run(['stl','release-bot','--chat',self.choice['destination'],'--action',action,'--value',value,'--message',message,'--output',self.work/'bot.json'],self.work,lambda:False,timeout=90)

    @staticmethod
    def fingerprint(m):return json.dumps(m.get('raw',{}),sort_keys=True)

    def checkpoint(self):
        self.baseline={m['id']:self.fingerprint(m) for m in self.export('bot-before')}

    def wait(self,predicate,label):
        self.update('conversation',label)
        end=time.monotonic()+120
        while time.monotonic()<end:
            messages=sorted(self.export('bot-reply'),key=lambda m:m['id'],reverse=True)
            for m in messages:
                if m['raw'].get('Out') or self.baseline.get(m['id'])==self.fingerprint(m):continue
                if predicate(m):self.last=m;return m
            time.sleep(1)
        raise ValueError('Bot did not provide the expected reply: '+label+'. Check its conversation before retrying.')

    @staticmethod
    def buttons(m):
        return [b for row in (m['raw'].get('ReplyMarkup') or {}).get('Rows',[]) for b in row.get('Buttons',[]) if b.get('Data')]

    def click(self,label):
        baseline=dict(self.baseline);rejected=set()
        for attempt in range(3):
            self.baseline=dict(baseline)
            m=self.wait(lambda m:m['id'] not in rejected and any(b.get('Text')==label for b in self.buttons(m)),'Waiting for bot button: '+label)
            matches=[b for b in self.buttons(m) if b.get('Text')==label]
            if len(matches)!=1:raise ValueError('Bot button is ambiguous: '+label)
            self.checkpoint()
            try:
                self.command('callback',matches[0]['Data'],m['id'])
                return
            except Exception as error:
                if 'MESSAGE_ID_INVALID' not in str(error) or attempt==2:raise
                rejected.add(m['id'])
                self.update('conversation','Bot replaced its button; waiting for the current '+label+' button.')

    def steps(self,lines):
        for line in lines:
            kind,value=line.split(': ',1)
            for key in ('creator','month','title'):value=value.replace('{'+key+'}',self.choice.get(key,''))
            if kind=='send':self.checkpoint();self.command('text',value)
            elif kind=='click':self.click(value)
            else:self.wait(lambda m:value in m['raw'].get('Message',''),'Waiting for bot: '+value)

    def begin(self):
        if self.choice['profile']=='custom':self.steps(self.choice['before']);return
        self.checkpoint();self.command('text','/start')
        welcome=self.choice.get('release_kind')=='welcome_pack'
        append=self.choice.get('append',False)
        creator=self.choice['creator'];period='Welcome Pack' if welcome else self.choice['month'] if append else eve_month(self.choice['month'])
        def creator_key(name):
            return ''.join(unicodedata.normalize('NFKC',name).split()).casefold()
        def row_button(m):
            matches=[]
            for row in (m['raw'].get('ReplyMarkup') or {}).get('Rows',[]):
                buttons=row.get('Buttons',[])
                names=[b['Text'] for b in buttons if creator_key(b.get('Text',''))==creator_key(creator)]
                if names:
                    periods=[b for b in buttons if (creator_key(b.get('Text',''))==creator_key(creator) if welcome or append else b.get('Text','').endswith(period)) and b.get('Data')]
                    if len(names)!=1 or len(periods)>1:
                        raise ValueError('Bot creator selection is ambiguous: '+creator)
                    if periods:matches.append((names[0],periods[0]))
            if len(matches)>1:raise ValueError('Bot creator selection is ambiguous: '+creator)
            return matches[0] if matches else None
        m=self.wait(lambda m:bool(row_button(m)),'Waiting for '+creator+' · '+period)
        creator,button=row_button(m);self.choice['creator']=creator
        self.checkpoint();self.command('callback',button['Data'],m['id'])
        if welcome:
            def welcome_buttons(m):
                return [b for b in self.buttons(m) if re.fullmatch(r'\W*welcome[\s_-]*pack\W*',b.get('Text',''),re.I)]
            menu=self.wait(lambda m:bool(welcome_buttons(m)),'Waiting for Welcome Pack category')
            matches=welcome_buttons(menu)
            if len(matches)!=1:raise ValueError('Welcome Pack category is ambiguous.')
            self.click(matches[0]['Text'])
        elif append:
            month=self.choice['month']
            if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',month or ''):raise ValueError('An addendum needs the original release month.')
            self.click('Monthly');self.click(month[:4]);self.click(calendar.month_name[int(month[5:])])
        suffix='Welcome Pack' if welcome else self.choice['month'][2:]
        if append:
            expected=creator+' - '+suffix
            def existing(m):
                text=m['raw'].get('Message','')
                if ('Upload a release for '+creator) in text:raise ValueError('Eve has no existing release for this addendum. No files were forwarded.')
                return ('A release for '+expected+' already exists.') in text
            menu=self.wait(existing,'Waiting for the existing release: '+expected)
            buttons=[b for b in self.buttons(menu) if b.get('Text')=='Append to release']
            if len(buttons)!=1:raise ValueError('The existing release has no unambiguous Append to release button.')
            self.checkpoint();self.command('callback',buttons[0]['Data'],menu['id'])
            self.wait(lambda m:('Upload additional files for '+creator+' ') in m['raw'].get('Message','') and expected in m['raw'].get('Message',''),'Waiting for the addendum upload prompt: '+expected)
            return
        self.wait(lambda m:('Upload a release for '+creator) in m['raw'].get('Message','') and (creator+' - '+suffix) in m['raw'].get('Message',''),'Waiting for the selected creator and release month')

    def finish(self):
        if self.choice['profile']=='custom':self.steps(self.choice['after']);success=self.choice['success']
        else:self.click('Complete');success='Upload done and saved.'
        return self.wait(lambda m:success in m['raw'].get('Message',''),'Waiting for publication confirmation')

    def upload_filename(self,name):
        if self.choice['profile']!='eve':return name
        suffix='Welcome Pack' if self.choice.get('release_kind')=='welcome_pack' else self.choice['month'][2:]
        prefix=self.choice['creator']+' - '+suffix
        # Preserve original names, volume numbers and addendum identifiers.
        result=name if name.startswith(prefix) else prefix+' - '+name
        from app.telegram_cli import safe_filename
        if not safe_filename(result):raise ValueError('The bot upload filename is invalid or too long.')
        return result
