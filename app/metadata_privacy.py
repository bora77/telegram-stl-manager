"""Remove timestamp metadata from staged files without re-encoding images.

Attribution, orientation, colour profiles and image coding data are retained.
This reduces timestamp exposure; it is not a general identity anonymizer.
"""
import hashlib
import io
import os
from pathlib import Path
import re
import struct
import tempfile
import xml.etree.ElementTree as ET
import zlib
from PIL import Image

VERSION=1
SUPPORTED={'.pdf','.jpg','.jpeg','.jpe','.png','.webp'}
LIMIT=16*1024*1024
DATE_NAMES={'createdate','modifydate','metadatadate','creationdate','modificationdate','datecreated','datetimeoriginal','datetimedigitized','datetime','date','when','gpsdatestamp','gpstimestamp','creationtime','modificationtime','datecreate','datemodify','timestamp'}
EXIF_DATES={306,36867,36868,36880,36881,36882,37520,37521,37522}
XMP=b'http://ns.adobe.com/xap/1.0/\x00'
EXTENDED=b'http://ns.adobe.com/xmp/extension/\x00'

def date_name(value):return re.sub('[^a-z]','',str(value).split('}')[-1].lower()) in DATE_NAMES

def xml_dates(data):
    if len(data)>LIMIT or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():raise ValueError('Unsafe or oversized XMP metadata.')
    root=ET.fromstring(data);removed=[]
    for element in root.iter():
        for name in list(element.attrib):
            if date_name(name):del element.attrib[name];removed.append(name)
        for child in list(element):
            if date_name(child.tag):element.remove(child);removed.append(child.tag)
    return (ET.tostring(root,encoding='utf-8') if removed else data),removed

def exif_dates(data):
    if len(data)>LIMIT:raise ValueError('Oversized EXIF metadata.')
    exif=Image.Exif();exif.load(data);removed=[]
    for tag in EXIF_DATES:
        if tag in exif:del exif[tag];removed.append('EXIF '+str(tag))
    for pointer,tags in ((34665,EXIF_DATES|{37500}),(34853,{7,29})):
        if pointer not in exif:continue
        values=exif.get_ifd(pointer).copy()
        for tag in tags:
            if tag in values:del values[tag];removed.append(('Opaque camera MakerNote' if tag==37500 else 'EXIF '+str(tag)))
        exif[pointer]=values
    if not removed:return data,[]
    result=exif.tobytes()
    if not data.startswith(b'Exif\0\0'):result=result[6:]
    return result,removed

def iptc_dates(data):
    out=bytearray();offset=0;removed=[]
    while offset<len(data):
        start=offset
        if offset+5>len(data) or data[offset]!=0x1c:raise ValueError('Unsupported IPTC metadata.')
        record,tag=data[offset+1:offset+3];length=int.from_bytes(data[offset+3:offset+5],'big');offset+=5
        if length&0x8000:
            count=length&0x7fff
            if not 1<=count<=4 or offset+count>len(data):raise ValueError('Invalid IPTC length.')
            length=int.from_bytes(data[offset:offset+count],'big');offset+=count
        offset+=length
        if offset>len(data):raise ValueError('Truncated IPTC metadata.')
        if (record==2 and tag in (55,60,62,63)) or (record==1 and tag in (70,80)):removed.append(f'IPTC {record}:{tag}')
        else:out.extend(data[start:offset])
    return bytes(out),removed

def photoshop_dates(data):
    prefix=b'Photoshop 3.0\0'
    if not data.startswith(prefix):return data,[]
    offset=len(prefix);out=bytearray(prefix);removed=[]
    while offset<len(data):
        start=offset
        if data[offset:offset+4]!=b'8BIM' or offset+7>len(data):raise ValueError('Unsupported Photoshop metadata resource.')
        resource=int.from_bytes(data[offset+4:offset+6],'big');offset+=6
        size=data[offset]+1;offset+=size+(size%2)
        if offset+4>len(data):raise ValueError('Truncated Photoshop metadata.')
        header=data[start:offset];length=int.from_bytes(data[offset:offset+4],'big');offset+=4
        body=data[offset:offset+length];offset+=length+(length%2)
        if len(body)!=length or offset>len(data):raise ValueError('Truncated Photoshop resource.')
        if resource==0x0404:body,changes=iptc_dates(body)
        elif resource==0x0424:body,changes=xml_dates(body)
        else:changes=[]
        removed.extend(changes);out.extend(header+len(body).to_bytes(4,'big')+body+(b'\0' if len(body)%2 else b''))
    return (bytes(out) if removed else data),removed

