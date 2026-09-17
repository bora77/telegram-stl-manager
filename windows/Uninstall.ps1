param([switch]$KeepInstaller)
$ErrorActionPreference = 'Stop'
function Test-OwnedDistribution($Record, [string]$Expected) {
    if ($Record.DistributionName -ne 'TelegramSTL' -or !$Record.BasePath) { return $false }
    $Actual = [IO.Path]::GetFullPath(($Record.BasePath -replace '^\\\\\?\\','')).TrimEnd('\','/')
    $Target = [IO.Path]::GetFullPath($Expected).TrimEnd('\','/')
    return [string]::Equals($Actual,$Target,[StringComparison]::OrdinalIgnoreCase)
}
function Assert-NoRedirect([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        $Item = Get-Item -LiteralPath $Path -Force
        if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Application folder is a link or junction. It was not deleted.' }
    }
}
try {
    Add-Type -AssemblyName System.Windows.Forms
    $HomeDir = Join-Path $env:LOCALAPPDATA 'TelegramSTLManager'
    $Installer = Join-Path $HomeDir 'installer'
    $Environment = Join-Path $HomeDir 'environment'
    foreach ($Path in @($HomeDir,$Installer,$Environment)) { Assert-NoRedirect $Path }
    $Records = @(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' -ErrorAction SilentlyContinue | ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object { $_.DistributionName -eq 'TelegramSTL' })
    if ($Records.Count -gt 1) { throw 'Ambiguous WSL registration. No environment was removed.' }
    if ($Records.Count -eq 1 -and !(Test-OwnedDistribution $Records[0] $Environment)) { throw 'TelegramSTL belongs to another installation. It was not removed.' }
    $Action = if ($KeepInstaller) {'Reset test installation'} else {'Uninstall Telegram STL Manager'}
    $Detail = if ($KeepInstaller) {'Downloaded installer files and Windows diagnostic logs will be kept for a faster reinstall.'} else {'The installed application and its cached installer files will also be deleted.'}
    $Message = "This stops Telegram STL Manager and permanently deletes its local settings, account sessions, download history and unfinished temporary downloads.`n`nConfigured external download destinations are NEVER deleted. Finished downloads inside the application environment will be preserved in your Windows Documents folder first. Other WSL environments and the Windows WSL feature remain installed.`n`n$Detail`n`nContinue?"
    $Answer = [System.Windows.Forms.MessageBox]::Show($Message,$Action,'YesNo','Warning','Button2')
    if ($Answer -ne 'Yes') { exit 0 }
    if ($Records.Count -eq 1) {
        & wsl.exe --terminate TelegramSTL 2>$null | Out-Null
        # Check for an initialized application before removing its private disk.
        & wsl.exe -d TelegramSTL -u root --exec test -f /home/stl/telegram-stl/data/settings.json
        if ($LASTEXITCODE -eq 0) {
            $Python = '/home/stl/telegram-stl/runtime/python/bin/python3'
            $Guard = (& wsl.exe -d TelegramSTL -u stl --exec wslpath -u (Join-Path $PSScriptRoot 'preserve-downloads.py')).Trim()
            if ($LASTEXITCODE -ne 0) { throw 'Could not locate the download preservation helper. Nothing was removed.' }
            $CheckOutput = & wsl.exe -d TelegramSTL -u root --exec $Python $Guard
            if ($LASTEXITCODE -ne 0) { throw 'Could not verify finished downloads. Uninstall cancelled; installation files were kept.' }
            $Check = $CheckOutput | ConvertFrom-Json
            if ($Check.local_files -gt 0) {
                $Backup = Join-Path ([Environment]::GetFolderPath('MyDocuments')) ('Telegram STL downloads ' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8))
                New-Item -ItemType Directory $Backup | Out-Null
                $LinuxBackup = (& wsl.exe -d TelegramSTL -u stl --exec wslpath -u $Backup).Trim()
                if ($LASTEXITCODE -ne 0) { throw 'Could not locate the download backup directory. Nothing was removed.' }
                & wsl.exe -d TelegramSTL -u root --exec $Python $Guard $LinuxBackup
                if ($LASTEXITCODE -ne 0) { throw 'Finished downloads could not be preserved. Uninstall cancelled; the original files were kept.' }
            }
        } elseif ($LASTEXITCODE -ne 1) {
            throw 'Could not inspect the application environment. Nothing was removed.'
        }
        & wsl.exe --terminate TelegramSTL 2>$null | Out-Null
        & wsl.exe --unregister TelegramSTL
        if ($LASTEXITCODE -ne 0) { throw 'Windows could not remove the application environment. Its files and installer have been kept; close any open files and try again.' }
    }
    Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -Name 'TelegramSTLSetup' -ErrorAction SilentlyContinue
    Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\TelegramSTLManager' -Recurse -ErrorAction SilentlyContinue
    $Shell = New-Object -ComObject WScript.Shell
    foreach ($Name in @('Telegram STL Manager','Stop Telegram STL Manager')) {
        $Path = Join-Path ([Environment]::GetFolderPath('Desktop')) ($Name + '.lnk')
        if (Test-Path -LiteralPath $Path) {
            $Shortcut = $Shell.CreateShortcut($Path)
            if ($Shortcut.WorkingDirectory -eq $Installer) { Remove-Item -LiteralPath $Path -Force }
        }
    }
    if ($KeepInstaller) {
        if (Test-Path -LiteralPath $Environment) { Remove-Item -LiteralPath $Environment -Recurse -Force }
        Remove-Item -LiteralPath (Join-Path $Installer 'owns-environment') -ErrorAction SilentlyContinue
        $Message = "Reset complete. Run Install.cmd again from your extracted package, or from:`n$Installer`n`nWSL itself is already installed, so normally no restart is needed."
    } else {
        if (Test-Path -LiteralPath $HomeDir) { Remove-Item -LiteralPath $HomeDir -Recurse -Force }
        $Message = 'Telegram STL Manager was removed. External download destinations and other WSL environments were not changed. Your original downloaded ZIP/extracted package was kept.'
    }
    if ($Backup) { $Message += "`n`nFinished local downloads were preserved in:`n$Backup" }
    [System.Windows.Forms.MessageBox]::Show($Message,$Action) | Out-Null
} catch {
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message,'Telegram STL Manager removal') | Out-Null
    exit 1
}
