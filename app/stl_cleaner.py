"""Portable Delicates-style STL rewriting on staged copies, with atomic writes.

Independent implementation inspired by STL Washer Delicates 1.6.1. No supplied
executables are run. Coordinates are translated and perturbed; this is NOT a
geometry-preserving metadata operation or a guarantee of watermark removal.
"""
from array import array
import hashlib
import math
import mmap
import os
from pathlib import Path
import random
import struct
import tempfile

VERSION=1
TRI=struct.Struct('<12fH')
VERT=struct.Struct('<3f')
MAX_TRIANGLES=20_000_000

class STLCleaningError(ValueError):
    pass


def binary_count(path):
    with path.open('rb') as stream:
        header=stream.read(84)
    if len(header)==84:
        count=struct.unpack_from('<I',header,80)[0]
        if path.stat().st_size==84+count*50:return count
    return None


def ascii_binary(source,target,stopped):
    count=0
    with source.open('r',encoding='ascii') as stream,target.open('wb') as out:
        out.write(bytes(84))
        def line():
            while True:
                value=stream.readline(65537)
                if len(value)>65536:raise STLCleaningError('STL line is too long.')
                if not value:raise STLCleaningError('Incomplete ASCII STL.')
                if value.strip():return value.strip().split()
        if line()[0].lower()!='solid':raise STLCleaningError('Not a valid binary or ASCII STL.')
        while True:
            if stopped():raise STLCleaningError('STL cleaning stopped; source retained.')
            words=line()
            if words[0].lower()=='endsolid':
                if any(value.strip() for value in stream):raise STLCleaningError('Unexpected content after ASCII STL.')
                break
            if len(words)!=5 or words[:2]!=['facet','normal']:raise STLCleaningError('Invalid ASCII STL facet.')
            normal=[float(v) for v in words[2:]]
            if line()!=['outer','loop']:raise STLCleaningError('Invalid ASCII STL loop.')
            vertices=[]
            for _ in range(3):
                words=line()
                if len(words)!=4 or words[0]!='vertex':raise STLCleaningError('Invalid ASCII STL vertex.')
                vertices.extend(float(v) for v in words[1:])
            if line()!=['endloop'] or line()!=['endfacet']:raise STLCleaningError('Incomplete ASCII STL facet.')
            out.write(TRI.pack(*normal,*vertices,0));count+=1
            if count>MAX_TRIANGLES:raise STLCleaningError('STL exceeds the cleaning size limit.')
        out.seek(80);out.write(struct.pack('<I',count))
    return count


def clean_stl(path,progress=lambda percent:None,stopped=lambda:False):
    """Replace a staged STL only after validating its complete binary output."""
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise STLCleaningError('STL must be a regular staged file.')
    last=[-1]
    def report(percent):
        if stopped():raise STLCleaningError('STL cleaning stopped; source retained.')
        value=int(percent)
        if value!=last[0]:last[0]=value;progress(value)
    report(0)
    with tempfile.TemporaryDirectory(prefix='stl-clean-',dir=path.parent) as temp:
        temp=Path(temp);source=path;count=binary_count(source)
        try:
            if count is None:
                source=temp/'input.stl';count=ascii_binary(path,source,stopped)
            if not count or count>MAX_TRIANGLES:raise STLCleaningError('Empty STL or triangle count exceeds the cleaning limit.')
            digest=hashlib.sha256();sums=[0.0]*3
            with source.open('rb') as inp,mmap.mmap(inp.fileno(),0,access=mmap.ACCESS_READ) as data:
                for i in range(count):
                    start=84+i*50;raw=data[start:start+50];values=TRI.unpack(raw)
                    if not all(math.isfinite(v) for v in values[:12]):raise STLCleaningError('STL contains non-finite coordinates or normals.')
                    digest.update(raw)
                    for axis in range(3):sums[axis]+=sum(values[3+axis:12:3])
                    if i%4096==0:report(15*i/count)
                seed=digest.digest();rng=random.Random(seed)
                # Correct the original program's centroid denominator (3 vertices per facet).
                translation=[value/(count*3)+rng.uniform(-50,50) for value in sums]
                order=array('I',range(count))
                for i in range(count-1,0,-1):
                    j=rng.randrange(i+1);order[i],order[j]=order[j],order[i]
                    if i%4096==0:report(15+10*(count-i)/count)
                target=temp/'cleaned.stl'
                with target.open('wb') as out:
                    out.write(bytes(80));out.write(struct.pack('<I',count))
                    for n,index in enumerate(order):
                        values=TRI.unpack_from(data,84+index*50);vertices=[]
                        for offset in (3,6,9):
                            point=tuple(v if v else 0.0 for v in values[offset:offset+3])
                            # Identical shared vertices always receive identical offsets.
                            noise=struct.unpack('<3I',hashlib.blake2s(VERT.pack(*point),key=seed,digest_size=12).digest())
                            vertices.extend(point[a]-translation[a]+(noise[a]/0xffffffff-.5)*.001 for a in range(3))
                        out.write(TRI.pack(0,0,0,*vertices,0))
                        if n%4096==0:report(25+60*n/count)
                    out.flush();os.fsync(out.fileno())
            if binary_count(target)!=count:raise STLCleaningError('Cleaned STL has an invalid length or triangle count.')
            with target.open('rb') as result:
                result.seek(84)
                for i in range(count):
                    values=TRI.unpack(result.read(50))
                    if not all(math.isfinite(v) for v in values[:12]) or values[:3]!=(0,0,0) or values[12]!=0:
                        raise STLCleaningError('Cleaned STL failed validation.')
                    if i%4096==0:report(85+14*i/count)
            report(99);os.replace(target,path);progress(100)
            return {'cleaner':'delicates-style','version':VERSION,'triangles':count,'translation':translation,'maximum_jitter_per_axis':.0005}
        except (UnicodeError,OverflowError,struct.error) as error:
            raise STLCleaningError('Invalid or unsupported STL: '+str(error)) from error


