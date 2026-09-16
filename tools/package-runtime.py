#!/usr/bin/env python3
"""Build an offline Linux x64 application package with its own Python runtime."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
URL='https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz'
SHA256='72748da13197c1fb161e3afeef20a6a385ff24f2165e6e2758e47008e7faba4c'


def main():
    spec=importlib.util.spec_from_file_location('public_package',ROOT/'tools/package-release.py')
    public=importlib.util.module_from_spec(spec);spec.loader.exec_module(public)
    files=public.public_files(ROOT);public.verify(ROOT,files,public.private_markers(ROOT))
    cache=ROOT/'.tools/runtime-cache';cache.mkdir(exist_ok=True)
    runtime=cache/'python-linux-x64.tar.gz'
    if not runtime.exists():urllib.request.urlretrieve(URL,runtime)
    if hashlib.sha256(runtime.read_bytes()).hexdigest()!=SHA256:raise ValueError('Python runtime checksum mismatch')
    binary=ROOT/'.tools/tdl-stl/tdl';build=json.loads((binary.parent/'build.json').read_text())
    if hashlib.sha256(binary.read_bytes()).hexdigest()!=build['binary_sha256']:raise ValueError('Telegram CLI checksum mismatch; rebuild it')
    with tempfile.TemporaryDirectory(prefix='runtime-build-',dir=ROOT/'dist') as tmp:
        app=Path(tmp)/'telegram-stl';app.mkdir()
        for rel in files:
            target=app/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,target)
        with tarfile.open(runtime) as archive:archive.extractall(app/'runtime',filter='data')
        python=app/'runtime/python/bin/python3'
        subprocess.run([python,'-m','pip','install','--disable-pip-version-check','--only-binary=:all:','Pillow==11.3.0'],check=True)
        shutil.copy2(binary,app/'.tools/tdl-stl/tdl');shutil.copy2(binary.parent/'build.json',app/'.tools/tdl-stl/build.json')
        shutil.copytree(ROOT/'.tools/7zip/runtime',app/'.tools/7zip/runtime',symlinks=True)
        (app/'runtime/manifest.json').write_text(json.dumps({'platform':'linux-x86_64','python_url':URL,'python_sha256':SHA256,'pillow':'11.3.0','telegram_cli_sha256':build['binary_sha256']},indent=2)+'\n')
        subprocess.run([python,'-c','import sqlite3,ssl,ctypes,PIL; print("Bundled Python, SQLite and Pillow verified")'],check=True)
        for path in app.rglob('__pycache__'):shutil.rmtree(path)
        output=ROOT/'dist/telegram-stl-linux-x64.tar.gz';temp=output.with_suffix('.tmp')
        with tarfile.open(temp,'w:gz') as archive:archive.add(app,arcname='telegram-stl')
        os.replace(temp,output)
        output.with_suffix(output.suffix+'.sha256').write_text(hashlib.sha256(output.read_bytes()).hexdigest()+'  '+output.name+'\n')
        print(output)


if __name__=='__main__':main()
