$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$config = Join-Path $root 'config.json'
$log = Join-Path $root 'data\live-full-stack.log'
$status = Join-Path $root 'data\live-full-stack.exit.json'
Set-Location -LiteralPath $root
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
"[{0}] IsAdmin={1}" -f (Get-Date -Format o), $isAdmin | Tee-Object -FilePath $log -Append
if (-not $isAdmin) {
    '{"ok":false,"error":"not elevated"}' | Set-Content -LiteralPath $status -Encoding utf8
    exit 5
}
& $python -m wechat_onebot --config $config bind 2>&1 | Tee-Object -FilePath $log -Append
$bindCode = $LASTEXITCODE
if ($bindCode -ne 0) {
    [ordered]@{ ok=$false; stage='bind'; exit_code=$bindCode; finished_at=(Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $status -Encoding utf8
    exit $bindCode
}
& $python -u (Join-Path $root 'scripts\live_full_stack_preflight.py') --config $config 2>&1 | Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE
[ordered]@{ ok=($code -eq 0); stage='full_stack'; exit_code=$code; finished_at=(Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $status -Encoding utf8
exit $code
