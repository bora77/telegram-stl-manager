import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from app.creator_tags import tags_from_text, normalize_catalog, sync_database
from app.first_run import parse_toc
from app.source_scope import SourceScope

class TagTests(unittest.TestCase):
    def test_normalize_and_ignore_administrative_notes(self):
        self.assertEqual(tags_from_text(' | (Minis, Sci Fi, SciFi, 40k) (paused feb25) [NSFW]'),['40K','Minis','NSFW','Sci-Fi'])
        self.assertEqual(tags_from_text(' (need dummy account to register) (Inactive?)'),[])

    def test_warhammer_40k_tags_merge(self):
        self.assertEqual(tags_from_text('(WH40K, 40k, wh40k)'),['40K'])

    def test_dungeons_and_dragons_label_is_stable(self):
        self.assertEqual(tags_from_text('(5E, Dungeons&Dragons)'),['Dungeons&Dragons'])
        data=normalize_catalog({'creators':[{'tags':['5E','Dungeons&Dragons']}]})
        self.assertEqual(normalize_catalog(data)['creators'][0]['tags'],['Dungeons&Dragons'])

    def test_utf16_and_no_tags_borrowed_from_next_artist(self):
        scope=SourceScope(123,10);text='😀 Artist (Studio) | (Minis, SciFi)\nPlain Artist\nOther | (NSFW)'
        def entity(name,topic):
            start=text.index(name)
            return {'URL':scope.prefix+str(topic),'Offset':len(text[:start].encode('utf-16-le'))//2,'Length':len(name.encode('utf-16-le'))//2}
        raw={'ID':10,'PeerID':{'ChannelID':123},'Message':text,'Entities':[entity('Artist (Studio)',20),entity('Plain Artist',21),entity('Other',22)]}
        rows=parse_toc({'id':123,'messages':[{'raw':raw}]},scope)['creators']
        self.assertEqual(rows[0]['tags'],['Minis','Sci-Fi']);self.assertEqual(rows[2]['tags'],[])

    def test_navigation_links_do_not_become_artists(self):
        scope=SourceScope(123,10)
        root={'ID':10,'PeerID':{'ChannelID':123},'Message':'[A]','Entities':[{'URL':scope.prefix+'11','Offset':0,'Length':3}]}
        child={'ID':11,'ReplyTo':{'ReplyToTopID':10,'ForumTopic':True},'PeerID':{'ChannelID':123},'Message':'Studio | (Fantasy)','Entities':[{'URL':scope.prefix+'20','Offset':0,'Length':6}]}
        rows=parse_toc({'id':123,'messages':[{'raw':root},{'raw':child}]},scope)['creators']
        self.assertEqual([r['name'] for r in rows],['Studio'])

    def test_future_toc_refresh_retains_merges_and_replaces_old_assignments(self):
        scope=SourceScope(123,10)
        def imported(description):
            raw={'ID':10,'PeerID':{'ChannelID':123},'Message':'Studio | '+description,
                 'Entities':[{'URL':scope.prefix+'20','Offset':0,'Length':6}]}
            return parse_toc({'id':123,'messages':[{'raw':raw}]},scope)
        with tempfile.TemporaryDirectory() as folder:
            sync_database(folder,imported('(Fantasy, WH40K, 40k, 5E)'))
            sync_database(folder,imported('(wh40k, 5e, SciFi)'))
            with sqlite3.connect(Path(folder)/'data/creator-tags.sqlite3') as db:
                self.assertEqual(db.execute('SELECT tag FROM artist_tags ORDER BY tag').fetchall(),
                                 [('40K',),('Dungeons&Dragons',),('Sci-Fi',)])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM tags').fetchone()[0],3)

    def test_database_counts_idempotency_and_removed_tags(self):
        with tempfile.TemporaryDirectory() as folder:
            data=normalize_catalog({'creators':[{'topic_url':'a','name':'Artist','tags':['SciFi','Sci Fi','Minis']},{'topic_url':'b','name':'Other','tags':['Minis']}]})
            self.assertEqual(data['tags'],[{'name':'Minis','count':2},{'name':'Sci-Fi','count':1}])
            sync_database(folder,data);sync_database(folder,data)
            with sqlite3.connect(Path(folder)/'data/creator-tags.sqlite3') as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM artist_tags').fetchone()[0],3)
            data['creators'][0]['tags']=[];sync_database(folder,normalize_catalog(data))
            with sqlite3.connect(Path(folder)/'data/creator-tags.sqlite3') as db:self.assertEqual(db.execute('SELECT name FROM tags').fetchall(),[('Minis',)])

if __name__=='__main__':unittest.main()
