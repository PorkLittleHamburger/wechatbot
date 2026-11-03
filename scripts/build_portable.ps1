$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Create .venv and install the project first.' }
$build = & $python (Join-Path $root 'scripts\build_portable.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$build = ($build | Select-Object -Last 1).Trim()
$zip = "$build.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip }
Compress-Archive -Path $build -DestinationPath $zip -CompressionLevel Optimal
"PORTABLE_DIR=$build"
"PORTABLE_ZIP=$zip"