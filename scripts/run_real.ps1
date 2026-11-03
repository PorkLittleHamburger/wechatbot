param([switch]$Quiet, [switch]$AutoResume, [switch]$Preflight)
trap {
    [Console]::Error.WriteLine($_.ToString())
    if (-not $Quiet -and -not $Preflight) { Read-Host 'Press Enter to close this window' | Out-Null }
    exit 1
}
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:WECHAT_ONEBOT_PWSH = (Get-Process -Id $PID).Path
$root = Split-Path -Parent $PSScriptRoot
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $Preflight -and -not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $pwsh = (Get-Process -Id $PID).Path
    $launchArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'))
    if ($Quiet) { $launchArgs += '-Quiet' }
    if ($AutoResume) { $launchArgs += '-AutoResume' }
    Start-Process -FilePath $pwsh -Verb RunAs -ArgumentList $launchArgs -WorkingDirectory $root -WindowStyle Normal
    exit 0
}
Set-Location -LiteralPath $root
$bundledPython = Join-Path $root 'python\python.exe'
$venv = Join-Path $root '.venv'
if (Test-Path -LiteralPath $bundledPython) {
    $python = $bundledPython
} else {
    $python = Join-Path $venv 'Scripts\python.exe'
    $expectedPackage = [IO.Path]::GetFullPath((Join-Path $root 'src\wechat_onebot\__init__.py'))
    $rebuildVenv = -not (Test-Path -LiteralPath $python)
    if (-not $rebuildVenv) {
        $installedPackage = & $python -c 'from pathlib import Path; import wechat_onebot; print(Path(wechat_onebot.__file__).resolve())' 2>$null
        $rebuildVenv = $LASTEXITCODE -ne 0 -or [string]$installedPackage -ne $expectedPackage
    }
    if ($rebuildVenv) {
        $systemPython = Get-Command python -ErrorAction SilentlyContinue
        if (-not $systemPython) { throw 'Install Python 3.11+ and add python to PATH.' }
        Write-Host 'Installing Python environment for this folder...'
        & $systemPython.Source -m venv --clear $venv
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create Python virtual environment.' }
        & $python -m pip install -U pip
        if ($LASTEXITCODE -ne 0) { throw 'Failed to update pip.' }
        & $python -m pip install -e .
        if ($LASTEXITCODE -ne 0) { throw 'Failed to install project dependencies.' }
    }
}
& $python -c 'import wechat_onebot'
if ($LASTEXITCODE -ne 0) { throw 'Failed to load WeChat OneBot from this folder.' }
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
if ($Preflight) {
    Write-Host 'Preflight passed: Python package and configuration are ready in this folder.'
    exit 0
}
if (-not $Quiet) {
    Write-Host ('WebUI: http://' + $settings.webui.host + ':' + $settings.webui.port + '/')
    if ($needsSetup) {
        Write-Host ('First-login WebUI Token: ' + $settings.webui.access_token)
        Write-Host 'Open WebUI, sign in with this token, and set a management password.'
    }
    Write-Host 'Select an account and configure OneBot endpoints in WebUI before starting the account.'
}
$runArgs = @('-m', 'wechat_onebot', '--config', $configPath, 'run')
if ($AutoResume) { $runArgs += '--resume-bridge' }
& $python @runArgs
exit $LASTEXITCODE
