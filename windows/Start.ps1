param([switch]$Stop)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$URL = 'http://127.0.0.1:6093'
try {
    if ($Stop) {
        $Choice = [System.Windows.Forms.MessageBox]::Show('Stop Telegram STL Manager and its transfers? Unfinished work may need Resume when you start again.','Telegram STL Manager','YesNo')
        if ($Choice -eq 'Yes') { & wsl.exe --terminate TelegramSTL }
        exit
    }
    try {
        $Existing = Invoke-RestMethod ($URL + '/api/setup') -TimeoutSec 2
        if ($Existing.application -eq 'telegram-stl-manager') { Start-Process ($URL + '/config#first-run'); exit }
    } catch {}
    $Stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $Log = Join-Path $PSScriptRoot ('manager-' + $Stamp + '.log')
    Write-Host '> wsl.exe -d TelegramSTL -u stl --exec bash /home/stl/telegram-stl/windows/session.sh'
    $QuotedLog = $Log.Replace("'", "''")
    $ConsoleCommand = "& wsl.exe -d TelegramSTL -u stl --exec bash /home/stl/telegram-stl/windows/session.sh 2>&1 | Tee-Object -FilePath '$QuotedLog'"
    $EncodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($ConsoleCommand))
    Start-Process powershell.exe -ArgumentList @('-NoProfile','-NoExit','-EncodedCommand',$EncodedCommand)
    $Ready = $false
    for ($i=0; $i -lt 90; $i++) {
        try {
            $Reply = Invoke-RestMethod ($URL + '/api/setup') -TimeoutSec 2
            if ($Reply.application -eq 'telegram-stl-manager') { $Ready = $true; break }
        } catch {}
        Start-Sleep -Seconds 1
    }
    if (!$Ready) { throw ('The application could not start. Diagnostic log: ' + $Log) }
    Start-Process ($URL + '/config#first-run')
} catch {
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message,'Telegram STL Manager') | Out-Null
    exit 1
}
