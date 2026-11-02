$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$preflight = Join-Path $root 'scripts\live_preflight.py'
$config = Join-Path $root 'config.json'
$log = Join-Path $root 'data\live-preflight.log'
$status = Join-Path $root 'data\live-preflight.exit.json'
Set-Location -LiteralPath $root
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
"[{0}] IsAdmin={1}" -f (Get-Date -Format o), $isAdmin | Tee-Object -FilePath $log -Append
if (-not $isAdmin) {
    '{"ok":false,"error":"not elevated"}' | Set-Content -LiteralPath $status -Encoding utf8
    exit 5
}
& $python -u $preflight --config $config --send-filehelper 2>&1 | Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE
$result = [ordered]@{ ok = ($code -eq 0); exit_code = $code; finished_at = (Get-Date).ToString('o') }
$result | ConvertTo-Json | Set-Content -LiteralPath $status -Encoding utf8
exit $code
