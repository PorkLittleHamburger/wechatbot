$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$root = Split-Path -Parent $PSScriptRoot
$project = Split-Path -Parent $root
Set-Location -LiteralPath $root
$build = & (Join-Path $project '.venv\Scripts\python.exe') (Join-Path $root 'scripts\build_portable.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$build = ($build | Select-Object -Last 1).Trim()
$zip = "$build.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip }
Compress-Archive -Path $build -DestinationPath $zip -CompressionLevel Optimal
"PORTABLE_DIR=$build"
"PORTABLE_ZIP=$zip"
