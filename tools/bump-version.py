#!/usr/bin/env python3
"""Prepare one patch-version increment per commit, including retries after failure."""
from pathlib import Path
import re
import subprocess

root=Path(__file__).resolve().parent.parent
previous=subprocess.run(['git','show','HEAD:VERSION'],cwd=root,capture_output=True,text=True)
base=previous.stdout.strip() if previous.returncode==0 else '0.1.0'
if not re.fullmatch(r'\d+\.\d+\.\d+',base):raise SystemExit('Invalid committed VERSION.')
major,minor,patch=map(int,base.split('.'))
version=f'{major}.{minor}.{patch+1}'
path=root/'VERSION';path.write_text(version+'\n')
subprocess.run(['git','add','--','VERSION'],cwd=root,check=True)
print('Commit version: '+version)
