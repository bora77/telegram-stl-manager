"""Generate documentation-only images and run the real local Auto-fill algorithm."""
import base64
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from app.collage_auto import choose,features
from app.collage_layout import geometry,render

names=['Skyward Tower','Moon Observatory','Forest Gate','Crystal Beacon','Stone Guardian','Sunlit Arch']
colors=['#d49b54','#8998d6','#6fb29a','#58bbd0','#b984bd','#d87867']
records=[];candidates=[];pictures={};raw_images={}
for index,(name,color) in enumerate(zip(names,colors)):
    width,height=[(720,960),(960,720),(800,800)][index%3]
    image=Image.new('RGB',(width,height),'#192330');draw=ImageDraw.Draw(image)
    cx,cy=width*.5,height*.53;scale=min(width,height)*.26
    draw.ellipse((cx-scale*1.4,cy+scale*.7,cx+scale*1.4,cy+scale*1.2),fill='#0d151f')
    sides=3+index
    points=[(cx+scale*math.cos(2*math.pi*i/sides-.7),cy+scale*.45*math.sin(2*math.pi*i/sides-.7)) for i in range(sides)]
    upper=[(x,y-scale*(.8+.16*index)) for x,y in points]
    for i in range(sides):
        j=(i+1)%sides;draw.polygon([points[i],points[j],upper[j],upper[i]],fill=color,outline='#ebd5ae',width=3)
    draw.polygon(upper,fill='#dbe1df',outline=color,width=4)
    for n in range(index+2):
        x=cx-scale*.8+n*scale*1.6/max(1,index+1)
        draw.ellipse((x-8,cy+scale*.75,x+8,cy+scale*.9),fill='#edcd8d')
    draw.text((width*.08,height*.08),'NORTHSTAR / DEMO',fill='#b4c2d6',font_size=24)
    draw.text((width*.08,height*.9),name,fill='white',font_size=26)
    stream=io.BytesIO();image.save(stream,'PNG');raw=stream.getvalue();identity=hashlib.sha256(raw).hexdigest()
    pictures[identity]=base64.b64encode(raw).decode();raw_images[identity]=raw
    record={'id':identity,'name':name+'.png','relative':name+'/'+name+'.png','archive':name+'.zip','width':width,'height':height,'reason':None}
    records.append(record);candidates.append({'id':identity,'name':record['name'],'group':name,'confidence':3,'reason':'Separate model archive','uncertain':False,'sha256':identity,**features(raw)})
result=choose(candidates);card=result['cards'][0];by_id={r['id']:r for r in records}
selected=[by_id[i] for i in card['images']]
cells=geometry([(r['width'],r['height']) for r in selected],card['layout'],'square',True)
settings={'shape':'square','layout':card['layout'],'theme':'dark','title':'Northstar Miniatures 2026-11 · Release Overview','labels':card['labels']}
with tempfile.TemporaryDirectory() as temp:
    output=Path(temp)/'preview.jpg'
    sources=[{**r,'open':lambda i=r['id']:io.BytesIO(raw_images[i])} for r in selected]
    render(sources,settings,output,preview=True);preview=base64.b64encode(output.read_bytes()).decode()
print(json.dumps({'images':records,'pictures':pictures,'result':result,'preview':preview,'geometry':{'size':[3000,3000],'cells':[{'index':i,'rect':rect} for i,rect in cells]}}))
