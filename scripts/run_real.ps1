$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$root = Split-Path -Parent $PSScriptRoot
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $pwsh = (Get-Process -Id $PID).Path
    Start-Process -FilePath $pwsh -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$PSCommandPath) -WorkingDirectory $root
    exit 0
}
Set-Location -LiteralPath $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    python -m venv .venv
    & $python -m pip install -U pip
    & $python -m pip install -e .
}
if (-not (Test-Path -LiteralPath (Join-Path $root 'config.json'))) {
    Copy-Item -LiteralPath (Join-Path $root 'config.example.json') -Destination (Join-Path $root 'config.json')
}
& $python -m wechat_onebot --config (Join-Path $root 'config.json') run
exit $LASTEXITCODE
