$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$config = Join-Path $root 'config.json'
$log = Join-Path $root 'data\live-runtime.log'
$status = Join-Path $root 'data\live-runtime.exit.json'
Set-Location -LiteralPath $root
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    '{"ok":false,"error":"not elevated"}' | Set-Content -LiteralPath $status -Encoding utf8
    exit 5
}
& $python -m wechat_onebot --config $config bind 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -u (Join-Path $root 'scripts\live_runtime_preflight.py') --config $config 2>&1 | Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE
[ordered]@{ ok=($code -eq 0); stage='runtime-network'; exit_code=$code; finished_at=(Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $status -Encoding utf8
exit $code
