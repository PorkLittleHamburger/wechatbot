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
    if (-not $systemPython) { throw '需要先安装 Python 3.11 或更新版本，并将 python 加入 PATH。' }
    & $systemPython.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败。' }
    & $python -m pip install -U pip
    if ($LASTEXITCODE -ne 0) { throw '更新 pip 失败。' }
    & $python -m pip install -e .
    if ($LASTEXITCODE -ne 0) { throw '安装项目依赖失败。' }
}
$configPath = Join-Path $root 'config.json'
& $python -c 'import sys; from wechat_onebot.config import ensure_config; ensure_config(sys.argv[1])' $configPath
if ($LASTEXITCODE -ne 0) { throw '初始化配置失败。' }
$settings = Get-Content -LiteralPath $configPath -Raw -Encoding utf8 | ConvertFrom-Json
if ($settings.webui.access_token -eq 'change-this-webui-token' -or $settings.onebot.access_token -eq 'change-this-token') {
    throw 'config.json 中仍是示例 Token，请设置独立的 WebUI 与 OneBot Token。'
}
if (-not $Quiet) {
    Write-Host ('WebUI: http://' + $settings.webui.host + ':' + $settings.webui.port + '/')
    Write-Host ('WebUI Access Token: ' + $settings.webui.access_token)
    Write-Host '首次使用请在「进程与账号」中扫描已登录微信账号并选择。'
}
& $python -m wechat_onebot --config $configPath run
exit $LASTEXITCODE
