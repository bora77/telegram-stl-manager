#!/usr/bin/env python3
"""Package the runtime and automatic Windows/WSL installer; exclude private state."""
import hashlib,json,tarfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
runtime=ROOT/'dist/telegram-stl-linux-x64.tar.gz'
if not runtime.is_file():raise SystemExit('Build the runtime first with tools/package-runtime.py')
# Reject stale runtime payloads: the installer must contain the current public files.
with tarfile.open(runtime) as archive:
    for path in [ROOT/'app/first_run.py',ROOT/'app/file_delivery.py',ROOT/'tools/launch.py',*sorted((ROOT/'windows').glob('*'))]:
        if not path.is_file():continue
        member=archive.extractfile('telegram-stl/'+str(path.relative_to(ROOT)))
        if member.read()!=path.read_bytes():raise SystemExit('Runtime is stale; rebuild it before packaging Windows.')
output=ROOT/'dist/Telegram-STL-Manager-Windows.zip'
with zipfile.ZipFile(output.with_suffix('.tmp'),'w',compression=zipfile.ZIP_DEFLATED) as out:
    for path in sorted((ROOT/'windows').iterdir()):
        if path.is_file():out.write(path,'Telegram-STL-Manager/'+('' if path.suffix=='.cmd' else 'support/')+path.name)
    out.write(runtime,'Telegram-STL-Manager/support/'+runtime.name)
    out.writestr('Telegram-STL-Manager/support/'+runtime.name+'.sha256',hashlib.sha256(runtime.read_bytes()).hexdigest()+'  '+runtime.name+'\n')
    out.writestr('Telegram-STL-Manager/READ-ME.txt','WINDOWS TEST BUILD (Intel/AMD x64)\nCopyright © 2026 trumphater77\n\nExtract this ZIP, then double-click Install.cmd.\nSetup automatically requests Windows administrator approval; choose Yes.\nNo right-click or Run as administrator is needed.\nIf a restart is requested, setup resumes at your next sign-in.\nInternet access is required during installation.\nSetup shows download progress, commands and installation output.\nAfter installation finishes, close Welcome to WSL and completed installer\nPowerShell windows. The app runs in the background with a tray icon;\nno PowerShell window needs to stay open. Double-click the icon to open\nthe app; right-click for View logs or Stop and exit. The icon may be\nunder the notification-area arrow. Closing the browser leaves transfers\nrunning; automatic availability checks need an open app page. Start the\ndesktop shortcut again after restarting Windows. Logs are under\n%LOCALAPPDATA%\\TelegramSTLManager\\logs. Use your normal Windows browser\nat http://127.0.0.1:6093.\nThe support folder contains internal files; do not run them directly.\n\nUse the Telegram STL Manager desktop shortcut. Configure Telegram,\nyour download folder in the browser. Choose Local folder or an optional\nNetwork share. MyMiniFactory is Beta and hidden by default; enable it\nin Configuration if wanted. No Ubuntu account creation or terminal\ncommands are needed.\n\nThis public package contains no saved accounts, credentials, sessions,\ncreator selections, download history or private Telegram source.\nEach tester supplies their own account and source settings.\nOn first start, complete Configuration and save all settings, then click\nFinish setup to unlock the other tabs. Connect MMF only if enabling its Beta.\n\nStop Telegram STL Manager stops only its dedicated WSL environment.\nExisting WSL distributions and their settings are not changed.\nTo test a clean reinstall, run Reset-test-install.cmd. This deletes\nlocal app data and sessions, keeping cached installer downloads and logs.\nRun Uninstall.cmd for full app removal, or use Windows Installed apps.\nBoth leave external destinations, other distributions and WSL untouched.\nFinished local downloads are preserved in Windows Documents first.\nInstallation, reboot continuation, reset, reinstallation and uninstall\nwere tested in a Windows 11 VM. Real NAS login and account downloads\nwere not tested in that VM.\n')
output.with_suffix('.tmp').replace(output)
output.with_suffix('.zip.sha256').write_text(hashlib.sha256(output.read_bytes()).hexdigest()+'  '+output.name+'\n')
print(output)
