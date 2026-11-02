$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    python -m venv .venv
    & $python -m pip install -U pip
    & $python -m pip install -e .
}
& $python -c 'import sys; from wechat_onebot.config import ensure_config; ensure_config(sys.argv[1])' (Join-Path $root 'config.json')
if ($LASTEXITCODE -ne 0) { throw '初始化配置失败。' }
& $python -m wechat_onebot --config (Join-Path $root 'config.json') run --mock --log-level info
exit $LASTEXITCODE
