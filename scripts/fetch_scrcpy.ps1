# 下载固定版本的 scrcpy，校验 SHA-256 后放到 vendor\scrcpy。
# 升级 scrcpy：改下面两行，再按 docs\验收记录.md 重新测一遍。
$ErrorActionPreference = 'Stop'
$Version = '5.0.1'
$Sha256  = 'b12a2c4ee8be317422451fc7dcf8ee20a71b5ea7ef9ad73ddd825a25316227a5'

$root   = Split-Path -Parent $PSScriptRoot
$target = Join-Path $root 'vendor\scrcpy'
$work   = Join-Path ([IO.Path]::GetTempPath()) ('scrcpy-' + [Guid]::NewGuid().ToString('N'))
$url    = "https://github.com/Genymobile/scrcpy/releases/download/v$Version/scrcpy-win64-v$Version.zip"

New-Item -ItemType Directory -Path $work | Out-Null
try {
    $zip = Join-Path $work 'scrcpy.zip'
    Write-Host "下载 $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing

    $actual = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256) { throw "SHA-256 不一致，已放弃。期望 $Sha256，实际 $actual" }
    Write-Host 'SHA-256 校验通过'

    Expand-Archive -Path $zip -DestinationPath $work
    $exe = Get-ChildItem -Path $work -Recurse -Filter 'scrcpy.exe' | Select-Object -First 1
    if (-not $exe) { throw '压缩包里没有 scrcpy.exe' }
    foreach ($name in 'adb.exe', 'scrcpy-server') {
        if (-not (Test-Path (Join-Path $exe.DirectoryName $name))) { throw "压缩包里没有 $name" }
    }
    if (Test-Path $target) { Remove-Item -Path $target -Recurse -Force }
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Move-Item -Path $exe.DirectoryName -Destination $target
    Write-Host "完成：$target"
}
finally {
    Remove-Item -Path $work -Recurse -Force -ErrorAction SilentlyContinue
}