def jpeg_blocks(data):
    if not data.startswith(b'\xff\xd8'):raise ValueError('Invalid JPEG header.')
    blocks=[(0xd8,b'',data[:2])];offset=2;scan=False
    while offset<len(data):
        if scan:
            start=offset
            while offset<len(data):
                if data[offset]==255:
                    n=offset+1
                    while n<len(data) and data[n]==255:n+=1
                    if n>=len(data):raise ValueError('Truncated JPEG scan.')
                    if data[n]!=0 and not 0xd0<=data[n]<=0xd7:break
                    offset=n+1
                else:offset+=1
            blocks.append((None,data[start:offset],data[start:offset]));scan=False
        start=offset
        if offset>=len(data):break
        if data[offset]!=255:raise ValueError('Invalid JPEG marker.')
        while offset<len(data) and data[offset]==255:offset+=1
        if offset>=len(data):raise ValueError('Truncated JPEG marker.')
        marker=data[offset];offset+=1
        if marker==0xd9:
            blocks.append((marker,b'',data[start:]));break
        if marker in (0xd8,0x01) or 0xd0<=marker<=0xd7:
            blocks.append((marker,b'',data[start:offset]));continue
        if offset+2>len(data):raise ValueError('Truncated JPEG segment.')
        length=int.from_bytes(data[offset:offset+2],'big')
        if length<2 or offset+length>len(data):raise ValueError('Invalid JPEG segment length.')
        body=data[offset+2:offset+length];offset+=length
        blocks.append((marker,body,data[start:offset]));scan=marker==0xda
    return blocks

def jpeg_dates(data):
    blocks=jpeg_blocks(data);extended={};removed=[];out=[]
    for marker,body,raw in blocks:
        if marker==0xe1 and body.startswith(EXTENDED):
            p=len(EXTENDED);guid=body[p:p+32];total,offset=struct.unpack('>II',body[p+32:p+40])
            if total>LIMIT:raise ValueError('Extended XMP is too large.')
            entry=extended.setdefault(guid,{'total':total,'parts':{}})
            if entry['total']!=total or offset in entry['parts']:raise ValueError('Ambiguous extended XMP.')
            entry['parts'][offset]=body[p+40:]
    for guid,entry in extended.items():
        cursor=0;parts=[]
        for offset,part in sorted(entry['parts'].items()):
            if offset!=cursor:raise ValueError('Incomplete extended XMP.')
            parts.append(part);cursor+=len(part)
        if cursor!=entry['total']:raise ValueError('Incomplete extended XMP.')
        cleaned,changes=xml_dates(b''.join(parts));entry.update(data=cleaned,guid=hashlib.md5(cleaned).hexdigest().upper().encode(),changed=bool(changes),emitted=False);removed.extend(changes)
    for marker,body,raw in blocks:
        changes=[]
        if marker==0xe1 and body.startswith(b'Exif\0\0'):body,changes=exif_dates(body)
        elif marker==0xe1 and body.startswith(XMP):
            packet,changes=xml_dates(body[len(XMP):])
            for guid,entry in extended.items():
                if entry['changed'] and guid in packet:packet=packet.replace(guid,entry['guid']);changes.append('Extended XMP reference')
            body=XMP+packet
        elif marker==0xe1 and body.startswith(EXTENDED):
            entry=extended[body[len(EXTENDED):len(EXTENDED)+32]]
            if entry['changed']:
                if not entry['emitted']:
                    for offset in range(0,len(entry['data']),64000):
                        payload=EXTENDED+entry['guid']+struct.pack('>II',len(entry['data']),offset)+entry['data'][offset:offset+64000]
                        out.append(b'\xff\xe1'+(len(payload)+2).to_bytes(2,'big')+payload)
                    entry['emitted']=True
                continue
        elif marker==0xed:body,changes=photoshop_dates(body)
        removed.extend(changes)
        if changes:
            if len(body)+2>65535:raise ValueError('Cleaned JPEG metadata exceeds segment size.')
            out.append(bytes((255,marker))+(len(body)+2).to_bytes(2,'big')+body)
        else:out.append(raw)
    return b''.join(out),removed

