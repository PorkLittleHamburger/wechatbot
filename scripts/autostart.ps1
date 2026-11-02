param(
    [switch]$Install,
    [switch]$Remove,
    [switch]$Status,
    [switch]$Json,
    [switch]$NoElevate
)
$ErrorActionPreference = 'Stop'
$taskName = 'WeChat OneBot'
$root = Split-Path -Parent $PSScriptRoot
if ($Status) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $runner = Join-Path $root 'scripts\run_real.ps1'
    $arguments = if ($task) { [string]$task.Actions[0].Arguments } else { '' }
    $managed = [bool]($task -and $arguments.Contains($runner))
    if ($Json) {
        @{ enabled = [bool]$task; managed = $managed; resumesBridge = [bool]($managed -and $arguments.Contains('-AutoResume')) } | ConvertTo-Json -Compress
        exit 0
    }
    if ($task) { $task | Select-Object TaskName,State,TaskPath | Format-Table -AutoSize }
    else { Write-Host 'Autostart is not enabled.' }
    exit 0
}
if (-not ($Install -xor $Remove)) { throw 'Specify -Install or -Remove.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    if ($NoElevate) { throw 'Managing autostart requires an elevated WebUI service.' }
    $shell = (Get-Process -Id $PID).Path
    $mode = if ($Install) { '-Install' } else { '-Remove' }
    Start-Process -FilePath $shell -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),$mode) -WorkingDirectory $root -Wait -WindowStyle Normal
    exit 0
}
if ($Remove) {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $runner = Join-Path $root 'scripts\run_real.ps1'
    if ($existing -and -not ([string]$existing.Actions[0].Arguments).Contains($runner)) {
        throw 'Autostart task belongs to another installation.'
    }
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host 'WeChat OneBot autostart task removed.'
    exit 0
}
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$powershell = (Get-Process -Id $PID).Path
$runner = Join-Path $root 'scripts\run_real.ps1'
$arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $runner + '" -Quiet -AutoResume'
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $taskPrincipal -Settings $settings -Force | Out-Null
Write-Host 'Autostart enabled at Windows logon. The last bridged account resumes after WeChat logs in.'
