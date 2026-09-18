param([switch]$Confirmed)
$ErrorActionPreference='Stop'
$HomeDir=Join-Path $env:LOCALAPPDATA 'TelegramSTLManager'
$Installer=Join-Path $HomeDir 'installer'
$Support=Join-Path $Installer 'support'
$URL='http://127.0.0.1:6093'
$LogDir=Join-Path $HomeDir ('logs\update-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force $LogDir | Out-Null
Start-Transcript -Path (Join-Path $LogDir 'update.log') | Out-Null
$Changed=$false
$Stopped=$false
$TrayGuard=$null
function Release-TrayGuard {
    if ($script:TrayGuard) {
        $script:TrayGuard.ReleaseMutex()
        $script:TrayGuard.Dispose()
        $script:TrayGuard=$null
    }
}
function Close-Tray {
    try { $s=[Threading.EventWaitHandle]::OpenExisting('Local\TelegramSTLManagerTrayExit');$s.Set() | Out-Null;$s.Dispose() } catch [Threading.WaitHandleCannotBeOpenedException] {}
    # Hold the launcher's existing mutex throughout file replacement. Even old
    # shortcuts must not start the previous server while its files are moved.
    if (!$script:TrayGuard) {
        $guard=New-Object Threading.Mutex($false, 'Local\TelegramSTLManagerTray')
        try { $locked=$guard.WaitOne(15000) } catch [Threading.AbandonedMutexException] { $locked=$true }
        if (!$locked) { $guard.Dispose();throw 'The tray did not close. Close any app error dialog and retry the update.' }
        $script:TrayGuard=$guard
    }
}
function Start-App {
    Release-TrayGuard
    Start-Process powershell.exe -ArgumentList ('-NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $Support 'Start.ps1')+'"') -WindowStyle Hidden
}
function Run-Update($Action) {
    $Arguments='-d TelegramSTL -u root --exec bash "'+$LinuxScript+'" '+$Action
    if ($Action -eq 'install') { $Arguments+=' "'+$LinuxPayload+'" '+$Expected }
    $p=Start-Process wsl.exe -ArgumentList $Arguments -NoNewWindow -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw ('Update operation failed: '+$Action) }
}
try {
    if (!(Test-Path (Join-Path $Installer 'owns-environment'))) { throw 'An existing managed installation is required. Use Install.cmd for a new installation.' }
    $Payload=Join-Path $PSScriptRoot 'telegram-stl-linux-x64.tar.gz'
    $Expected=((Get-Content ($Payload+'.sha256') -Raw).Trim() -split '\s+')[0]
    if ($Expected -notmatch '^[a-fA-F0-9]{64}$' -or (Get-FileHash $Payload -Algorithm SHA256).Hash -ne $Expected) { throw 'Package checksum failed. Nothing was installed.' }
    Write-Host 'Update Telegram STL Manager, preserving settings, accounts, history and downloads.'
    if (!$Confirmed) { if ((Read-Host 'Finish active tasks, then type UPDATE to continue') -cne 'UPDATE') { exit 0 } }
    # Recheck the live queue immediately before stopping its dedicated environment.
    try { $Queue=Invoke-RestMethod ($URL+'/api/queue') -TimeoutSec 5 } catch { $Queue=$null }
    if ($Queue -and ($Queue.active -or $Queue.organizer_active)) { throw 'A download or organizer is still running. Finish or stop it before updating.' }
    try { $MMF=Invoke-RestMethod ($URL+'/api/mmf/status') -TimeoutSec 5 } catch { $MMF=$null }
    if ($MMF -and $MMF.active) { throw 'An MMF task is still running. Finish or stop it before updating.' }
    Write-Host 'Stopping the app and temporarily blocking its desktop shortcut...'
    Close-Tray
    & wsl.exe --terminate TelegramSTL
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the application.' }
    $Stopped=$true
    $LinuxPayload=((& wsl.exe -d TelegramSTL -u root --exec wslpath -u $Payload) -join "`n").Trim()
    $LinuxScript=((& wsl.exe -d TelegramSTL -u root --exec wslpath -u (Join-Path $PSScriptRoot 'update.sh')) -join "`n").Trim()
    # Keep the previous Windows launcher alongside the update log for rollback.
    Copy-Item (Join-Path $Support 'Start.ps1') (Join-Path $LogDir 'Start.previous.ps1')
    Copy-Item (Join-Path $PSScriptRoot 'update.sh') (Join-Path $Support 'update.sh') -Force
    Run-Update 'install';$Changed=$true
    Copy-Item (Join-Path $PSScriptRoot 'Start.ps1') (Join-Path $Support 'Start.ps1') -Force
    $Wanted=((& wsl.exe -d TelegramSTL -u stl --exec cat /home/stl/telegram-stl/VERSION) -join '').Trim()
    Start-App
    $Healthy=$false
    $LastStartup='No response received.'
    for ($i=0;$i -lt 90;$i++) {
        Start-Sleep -Seconds 2
        try {
            $Reply=Invoke-RestMethod ($URL+'/api/setup') -TimeoutSec 2
            $LastStartup='Application: '+$Reply.application+'; version: '+$Reply.version+'; expected: '+$Wanted
            if ($Reply.application -eq 'telegram-stl-manager' -and $Reply.version -eq $Wanted) { $Healthy=$true;break }
        } catch { $LastStartup=$_.Exception.Message }
    }
    if (!$Healthy) { throw ('The updated application did not pass its startup check. '+$LastStartup) }
    Run-Update 'finish'
    Write-Host ('Update complete: v'+$Wanted+'. You may close this window.') -ForegroundColor Green
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($Changed) {
        Write-Host 'Restoring the previous version and saved state...'
        Close-Tray
        & wsl.exe --terminate TelegramSTL
        Run-Update 'rollback'
        Copy-Item (Join-Path $LogDir 'Start.previous.ps1') (Join-Path $Support 'Start.ps1') -Force
        Start-App
        Write-Host 'Previous version restored. Keep update.log for troubleshooting.'
    }
    elseif ($Stopped) { Start-App }
    if ($Confirmed) { Read-Host 'Press Enter to close' | Out-Null }
    exit 1
} finally { Release-TrayGuard;Stop-Transcript | Out-Null }
