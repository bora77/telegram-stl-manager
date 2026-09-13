"""Release month comes from attachment metadata, never its posting date."""
from datetime import datetime
import re
from zoneinfo import ZoneInfo

MONTHS={name[:3].lower():i for i,name in enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'],1)}
def release_month(filename):
    values=set()
    for year,month in re.findall(r'(?<!\d)(20\d{2})[ ._\-]+(0?[1-9]|1[0-2])(?![\da-z])',filename,re.I):values.add(f'{year}-{int(month):02d}')
    for month,year in re.findall(r'(?<![\da-z])(0?[1-9]|1[0-2])[ ._\-]+(20\d{2})(?![\da-z])',filename,re.I):values.add(f'{year}-{int(month):02d}')
    for year,month in re.findall(r'(?<![\da-z])(\d{2})[ _\-]+(0[1-9]|1[0-2])(?![\da-z])',filename,re.I):values.add(f'20{year}-{month}')
    for month,year in re.findall(r'(?<![\da-z])(0[1-9]|1[0-2])[ ._\-]+(\d{2})(?![\da-z])',filename,re.I):values.add(f'20{year}-{month}')
    words=r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober|obre)?|Nov(?:ember)?|Dec(?:ember)?)'
    # A quarterly loyalty pack must not silently be assigned to its last month.
    named_months={m[:3].lower() for m in re.findall(r'(?<![a-z])'+words+r'(?![a-z])',filename,re.I)}
    if len(named_months)>1:return None
    for month,year in re.findall(words+r'[ ._\-]+(20\d{2})',filename,re.I):values.add(f'{year}-{MONTHS[month[:3].lower()]:02d}')
    for year,month in re.findall(r'(20\d{2})[ ._\-]+'+words,filename,re.I):values.add(f'{year}-{MONTHS[month[:3].lower()]:02d}')
    return next(iter(values)) if len(values)==1 else None

def first_month(timestamp):
    return datetime.fromisoformat(timestamp).astimezone(ZoneInfo('Europe/Berlin')).strftime('%Y-%m')

def baseline_month(timestamp):
    year,month=map(int,first_month(timestamp).split('-'))
    return f'{year-1}-12' if month==1 else f'{year}-{month-1:02d}'

def in_scope(subscription,month):
    return subscription['download_scope']=='all_and_future' or month is None or month>=subscription['start_month']
