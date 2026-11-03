$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    $installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($LASTEXITCODE -ne 0 -or -not $installation) { throw 'MSVC x64 tools are required.' }
    Import-Module (Join-Path $installation 'Common7/Tools/Microsoft.VisualStudio.DevShell.dll')
    Enter-VsDevShell -VsInstallPath $installation -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64'
}
$target = Join-Path $root '.development/native-tests'
New-Item -ItemType Directory -Path $target -Force | Out-Null
Push-Location $target
try {
    & cl.exe /nologo /EHsc /std:c++17 /W4 /UNDEBUG (Join-Path $root 'tests/native/local_file_path_test.cpp') /Fe:local_file_path_test.exe
    if ($LASTEXITCODE -ne 0) { throw 'Native path test compilation failed.' }
    & .\local_file_path_test.exe
    if ($LASTEXITCODE -ne 0) { throw 'Native path assertions failed.' }
} finally {
    Pop-Location
}
