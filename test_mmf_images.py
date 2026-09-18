import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from mmf_images import Gallery,image_url,fetch_images

class ImagesTests(unittest.TestCase):
    def test_only_owner_product_and_original_urls(self):
        image='https://assets.myminifactory.com/object-images/abc/images/720X720-cover.png'
        p=Gallery(123)
        p.feed('<script type="application/ld+json">'+json.dumps([{'@type':'Product','sku':'3DO999','image':[image.replace('abc','wrong')]},{'@type':'Product','sku':'3DO123','image':[image,'https://images2.myminifactory.com/plain/'+image]}])+'</script>')
        self.assertEqual(p.images(),[image.replace('720X720-','')])
        for u in ('http://assets.myminifactory.com/object-images/a/images/x.jpg','https://evil.com/x.jpg','https://assets.myminifactory.com/avatars/a.jpg'):
            self.assertIsNone(image_url(u))
    def test_download_validates_image_and_deduplicates_objects(self):
        url='https://assets.myminifactory.com/object-images/abc/images/cover.png'
        class Client:
            calls=0
            def open(self,path):self.calls+=1;return io.BytesIO(('<meta property="og:image" content="'+url+'">').encode())
        b=io.BytesIO();Image.new('RGB',(10,20)).save(b,format='PNG');client=Client()
        with tempfile.TemporaryDirectory() as d,patch('mmf_images.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=io.BytesIO(b.getvalue())
            paths=fetch_images(client,[{'object_id':123},{'object_id':123}],Path(d))
            self.assertEqual(len(paths),1);self.assertEqual(client.calls,1);self.assertEqual(paths[0].read_bytes(),b.getvalue())
if __name__=='__main__':unittest.main()
