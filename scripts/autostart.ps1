param(
    [switch]$Install,
    [switch]$Remove,
    [switch]$Status,
    [switch]$Json,
    [switch]$NoElevate
)
$ErrorActionPreference = 'Stop'
$taskName = 'WeChat OneBot'
$shortcutName = 'WeChat OneBot.lnk'
$root = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $root 'scripts\run_real.ps1'
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) $shortcutName
$approvalKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder'

if ($Status) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $arguments = if ($task) { [string]$task.Actions[0].Arguments } else { '' }
    $shortcutExists = Test-Path -LiteralPath $shortcutPath
    $approval = if ($shortcutExists -and (Test-Path -LiteralPath $approvalKey)) {
        (Get-ItemProperty -LiteralPath $approvalKey).PSObject.Properties[$shortcutName].Value
    }
    $startupEnabled = $shortcutExists -and (-not $approval -or $approval[0] -ne 3)
    $managed = [bool]($task -and $arguments.Contains($runner) -and $shortcutExists)
    $result = @{
        enabled = [bool]($task -and $startupEnabled)
        managed = $managed
        resumesBridge = [bool]($managed -and $startupEnabled -and $arguments.Contains('-AutoResume'))
        startupApp = $shortcutExists
    }
    if ($Json) { $result | ConvertTo-Json -Compress; exit 0 }
    $result | Format-List
    exit 0
}
if (-not ($Install -xor $Remove)) { throw 'Specify -Install or -Remove.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    if ($NoElevate) { throw 'Managing autostart requires an elevated WebUI service.' }
    $shell = (Get-Process -Id $PID).Path
    $mode = if ($Install) { '-Install' } else { '-Remove' }
    $process = Start-Process -FilePath $shell -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),$mode) -WorkingDirectory $root -Wait -PassThru -WindowStyle Normal
    exit $process.ExitCode
}
if ($Remove) {
    Remove-Item -LiteralPath $shortcutPath -ErrorAction SilentlyContinue
    Remove-ItemProperty -Path $approvalKey -Name $shortcutName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host 'WeChat OneBot autostart removed.'
    exit 0
}

$user = $identity.Name
$powershell = (Get-Process -Id $PID).Path
$arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $runner + '" -Quiet -AutoResume'
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $root
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $taskPrincipal -Settings $settings -Force | Out-Null

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $env:WINDIR 'System32\schtasks.exe'
$shortcut.Arguments = '/Run /TN "' + $taskName + '"'
$shortcut.WorkingDirectory = $root
$shortcut.Description = 'Start WeChat OneBot at Windows logon'
$icon = Join-Path $root 'assets\icons\wechat-onebot.ico'
if (Test-Path -LiteralPath $icon) { $shortcut.IconLocation = $icon }
$shortcut.Save()
New-Item -Path $approvalKey -Force | Out-Null
Set-ItemProperty -Path $approvalKey -Name $shortcutName -Value ([byte[]](2,0,0,0,0,0,0,0,0,0,0,0))
Write-Host 'Autostart enabled. Toggle WeChat OneBot under Task Manager > Startup apps.'
