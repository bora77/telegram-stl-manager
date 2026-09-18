"""Targeted footer-stamp processing for staged PDFs, preserving other content.

Modified adaptation of PDFCleaner by RC (supplied by the project owner).
The original footer-stamp profiles are implemented here using pypdf operations. Broad
white-text, tiny-image, metadata stripping and raster rebuilds are not used.
"""
import os
from pathlib import Path
import tempfile


def numbers(values, expected):
    try:return len(values)==len(expected) and all(abs(float(a)-b)<.0001 for a,b in zip(values,expected))
    except (TypeError,ValueError):return False


def strip_stamps(operations):
    removed=set();profiles=[]
    # Isolated PDFlib bottom-left order stamp. Match the complete operation block.
    for start,(args,op) in enumerate(operations):
        if op!=b'q':continue
        block=operations[start+1:start+14]
        end=next((i for i,(_,operator) in enumerate(block) if operator==b'Q'),None)
        if end is None:continue
        inner=block[:end];ops=[operator for _,operator in inner]
        if ops not in ([b'cm',b'rg',b'RG',b'w',b'BT',b'Tf',b'Tr',b'Td',b'TJ',b'ET'],
                       [b'cm',b'rg',b'RG',b'w',b'BT',b'Tf',b'Tr',b'Td',b'TJ',b'w',b'ET']):continue
        if (numbers(inner[0][0],[1,0,0,1,5,5]) and numbers(inner[1][0],[.475,.475,.475])
                and numbers(inner[2][0],[1,1,1]) and len(inner[5][0])==2
                and str(inner[5][0][0])=='/F0' and numbers(inner[5][0][1:],[6])
                and numbers(inner[6][0],[2]) and numbers(inner[7][0],[0,0])):
            removed.update(range(start,start+end+2));profiles.append('order_footer')
    # Known dark footer badge. Restrict the candidate to path operations.
    badge=False
    for start,(args,op) in enumerate(operations):
        if op!=b'g' or not numbers(args,[0]) or start+1>=len(operations):continue
        point,operator=operations[start+1]
        if operator!=b'm' or not numbers(point,[18.43,42.52]):continue
        for end in range(start+2,min(len(operations),start+65)):
            if operations[end][1]==b'f':
                removed.update(range(start+1,end+1));profiles.append('footer_badge');badge=True;break
            if operations[end][1] not in (b'm',b'l',b'c',b'v',b'y',b'h'):break
    if badge:
        for start in range(len(operations)-4):
            block=operations[start:start+5]
            if [op for _,op in block]!=[b'rg',b'BT',b'Td',b'Tj',b'ET']:continue
            if not numbers(block[0][0],[1,1,1]) or len(block[2][0])!=2:continue
            y=float(block[2][0][1])
            if 21<=y<22 or 33<=y<34:
                removed.update(range(start+1,start+5));profiles.append('badge_footer_text')
    return [entry for i,entry in enumerate(operations) if i not in removed],profiles


def clean_pdf(path, stopped=lambda:False):
    """Replace only a verified staged copy; unchanged PDFs remain byte-identical."""
    from pypdf import PdfReader, PdfWriter
    path=Path(path)
    reader=PdfReader(path,strict=True)
    if reader.is_encrypted:raise ValueError('Encrypted PDF cannot be processed automatically.')
    writer=PdfWriter(clone_from=reader);changed=[];page_count=len(reader.pages)
    for number,page in enumerate(writer.pages,1):
        if stopped():raise ValueError('PDF processing stopped.')
        stream=page.get_contents()
        if stream is None:continue
        operations,profiles=strip_stamps(stream.operations)
        if profiles:
            stream.operations=operations;page.replace_contents(stream)
            changed.append({'page':number,'profiles':profiles})
    if not changed:return {'changed':False,'pages':page_count}
    fd,temporary=tempfile.mkstemp(prefix='.pdf-clean-',suffix='.pdf',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as output:writer.write(output)
        result=PdfReader(temporary,strict=True)
        if len(result.pages)!=page_count:raise ValueError('PDF page count changed unexpectedly.')
        for page in result.pages:
            if stopped():raise ValueError('PDF processing stopped.')
            stream=page.get_contents()
            if stream is not None:stream.operations
        os.replace(temporary,path)
    finally:Path(temporary).unlink(missing_ok=True)
    return {'changed':True,'pages':page_count,'changes':changed}
