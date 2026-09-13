"""Telegram displays binary sizes rounded to one or more decimal places."""
from decimal import Decimal
import re

def display_size(text):
    match=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB)\s*',text,re.I)
    if not match:return None
    number,unit=match.groups();factor=1024**['B','KB','MB','GB','TB'].index(unit.upper())
    decimals=len(number.split('.')[1]) if '.' in number else 0
    return {'bytes':int(Decimal(number)*factor),'tolerance':max(1,int(Decimal(factor)/(10**decimals))),'label':text.strip()}

def matches_size(size,expected):
    return expected is not None and size>0 and abs(size-expected['bytes'])<=expected['tolerance']
