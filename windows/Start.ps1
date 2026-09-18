param([switch]$Stop, [switch]$Tray)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$URL = 'http://127.0.0.1:6093'
$Title = 'Telegram STL Manager'
$MutexName = 'Local\TelegramSTLManagerTray'
$StopEventName = 'Local\TelegramSTLManagerTrayStop'
$ExitEventName = 'Local\TelegramSTLManagerTrayExit'
function Stop-Manager {
    $Process = Start-Process wsl.exe -ArgumentList @('--terminate','TelegramSTL') -WindowStyle Hidden -Wait -PassThru
    if ($Process.ExitCode -ne 0) { throw 'Windows could not stop Telegram STL Manager. Please try again.' }
}
try {
    if ($Stop) {
        $Choice = [System.Windows.Forms.MessageBox]::Show('Stop Telegram STL Manager and its transfers? Unfinished work may need Resume when you start again.',$Title,'YesNo','Question','Button2')
        if ($Choice -eq 'Yes') {
            $Signal = $null
            try { $Signal = [Threading.EventWaitHandle]::OpenExisting($StopEventName) } catch [Threading.WaitHandleCannotBeOpenedException] {}
            if ($Signal) { $Signal.Set() | Out-Null; $Signal.Dispose() } else { Stop-Manager }
        }
        exit
    }
    if (!$Tray) {
        # The installer stays visible; only the long-lived tray host runs hidden.
        $Arguments = '-NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $PSCommandPath + '" -Tray'
        Start-Process "$PSHOME\powershell.exe" -ArgumentList $Arguments -WindowStyle Hidden
        exit
    }
    $Created = $false
    $Mutex = New-Object Threading.Mutex($true, $MutexName, [ref]$Created)
    if (!$Created) { $Mutex.Dispose(); Start-Process $URL; exit }
    $StopSignal = New-Object Threading.EventWaitHandle($false, [Threading.EventResetMode]::AutoReset, $StopEventName)
    $ExitSignal = New-Object Threading.EventWaitHandle($false, [Threading.EventResetMode]::AutoReset, $ExitEventName)
    $Context = New-Object System.Windows.Forms.ApplicationContext
    $Icon = New-Object System.Windows.Forms.NotifyIcon
    $Menu = New-Object System.Windows.Forms.ContextMenuStrip
    $Open = $Menu.Items.Add('Open Telegram STL Manager')
    $Logs = $Menu.Items.Add('View logs')
    $Menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
    $Quit = $Menu.Items.Add('Stop and exit')
    $Icon.Icon = [System.Drawing.SystemIcons]::Application
    $Icon.Text = 'Telegram STL Manager - starting'
    $Icon.ContextMenuStrip = $Menu
    $Icon.Visible = $true
    $LogRoot = Join-Path $env:LOCALAPPDATA 'TelegramSTLManager\logs'
    $LogDir = Join-Path $LogRoot (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
    New-Item -ItemType Directory -Force $LogDir | Out-Null
    $Open.add_Click({ Start-Process $URL })
    $Icon.add_DoubleClick({ Start-Process $URL })
    $Logs.add_Click({ Start-Process explorer.exe -ArgumentList ('"' + $LogDir + '"') })
    $Quit.add_Click({
        $Choice = [System.Windows.Forms.MessageBox]::Show('Stop Telegram STL Manager and its transfers? Unfinished work may need Resume when you start again.',$Title,'YesNo','Question','Button2')
        if ($Choice -eq 'Yes') { $StopSignal.Set() | Out-Null }
    })
    $Server = $null
    $Ready = $false
    try {
        $Existing = Invoke-RestMethod ($URL + '/api/setup') -TimeoutSec 2
        $Ready = $Existing.application -eq 'telegram-stl-manager'
    } catch {}
    if (!$Ready) {
        $Recovery = Join-Path $PSScriptRoot 'update.sh'
        if (Test-Path $Recovery) {
            $LinuxRecovery = ((& wsl.exe -d TelegramSTL -u root --exec wslpath -u $Recovery) -join "`n").Trim()
            & wsl.exe -d TelegramSTL -u root --exec bash $LinuxRecovery recover
            if ($LASTEXITCODE -ne 0) { throw 'Update recovery needs attention. Run Update.cmd again and keep its log.' }
        }
        $Server = Start-Process wsl.exe -ArgumentList @('-d','TelegramSTL','-u','stl','--exec','bash','/home/stl/telegram-stl/windows/session.sh') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $LogDir 'server.log') -RedirectStandardError (Join-Path $LogDir 'server-errors.log')
    }
    $State = @{ Ready=$Ready; BrowserOpened=$false; Deadline=(Get-Date).AddSeconds(90); LastCheck=[datetime]::MinValue }
    $Timer = New-Object System.Windows.Forms.Timer
    $Timer.Interval = 1000
    $Timer.add_Tick({
        try {
            # Uninstall can dismiss the tray without racing its own WSL operations.
            if ($ExitSignal.WaitOne(0)) { $Context.ExitThread(); return }
            if ($StopSignal.WaitOne(0)) { Stop-Manager; $Context.ExitThread(); return }
            if ($Server -and $Server.HasExited) {
                if (!$State.Ready -or $Server.ExitCode -ne 0) { throw ('The server stopped. Open View logs for details: ' + $LogDir) }
                $Context.ExitThread(); return
            }
            if (!$State.Ready -or (!$Server -and ((Get-Date) - $State.LastCheck).TotalSeconds -ge 5)) {
                $State.LastCheck = Get-Date
                try {
                    $Reply = Invoke-RestMethod ($URL + '/api/setup') -TimeoutSec 1
                    $State.Ready = $Reply.application -eq 'telegram-stl-manager'
                } catch { $State.Ready = $false }
            }
            if ($State.Ready -and !$State.BrowserOpened) {
                $Icon.Text = 'Telegram STL Manager - running'
                Start-Process ($URL + '/config#first-run')
                $Icon.ShowBalloonTip(5000,$Title,'Running in the background. Double-click this icon to open the app; right-click for logs or Stop and exit.',[System.Windows.Forms.ToolTipIcon]::Info)
                $State.BrowserOpened = $true
            }
            if (!$State.Ready -and (Get-Date) -gt $State.Deadline) { throw ('The application is unavailable. Diagnostic logs: ' + $LogDir) }
        } catch {
            $Timer.Stop()
            $Icon.Text = 'Telegram STL Manager - needs attention'
            [System.Windows.Forms.MessageBox]::Show($_.Exception.Message,$Title,'OK','Error') | Out-Null
            # Keep the tray usable for logs and stopping, without repeated alerts.
            $State.Deadline = [datetime]::MaxValue
            $script:Server = $null
            $Timer.Start()
        }
    })
    $Timer.Start()
    [System.Windows.Forms.Application]::Run($Context)
} catch {
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message,$Title,'OK','Error') | Out-Null
    exit 1
} finally {
    if ($Timer) { $Timer.Stop(); $Timer.Dispose() }
    if ($Icon) { $Icon.Visible=$false; $Icon.Dispose() }
    if ($Menu) { $Menu.Dispose() }
    if ($Context) { $Context.Dispose() }
    if ($StopSignal) { $StopSignal.Dispose() }
    if ($ExitSignal) { $ExitSignal.Dispose() }
    if ($Mutex -and $Created) { $Mutex.ReleaseMutex(); $Mutex.Dispose() }
}
