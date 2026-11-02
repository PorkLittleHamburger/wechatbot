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
if ($settings.webui.access_token -eq 'change-this-webui-token' -or $settings.onebot.access_token -eq 'change-this-token') {
    throw 'config.json still has example tokens. Set separate WebUI and OneBot tokens.'
}
if (-not $Quiet) {
    Write-Host ('WebUI: http://' + $settings.webui.host + ':' + $settings.webui.port + '/')
    Write-Host ('WebUI Access Token: ' + $settings.webui.access_token)
    Write-Host 'First use: open Processes in WebUI, scan logged-in accounts, and select one.'
}
& $python -m wechat_onebot --config $configPath run
exit $LASTEXITCODE
