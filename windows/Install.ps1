param([switch]$MachineSetup)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'Continue'
# Only machine prerequisites run elevated. WSL registration and shortcuts remain
# owned by the original user, even when UAC asks for another admin's credentials.
if ($MachineSetup) {
    try {
        $Principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
        if (!$Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator approval is required for Windows setup.' }
        Write-Host 'Checking Windows Subsystem for Linux (administrator)...'
        Write-Host '> wsl.exe --status'
        & cmd.exe /d /c 'wsl.exe --status 2>&1' | ForEach-Object { $_ -replace "`0", '' }
        if ($LASTEXITCODE -eq 0) { exit 0 }
        Write-Host '> wsl.exe --install --no-distribution'
        & wsl.exe --install --no-distribution
        if ($LASTEXITCODE -notin @(0,3010)) { throw 'WSL installation did not finish. Retry setup after correcting the error shown above.' }
        exit 3010
    } catch {
        Write-Host $_.Exception.Message -ForegroundColor Red
        Read-Host 'Press Enter to close'
        exit 1
    }
}
$Distro = 'TelegramSTL'
$HomeDir = Join-Path $env:LOCALAPPDATA 'TelegramSTLManager'
$Installer = Join-Path $HomeDir 'installer'
$Support = Join-Path $Installer 'support'
$PackageRoot = Split-Path -Parent $PSScriptRoot
$WslImage = 'ubuntu-24.04.5-wsl-amd64.wsl'
$ImageHash = 'bb415d824822c4b878125729af451a5d18fb13d1cf5cbed9a7393ad64ac6039e'
function RunWSL([string[]]$Arguments) {
    Write-Host ('> wsl.exe ' + ($Arguments -join ' '))
    & wsl.exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Windows Linux environment reported an error ($LASTEXITCODE). Setup can be retried safely." }
}
try {
    if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'This test package requires a 64-bit Intel/AMD Windows PC.' }
    New-Item -ItemType Directory -Force $Installer | Out-Null
    $SetupLog = Join-Path $Installer 'setup.log'
    Start-Transcript -Path $SetupLog -Append -Force | Out-Null
    Write-Host ('Setup started: ' + (Get-Date).ToString('s'))
    if ($PackageRoot -ne $Installer) { Copy-Item "$PackageRoot\*" $Installer -Recurse -Force }
    $Resume = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $Support 'Install.ps1') + '"'
    $RunOnce = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce'
    $Payload = Join-Path $Support 'telegram-stl-linux-x64.tar.gz'
    $Expected = ((Get-Content ($Payload + '.sha256') -Raw).Trim() -split '\s+')[0]
    if ((Get-FileHash $Payload -Algorithm SHA256).Hash -ne $Expected) { throw 'Application package checksum failed. Download the package again.' }
    Write-Host 'Requesting Windows administrator approval. Choose Yes in the prompt.'
    $MachineArguments = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $Support 'Install.ps1') + '" -MachineSetup'
    $Process = Start-Process -FilePath "$PSHOME\powershell.exe" -ArgumentList $MachineArguments -Verb RunAs -Wait -PassThru
    if ($Process.ExitCode -eq 3010) {
        New-Item -Force $RunOnce | Out-Null
        New-ItemProperty $RunOnce -Name 'TelegramSTLSetup' -Value $Resume -PropertyType String -Force | Out-Null
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show('Restart Windows to finish enabling WSL. Telegram STL setup will continue at your next sign-in.','Telegram STL Manager') | Out-Null
        exit 0
    }
    if ($Process.ExitCode -ne 0) { throw 'Windows prerequisite setup failed. Review the administrator console output and run Install.cmd again.' }
    $Installed = ((& wsl.exe --list --quiet) -replace "`0", '')
    if ($Installed -notcontains $Distro) {
        $Image = Join-Path $Installer $WslImage
        if (!(Test-Path $Image) -or (Get-FileHash $Image -Algorithm SHA256).Hash -ne $ImageHash) {
            Write-Host 'Downloading the private application environment from Ubuntu...'
            Write-Host ('> curl.exe: downloading ' + $WslImage + ' (progress, speed and errors below)')
            & curl.exe --fail --location --show-error --retry 3 --connect-timeout 30 --speed-limit 1024 --speed-time 60 --output ($Image + '.partial') ('https://releases.ubuntu.com/noble/' + $WslImage)
            if ($LASTEXITCODE -ne 0) { throw 'Ubuntu download failed. Check your Internet connection and run Install.cmd again.' }
            if ((Get-FileHash ($Image + '.partial') -Algorithm SHA256).Hash -ne $ImageHash) { throw 'Ubuntu image checksum failed.' }
            Move-Item ($Image + '.partial') $Image -Force
        }
        RunWSL -Arguments @('--import',$Distro,(Join-Path $HomeDir 'environment'),$Image,'--version','2')
        Set-Content (Join-Path $Installer 'owns-environment') 'TelegramSTL' -Encoding ASCII
    } elseif (!(Test-Path (Join-Path $Installer 'owns-environment'))) {
        throw 'An unrelated WSL environment named TelegramSTL already exists. It was not modified.'
    }
    $LinuxPayload = ((& wsl.exe -d $Distro -u root --exec wslpath -u $Payload) -join "`n").Trim()
    if ($LASTEXITCODE -ne 0 -or !$LinuxPayload) { throw 'Could not locate the application payload inside WSL.' }
    $LinuxBootstrap = ((& wsl.exe -d $Distro -u root --exec wslpath -u (Join-Path $Support 'bootstrap.sh')) -join "`n").Trim()
    if ($LASTEXITCODE -ne 0 -or !$LinuxBootstrap) { throw 'Could not locate the bootstrap script inside WSL.' }
    Write-Host 'Installing application dependencies automatically...'
    RunWSL -Arguments @('-d',$Distro,'-u','root','--exec','bash',$LinuxBootstrap,$LinuxPayload)
    $PrivateProfile = Join-Path $Support 'private-profile.json'
    if (Test-Path $PrivateProfile) {
        Write-Host 'Restoring private test settings, account sessions and history (secret contents are not printed)...'
        $ProfileHash = ((Get-Content ($PrivateProfile + '.sha256') -Raw).Trim() -split '\s+')[0]
        if ((Get-FileHash $PrivateProfile -Algorithm SHA256).Hash -ne $ProfileHash) { throw 'Private test profile checksum failed.' }
        $LinuxProfile = ((& wsl.exe -d $Distro -u root --exec wslpath -u $PrivateProfile) -join "`n").Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Could not read the private test profile.' }
        RunWSL -Arguments @('-d',$Distro,'-u','stl','--exec','/home/stl/telegram-stl/runtime/python/bin/python3','/home/stl/telegram-stl/windows/import-profile.py',$LinuxProfile)
        RunWSL -Arguments @('-d',$Distro,'-u','root','--exec','/home/stl/telegram-stl/runtime/python/bin/python3','/home/stl/telegram-stl/windows/import-profile.py',$LinuxProfile,'--mount')
    }
    Write-Host 'Creating desktop shortcuts and Windows uninstall entry...'
    $Shell = New-Object -ComObject WScript.Shell
    foreach ($Name in @('Telegram STL Manager','Stop Telegram STL Manager')) {
        $Shortcut = $Shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) ($Name + '.lnk')))
        $Shortcut.TargetPath = 'powershell.exe'
        $Shortcut.Arguments = '-NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + (Join-Path $Support 'Start.ps1') + '"' + $(if ($Name.StartsWith('Stop')) {' -Stop'} else {''})
        $Shortcut.WorkingDirectory = $Installer
        $Shortcut.Save()
    }
    $UninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\TelegramSTLManager'
    New-Item -Force $UninstallKey | Out-Null
    $Properties = @{
        DisplayName = 'Telegram STL Manager (WSL)'
        DisplayVersion = 'Test build'
        Publisher = 'Telegram STL Manager'
        InstallLocation = $HomeDir
        UninstallString = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $Support 'Uninstall.ps1') + '"'
    }
    foreach ($Property in $Properties.GetEnumerator()) {
        New-ItemProperty $UninstallKey -Name $Property.Key -Value $Property.Value -PropertyType String -Force | Out-Null
    }
    Remove-ItemProperty $RunOnce -Name 'TelegramSTLSetup' -ErrorAction SilentlyContinue
    Write-Host 'Starting the application in the Windows notification area. Right-click its tray icon for logs or Stop and exit.'
    Start-Process powershell.exe -ArgumentList ('-NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $Support 'Start.ps1') + '"') -WindowStyle Hidden
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    $Failure = ($_ | Out-String) -replace "`0", ''
    if ([string]::IsNullOrWhiteSpace($Failure)) { $Failure = 'Setup failed without a readable error message.' }
    Write-Host $Failure
    $LogHint = if ($SetupLog) { "`n`nSetup log: $SetupLog" } else { '' }
    [System.Windows.Forms.MessageBox]::Show(($Failure + $LogHint),'Telegram STL Manager setup') | Out-Null
    exit 1
} finally {
    try { Stop-Transcript | Out-Null } catch {}
}
