param(
    [switch]$Install,
    [switch]$Remove,
    [switch]$Status
)
$ErrorActionPreference = 'Stop'
$taskName = 'WeChat OneBot'
$root = Split-Path -Parent $PSScriptRoot
if ($Status) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) { $task | Select-Object TaskName,State,TaskPath | Format-Table -AutoSize }
    else { Write-Host 'Autostart is not enabled.' }
    exit 0
}
if (-not ($Install -xor $Remove)) { throw 'Specify -Install or -Remove.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $shell = (Get-Process -Id $PID).Path
    $mode = if ($Install) { '-Install' } else { '-Remove' }
    Start-Process -FilePath $shell -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),$mode) -WorkingDirectory $root -Wait -WindowStyle Normal
    exit 0
}
if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host 'WeChat OneBot autostart task removed.'
    exit 0
}
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$runner = Join-Path $root 'scripts\run_real.ps1'
$arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $runner + '" -Quiet'
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $taskPrincipal -Settings $settings -Force | Out-Null
Write-Host 'Autostart enabled at Windows logon. If WeChat is not logged in, start the account later in WebUI.'