def inflate(data):
    z=zlib.decompressobj();value=z.decompress(data,LIMIT+1)
    if len(value)>LIMIT or not z.eof:raise ValueError('Oversized or truncated compressed metadata.')
    return value

def png_dates(data):
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Invalid PNG header.')
    out=bytearray(data[:8]);offset=8;removed=[]
    while offset<len(data):
        start=offset
        if offset+12>len(data):raise ValueError('Truncated PNG chunk.')
        size=int.from_bytes(data[offset:offset+4],'big');kind=data[offset+4:offset+8];body=data[offset+8:offset+8+size];offset+=size+12
        if offset>len(data):raise ValueError('Truncated PNG metadata.')
        if zlib.crc32(kind+body)&0xffffffff!=int.from_bytes(data[offset-4:offset],'big'):raise ValueError('PNG checksum mismatch.')
        changes=[]
        if kind==b'tIME':removed.append('PNG modification time');continue
        if kind==b'eXIf':body,changes=exif_dates(body)
        if kind in (b'tEXt',b'zTXt',b'iTXt'):
            key,rest=body.split(b'\0',1)
            if date_name(key.decode('latin1')):removed.append('PNG '+key.decode('latin1'));continue
            if kind==b'tEXt':text=rest;prefix=key+b'\0';compressed=False
            elif kind==b'zTXt':
                if not rest or rest[0]!=0:raise ValueError('Unsupported PNG text compression.')
                text=inflate(rest[1:]);prefix=key+b'\0\0';compressed=True
            else:
                if len(rest)<2 or rest[0] not in (0,1) or rest[1]!=0:raise ValueError('Invalid PNG international text.')
                language,translated,text=rest[2:].split(b'\0',2);compressed=bool(rest[0]);prefix=key+b'\0'+rest[:2]+language+b'\0'+translated+b'\0'
                if compressed:text=inflate(text)
            if b'xmp' in key.lower():text,changes=xml_dates(text)
            elif key.lower()==b'raw profile type exif':
                lines=text.decode('ascii').split('\n');raw=bytes.fromhex(''.join(lines[3:]));raw,changes=exif_dates(raw)
                if changes:text=('\nexif\n'+str(len(raw))+'\n'+raw.hex()+'\n').encode('ascii')
            if changes:body=prefix+(zlib.compress(text) if compressed else text)
        removed.extend(changes)
        out.extend((len(body).to_bytes(4,'big')+kind+body+(zlib.crc32(kind+body)&0xffffffff).to_bytes(4,'big')) if changes else data[start:offset])
    return bytes(out),removed

def webp_dates(data):
    if data[:4]!=b'RIFF' or data[8:12]!=b'WEBP' or int.from_bytes(data[4:8],'little')+8!=len(data):raise ValueError('Invalid WebP container.')
    offset=12;out=bytearray(b'WEBP');removed=[]
    while offset<len(data):
        if offset+8>len(data):raise ValueError('Truncated WebP chunk.')
        kind=data[offset:offset+4];size=int.from_bytes(data[offset+4:offset+8],'little');body=data[offset+8:offset+8+size];offset+=8+size+(size%2)
        if offset>len(data):raise ValueError('Truncated WebP metadata.')
        if kind==b'EXIF':body,changes=exif_dates(body)
        elif kind==b'XMP ':body,changes=xml_dates(body)
        else:changes=[]
        removed.extend(changes);out.extend(kind+len(body).to_bytes(4,'little')+body+(b'\0' if len(body)%2 else b''))
    return b'RIFF'+len(out).to_bytes(4,'little')+bytes(out),removed

