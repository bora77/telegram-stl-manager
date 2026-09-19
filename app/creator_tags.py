"""Exact TOC tags, conservative normalization, and private artist/tag relations."""
from collections import Counter
from pathlib import Path
import re
import sqlite3
import unicodedata

ALIASES={
    'sci fi':'Sci-Fi','scifi':'Sci-Fi','sci-fi':'Sci-Fi',
    'mini':'Minis','miniatures':'Minis','minis':'Minis',
    'cos play':'Cosplay','cosplay':'Cosplay',
    'tutorial':'Tutorials','tutorials':'Tutorials',
    'wargame':'Wargaming','wargames':'Wargaming','wargaming':'Wargaming',
    'boardgame':'Board Games','boardgames':'Board Games','board game':'Board Games','board games':'Board Games',
    'space ships':'Spaceships','spaceships':'Spaceships',
    'statue':'Statues','statues':'Statues','cartoon':'Cartoons','cartoons':'Cartoons',
    'fanatsy':'Fantasy','bloodbow!':'Bloodbowl',
    **{x.casefold():x for x in ['NSFW','RPG','PDF','WW2','40K','WH40K','LOTR','DC','5E','75mm','80s','90s','OpenLock','HeroQuest','LEGO']},
    'wh40k':'40K',
    '5e':'Dungeons&Dragons','dungeons&dragons':'Dungeons&Dragons',
}


def normalize_tag(value):
    value=re.sub(r'\s+',' ',unicodedata.normalize('NFKC',value)).strip(' \t.,')
    key=value.casefold()
    if not value or len(value)>60 or re.search(r'[\x00-\x1f]',value):return None
    if re.search(r'^(inactive|paused|need .*account|switched to|unknown|marked)(\b|\?)',key):return None
    return ALIASES.get(key,value.title())


def tags_from_text(text):
    """Only bracketed descriptions after the linked name, never the name itself."""
    values=[]
    for group in re.finditer(r'\(([^()]*)\)|\[([^\[\]]*)\]',text):
        for value in re.split(r'[,;]',group.group(1) or group.group(2) or ''):
            tag=normalize_tag(value)
            if tag:values.append(tag)
    return sorted(set(values),key=str.casefold)


def normalize_catalog(catalog):
    for row in catalog['creators']:
        row['tags']=sorted({tag for value in row.get('tags',[]) if (tag:=normalize_tag(value))},key=str.casefold)
    counts=Counter(tag for row in catalog['creators'] for tag in row['tags'])
    catalog['tags']=[{'name':tag,'count':counts[tag]} for tag in sorted(counts,key=str.casefold)]
    return catalog


def sync_database(root,catalog):
    """A replaceable index; account/subscription/history databases are untouched."""
    normalize_catalog(catalog)
    path=Path(root)/'data/creator-tags.sqlite3';path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path,timeout=15) as db:
        db.executescript('''
          CREATE TABLE IF NOT EXISTS artists (topic_url TEXT PRIMARY KEY, name TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS tags (name TEXT PRIMARY KEY);
          CREATE TABLE IF NOT EXISTS artist_tags (topic_url TEXT NOT NULL, tag TEXT NOT NULL,
            PRIMARY KEY(topic_url,tag), FOREIGN KEY(topic_url) REFERENCES artists(topic_url),
            FOREIGN KEY(tag) REFERENCES tags(name));
          CREATE INDEX IF NOT EXISTS artist_tags_by_tag ON artist_tags(tag);
        ''')
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('DELETE FROM artist_tags');db.execute('DELETE FROM tags');db.execute('DELETE FROM artists')
        db.executemany('INSERT INTO tags VALUES (?)',[(r['name'],) for r in catalog['tags']])
        db.executemany('INSERT INTO artists VALUES (?,?)',[(r['topic_url'],r['name']) for r in catalog['creators']])
        db.executemany('INSERT INTO artist_tags VALUES (?,?)',[(r['topic_url'],tag) for r in catalog['creators'] for tag in r['tags']])
    path.chmod(0o600)
