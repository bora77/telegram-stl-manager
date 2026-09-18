#!/usr/bin/env python3
"""Initialize private local files and start the bundled web server in foreground."""
import os
from pathlib import Path
import runpy
import subprocess
import sys

root=Path(__file__).resolve().parent.parent
os.chdir(root);sys.path.insert(0,str(root))
os.umask(0o077)
runpy.run_path(str(root/'tools/init-local.py'),run_name='__main__')
subprocess.run([sys.executable,str(root/'tools/build-catalog.py')],check=True)
port=int(os.environ.get('TELEGRAM_STL_PORT','6093'))
print(f'Open http://localhost:{port} in your browser. Ctrl+C stops the web server.',flush=True)
runpy.run_module('app.catalog_server',run_name='__main__')