def pdf_dates(path,temporary,stopped):
    from pypdf import PdfReader,PdfWriter
    from pypdf.generic import DictionaryObject,ArrayObject,IndirectObject,StreamObject,DecodedStreamObject,NameObject
    reader=PdfReader(path,strict=True)
    if reader.is_encrypted:raise ValueError('Encrypted PDF requires manual handling.')
    writer=PdfWriter(clone_from=reader);removed=[];seen=set();metadata_ids=set()
    def walk(value):
        if stopped():raise ValueError('Timestamp cleanup stopped.')
        if isinstance(value,IndirectObject):value=value.get_object()
        if id(value) in seen:return
        seen.add(id(value))
        if len(seen)>200000:raise ValueError('PDF exceeds metadata processing limit.')
        if isinstance(value,DictionaryObject):
            if value.get('/FT')=='/Sig' or value.get('/Type')=='/Sig':raise ValueError('Signed PDF requires manual handling; sources retained.')
            for key in list(value):
                item=value[key]
                if key=='/Metadata' and isinstance(item,StreamObject):metadata_ids.add(id(item))
                if key in ('/CreationDate','/ModDate') or (key=='/M' and isinstance(item,str) and re.match(r'D:\d{4}',item)):
                    del value[key];removed.append('PDF '+str(key))
                else:walk(item)
        elif isinstance(value,ArrayObject):
            for item in value:walk(item)
    for obj in list(writer._objects):walk(obj)
    for index,obj in enumerate(list(writer._objects)):
        if isinstance(obj,StreamObject) and (obj.get('/Type')=='/Metadata' or id(obj) in metadata_ids):
            content,changes=xml_dates(obj.get_data());removed.extend(changes)
            if changes:
                stream=DecodedStreamObject();stream.update({k:v for k,v in obj.items() if k not in ('/Length','/Filter','/DecodeParms')});stream.set_data(content);stream.indirect_reference=obj.indirect_reference;writer._objects[index]=stream
    if not removed:return []
    before=[page.get_contents().get_data() if page.get_contents() is not None else b'' for page in reader.pages]
    writer.compress_identical_objects(remove_duplicates=False,remove_unreferenced=True)
    writer.write(temporary);after=PdfReader(temporary,strict=True)
    if len(after.pages)!=len(before) or any((p.get_contents().get_data() if p.get_contents() is not None else b'')!=old for p,old in zip(after.pages,before)):raise ValueError('PDF page content changed during metadata cleanup.')
    return removed

def clean_timestamps(path,stopped=lambda:False):
    """Atomically clean a staged PDF/JPEG/PNG/WebP, leaving source archives alone."""
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('Metadata input must be a regular staged file.')
    suffix=path.suffix.lower()
    if suffix not in SUPPORTED:return {'changed':False,'supported':False,'removed':[]}
    if stopped():raise ValueError('Timestamp cleanup stopped.')
    if path.stat().st_size>512*1024**2:raise ValueError('Metadata input exceeds 512 MiB.')
    fd,name=tempfile.mkstemp(prefix='.metadata-',suffix=suffix,dir=path.parent);os.close(fd);temporary=Path(name)
    try:
        if suffix=='.pdf':removed=pdf_dates(path,temporary,stopped)
        else:
            original=path.read_bytes();cleaner=jpeg_dates if suffix in ('.jpg','.jpeg','.jpe') else png_dates if suffix=='.png' else webp_dates
            output,removed=cleaner(original)
            if removed:
                # Metadata surgery does not touch compressed image payloads.
                _,remaining=cleaner(output)
                if remaining:raise ValueError('Timestamp metadata remains after cleanup.')
                with Image.open(io.BytesIO(original)) as before,Image.open(io.BytesIO(output)) as after:
                    if before.size!=after.size or before.format!=after.format:raise ValueError('Image format changed during metadata cleanup.')
                    after.verify()
                temporary.write_bytes(output)
        if stopped():raise ValueError('Timestamp cleanup stopped.')
        if removed:temporary.replace(path)
        os.utime(path,(0,0))
        return {'changed':bool(removed),'supported':True,'removed':sorted(set(removed)),'policy_version':VERSION}
    finally:temporary.unlink(missing_ok=True)
