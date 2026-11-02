param([switch]$Quiet)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$root = Split-Path -Parent $PSScriptRoot
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $pwsh = (Get-Process -Id $PID).Path
    $launchArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'))
    if ($Quiet) { $launchArgs += '-Quiet' }
    Start-Process -FilePath $pwsh -Verb RunAs -ArgumentList $launchArgs -WorkingDirectory $root -WindowStyle Normal
    exit 0
}
Set-Location -LiteralPath $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if (-not $systemPython) { throw 'Install Python 3.11+ and add python to PATH.' }
    & $systemPython.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create Python virtual environment.' }
    & $python -m pip install -U pip
    if ($LASTEXITCODE -ne 0) { throw 'Failed to update pip.' }
    & $python -m pip install -e .
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install project dependencies.' }
}
$configPath = Join-Path $root 'config.json'
& $python -c 'import sys; from wechat_onebot.config import ensure_config; ensure_config(sys.argv[1])' $configPath
if ($LASTEXITCODE -ne 0) { throw 'Failed to initialize config.json.' }
$settings = Get-Content -LiteralPath $configPath -Raw -Encoding utf8 | ConvertFrom-Json
$dataDir = $settings.storage.data_dir
if (-not [IO.Path]::IsPathRooted($dataDir)) { $dataDir = Join-Path $root $dataDir }
$needsSetup = -not (Test-Path -LiteralPath (Join-Path $dataDir 'webui-auth.json'))
if ($needsSetup -and $settings.webui.access_token -eq 'change-this-webui-token') {
    throw 'Set a unique first-login WebUI token in config.json.'
}
if ($settings.onebot.access_token -eq 'change-this-token') {
    throw 'Set a unique OneBot token in config.json.'
}
if (-not $Quiet) {
    Write-Host ('WebUI: http://' + $settings.webui.host + ':' + $settings.webui.port + '/')
    if ($needsSetup) {
        Write-Host ('First-login WebUI Token: ' + $settings.webui.access_token)
        Write-Host 'Open WebUI, sign in with this token, and set a management password.'
    }
    Write-Host 'Select an account and configure OneBot endpoints in WebUI before starting the account.'
}
& $python -m wechat_onebot --config $configPath run
exit $LASTEXITCODE
