#!/usr/bin/env python3
"""Build a configuration-preserving Windows update, with no local state."""
import hashlib
from pathlib import Path
import tarfile
import zipfile
ROOT=Path(__file__).resolve().parent.parent
runtime=ROOT/'dist/telegram-stl-linux-x64.tar.gz'
import importlib.util
spec=importlib.util.spec_from_file_location('release_files',ROOT/'tools/package-release.py');public=importlib.util.module_from_spec(spec);spec.loader.exec_module(public)
with tarfile.open(runtime,'r|gz') as archive:public.verify_runtime(ROOT,archive)

output=ROOT/'dist/Telegram-STL-Manager-Update.zip'
with zipfile.ZipFile(output.with_suffix('.tmp'),'w',compression=zipfile.ZIP_DEFLATED) as out:
    prefix='Telegram-STL-Manager-Update/'
    for name in ('Update.cmd','Update.ps1','update.sh','Start.ps1'):
        out.write(ROOT/'windows'/name,prefix+('' if name.endswith('.cmd') else 'support/')+name)
    out.write(runtime,prefix+'support/'+runtime.name)
    out.writestr(prefix+'support/'+runtime.name+'.sha256',hashlib.sha256(runtime.read_bytes()).hexdigest()+'  '+runtime.name+'\n')
    out.writestr(prefix+'READ-ME.txt','Telegram STL Manager update\nCopyright © 2026 trumphater77\n\nFinish all active tasks. Extract the entire ZIP into a new folder.\nDouble-click Update.cmd using the same Windows account as the installation.\nType UPDATE when prompted. No reinstall, reset or administrator rights are needed.\nKeep the visible update window open until it reports completion.\nThe updater keeps settings, account sessions, subscriptions and history,\nand never deletes your external download destination. It backs up the\nprevious application and local data inside the managed WSL environment.\nIf startup fails, it restores the previous version and saved state.\nLogs: %LOCALAPPDATA%\\TelegramSTLManager\\logs\\update-*\\update.log\nFuture updates: Configuration > Application updates > Install update.\nNo GitHub login is required for public releases.\n')
output.with_suffix('.tmp').replace(output)
output.with_suffix('.zip.sha256').write_text(hashlib.sha256(output.read_bytes()).hexdigest()+'  '+output.name+'\n')
print(output)
