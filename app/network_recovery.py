"""Bounded, cancellable retries for idempotent network reads only."""
import errno
import http.client
import math
import time
import urllib.error
import ssl
import json
import hashlib
import os
import tempfile
from pathlib import Path

DELAYS=(2,5,10,20)

class RecoveryStopped(ValueError):pass

def retryable(error):
    if getattr(error,'retryable_network',False):return True
    if isinstance(error,urllib.error.HTTPError):return error.code in (408,429,500,502,503,504)
    if isinstance(error,urllib.error.URLError) and isinstance(error.reason,ssl.SSLCertVerificationError):return False
    if isinstance(error,(urllib.error.URLError,TimeoutError,ConnectionError,http.client.IncompleteRead,ssl.SSLEOFError)):return True
    return isinstance(error,OSError) and error.errno in (errno.ENETDOWN,errno.ENETUNREACH,errno.EHOSTUNREACH,errno.ECONNRESET,errno.ECONNABORTED,errno.ETIMEDOUT,errno.EPIPE)

def wait_retry(delay,stopped,notify,attempt):
    end=time.monotonic()+delay;last=None
    while True:
        if stopped():raise RecoveryStopped('Stopped while reconnecting; saved progress is retained.')
        remaining=max(0,math.ceil(end-time.monotonic()))
        if remaining!=last:notify(attempt,len(DELAYS),remaining);last=remaining
        if not remaining:return
        time.sleep(min(.25,max(0,end-time.monotonic())))

def resume_identity(item):
    return hashlib.sha256(json.dumps({k:item.get(k) for k in ('object_id','archive_id','size','updated_at')},sort_keys=True).encode()).hexdigest()

def resume_proof(path):
    proof=Path(str(path)+'.http-resume.json')
    try:
        if proof.is_symlink() or proof.stat().st_size>8192:return {}
        value=json.loads(proof.read_text());return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}

def resume_headers(path,item,offset):
    proof=resume_proof(path)
    if offset and proof and proof.get('identity')!=resume_identity(item):raise ValueError('The partial download belongs to a different file version. Start a fresh download.')
    validator=proof.get('validator','')
    if not isinstance(validator,str) or len(validator)>1024 or any(ord(c)<32 for c in validator):raise ValueError('Invalid saved download validator.')
    return {'If-Range':validator} if offset and validator else {}

def record_response(path,item,offset,response):
    validator=response.headers.get('ETag','')
    if validator.startswith('W/'):validator=''
    validator=validator or response.headers.get('Last-Modified','')
    if len(validator)>1024 or any(ord(c)<32 for c in validator):raise ValueError('Invalid download validator.')
    previous=resume_proof(path)
    if offset and response.status==206 and previous.get('validator') and validator and validator!=previous['validator']:
        raise ValueError('The remote file changed during resume. Partial data was retained; start a fresh download.')
    proof=Path(str(path)+'.http-resume.json');fd,name=tempfile.mkstemp(dir=proof.parent,prefix='.http-resume-')
    try:
        with os.fdopen(fd,'w') as out:json.dump({'identity':resume_identity(item),'validator':validator},out)
        os.replace(name,proof)
    finally:
        if os.path.exists(name):os.unlink(name)

def retry_network(operation,stopped=lambda:False,notify=lambda *args:None,predicate=retryable):
    for attempt in range(len(DELAYS)+1):
        if stopped():raise RecoveryStopped('Stopped; saved progress is retained.')
        try:return operation()
        except Exception as error:
            if stopped():raise RecoveryStopped('Stopped; saved progress is retained.') from None
            if not predicate(error) or attempt==len(DELAYS):raise
            retry_after=getattr(error,'retry_after',0) or 0
            # Do not violate long server-directed delays or wait indefinitely.
            if retry_after>60:raise
            wait_retry(max(DELAYS[attempt],retry_after),stopped,notify,attempt+1)