def validate_stl(path,stopped=lambda:False):
    count=binary_count(Path(path))
    if not count or count>MAX_TRIANGLES:raise STLCleaningError('Cleaner produced an empty or invalid binary STL.')
    with Path(path).open('rb') as stream:
        stream.seek(84)
        for i in range(count):
            if i%4096==0 and stopped():raise STLCleaningError('STL validation stopped; source retained.')
            if not all(math.isfinite(v) for v in TRI.unpack(stream.read(50))[:12]):
                raise STLCleaningError('Cleaner produced non-finite coordinates.')
    return count


def cotton_executable(root,level=0,directory=''):
    import platform
    if type(level) is not int or level not in range(6):raise STLCleaningError('Choose a Cotton variant from a0 to a5.')
    machine=platform.machine().lower()
    arch={'x86_64':'x64','amd64':'x64','aarch64':'arm64','arm64':'arm64'}.get(machine)
    if platform.system()!='Linux' or not arch:raise STLCleaningError('Cotton requires Linux, WSL or a Linux container on x64/ARM64.')
    folder=Path(directory).expanduser() if directory else Path(root)/'data'/'stl-cleaners'/arch
    executable=folder/f'stlwasher-cotton-a{level}'
    if not executable.is_file() or not os.access(executable,os.X_OK):
        raise STLCleaningError('Cotton is not installed. Set its executable folder in Configuration, or select Delicates / Off.')
    with executable.open('rb') as stream:header=stream.read(20)
    expected=62 if arch=='x64' else 183
    if len(header)<20 or header[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',header,18)[0]!=expected:
        raise STLCleaningError('Cotton executable does not match this machine architecture.')
    return executable.resolve()


def clean_cotton(path,executable,progress=lambda percent:None,stopped=lambda:False):
    """Isolate the in-place Cotton writer from the staged input; reject bad output."""
    import shutil
    import signal
    import subprocess
    import time
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise STLCleaningError('STL must be a regular staged file.')
    with tempfile.TemporaryDirectory(prefix='cotton-',dir=path.parent) as temporary:
        work=Path(temporary);copy=work/'model.stl';shutil.copyfile(path,copy)
        if binary_count(copy) is None:
            converted=work/'binary.stl';ascii_binary(copy,converted,stopped);converted.replace(copy)
        before=validate_stl(copy,stopped)
        if stopped():raise STLCleaningError('Cotton stopped; source retained.')
        started=time.monotonic()
        with (work/'output.log').open('wb') as log:
            process=subprocess.Popen([str(executable),str(copy)],cwd=work,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
            try:
                while process.poll() is None:
                    if stopped():raise STLCleaningError('Cotton stopped; source retained.')
                    if time.monotonic()-started>3600:raise STLCleaningError('Cotton exceeded the one-hour limit; source retained.')
                    if log.tell()>1024*1024:raise STLCleaningError('Cotton produced excessive output; source retained.')
                    progress(None);time.sleep(.2)
                if process.returncode:raise STLCleaningError('Cotton failed with exit code '+str(process.returncode)+'; source retained.')
            finally:
                if process.poll() is None:
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    process.wait()
        if copy.is_symlink():raise STLCleaningError('Cotton output must be a regular file.')
        after=validate_stl(copy,stopped)
        if stopped():raise STLCleaningError('Cotton stopped; source retained.')
        os.replace(copy,path);progress(100)
        return {'cleaner':Path(executable).name,'version':1,'triangles_before':before,'triangles':after,'seconds':round(time.monotonic()-started,3)}
