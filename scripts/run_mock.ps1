$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$venv = Join-Path $root '.venv'
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
& $python -c 'import wechat_onebot'
if ($LASTEXITCODE -ne 0) { throw 'Failed to load WeChat OneBot from this folder.' }
& $python -c 'import sys; from wechat_onebot.config import ensure_config; ensure_config(sys.argv[1])' (Join-Path $root 'config.json')
if ($LASTEXITCODE -ne 0) { throw 'Failed to initialize config.json.' }
& $python -m wechat_onebot --config (Join-Path $root 'config.json') run --mock --log-level info
exit $LASTEXITCODE