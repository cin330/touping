# 打包成免安装文件夹：dist\TouPing。-SkipScrcpy 只验证外壳能否打包。
param([switch]$SkipScrcpy)
$ErrorActionPreference = 'Stop'
$root   = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$vendor = Join-Path $root 'vendor\scrcpy'
$dist   = Join-Path $root 'dist\TouPing'

if (-not (Test-Path $python)) { throw '没有 .venv，请先运行：uv sync --python 3.13' }
if (-not $SkipScrcpy -and -not (Test-Path (Join-Path $vendor 'scrcpy.exe'))) {
    throw '没有 vendor\scrcpy，请先运行 scripts\fetch_scrcpy.ps1'
}

Push-Location $root
try {
    & $python -m touping.icons 'assets\touping.ico'
    if ($LASTEXITCODE -ne 0) { throw '生成图标失败' }
    & $python -m PyInstaller --noconfirm --clean --log-level WARN touping.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 失败' }
    if (-not $SkipScrcpy) {
        Copy-Item -Path $vendor -Destination (Join-Path $dist 'scrcpy') -Recurse
    }
    $size = (Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
    Write-Host ('完成：{0}（{1:N0} MB）' -f $dist, $size)
}
finally { Pop-Location }
