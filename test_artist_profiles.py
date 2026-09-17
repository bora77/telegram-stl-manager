import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from artist_profiles import ArtistProfiles,artist_key,image_bytes

class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'data').mkdir()
        self.profiles=ArtistProfiles(self.root);self.profiles.library.mkdir(parents=True)
    def image(self,color='red'):
        out=io.BytesIO();Image.new('RGB',(800,600),color).save(out,'PNG');return out.getvalue()
    def default(self,color):
        blob=image_bytes(self.image(color));file=hashlib.sha256(blob).hexdigest()+'.png'
        (self.profiles.library/file).write_bytes(blob)
        (self.profiles.library/'index.json').write_text(json.dumps({artist_key('Example Artist'):{'name':'Example Artist','image':file,'links':{'Patreon':'https://www.patreon.com/exampleartist'}}}))
        return blob
    def test_upload_survives_library_update_and_restore_uses_latest_default(self):
        old=self.default('red');upload=self.image('blue')
        self.profiles.save({'name':'Example Artist','mode':'upload','image':base64.b64encode(upload).decode()})
        custom=self.profiles.image('Example Artist')[0]
        new=self.default('green');self.assertNotEqual(old,new)
        self.assertEqual(self.profiles.image('Example Artist')[0],custom)
        self.profiles.save({'name':'Example Artist','mode':'initials'})
        self.assertEqual(self.profiles.image('Example Artist')[1],'image/svg+xml')
        self.profiles.save({'name':'Example Artist','mode':'default'})
        self.assertEqual(self.profiles.image('Example Artist')[0],new)
    def test_missing_logo_is_safe_initials_and_bad_upload_preserves_existing(self):
        self.default('red');before=self.profiles.image('Example Artist')
        for value in ('not base64','PHN2Zz48c2NyaXB0Lz48L3N2Zz4='):
            with self.assertRaises(ValueError):self.profiles.save({'name':'Example Artist','mode':'upload','image':value})
        self.assertEqual(self.profiles.image('Example Artist'),before)
        svg,mime=self.profiles.image('<script> Artist');self.assertNotIn(b'<script>',svg)
        self.assertEqual(mime,'image/svg+xml')
        with self.assertRaises(ValueError):artist_key('../../')
    def test_upload_resizes_without_cropping_and_persists(self):
        self.profiles.save({'name':'Example Artist','mode':'upload','image':base64.b64encode(self.image()).decode()})
        blob,_=ArtistProfiles(self.root).image('Example Artist')
        self.assertEqual(Image.open(io.BytesIO(blob)).size,(384,288))

class LinkTests(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location('collector',Path(__file__).parent/'tools/collect-artist-profiles.py')
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
    def test_only_creator_profile_links_are_collected(self):
        mod=self.module()
        page=mod.Page('<a href="https://www.myminifactory.com/users/ExampleArtist">Shop</a><a href="https://www.patreon.com/other">Other</a><a href="javascript:alert(1)">Bad</a><a href="https://evilmyminifactory.com/ExampleArtist">Fake</a><a href="https://www.patreon.com/ExampleArtist/posts/sale-123">Post</a>')
        self.assertEqual(mod.linked_profiles(page,'Example Artist'),{'MyMiniFactory':'https://www.myminifactory.com/users/ExampleArtist','Patreon':'https://www.patreon.com/ExampleArtist'})
    def test_creator_formats_match_but_wrong_identity_and_context_do_not(self):
        mod=self.module();out=io.BytesIO();Image.new('RGB',(100,100),'blue').save(out,'PNG');blob=out.getvalue()
        for title,context,expected in [('Example Artist — STL miniatures','3D printing',True),('Get more from Example Artist Miniatures on Patreon','Miniatures',True),('Other Artist — STL models','3D printing',False),('Example Artist — Photography','Portrait photography',False)]:
            with self.subTest(title=title),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'assets/artist-profiles').mkdir(parents=True)
                page=f'<meta property="og:title" content="{title}"><meta property="og:description" content="{context}"><script>{{"avatar_photo_image_urls":{{"default":"https://c10.patreonusercontent.com/avatar.png"}}}}</script>'
                def fetch(url,*args):
                    if 'patreonusercontent' in url:return blob
                    if 'patreon.com' in url:return page.encode()
                    raise ValueError('No Linktree fixture')
                with patch.object(mod,'ROOT',root),patch.object(mod,'fetch',side_effect=fetch):_,record=mod.collect('Example Artist')
                self.assertEqual(bool(record.get('image')),expected)

if __name__=='__main__':unittest.main()
