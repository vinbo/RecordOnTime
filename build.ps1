# Build a standalone Windows folder and zip for RecordOnTime.
# Usage:  powershell -ExecutionPolicy Bypass -File .\build.ps1
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# Keep temporary build files inside the project.  This also avoids permission
# problems in restricted/temporary Windows profiles.
$tmp = Join-Path $root ".tmp"
New-Item -ItemType Directory -Force $tmp | Out-Null
$env:TEMP = $tmp
$env:TMP = $tmp
$env:PIP_CACHE_DIR = Join-Path $root ".pip-cache"

$venvDir = Join-Path $root ".venv"
$python = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "[1/5] 正在创建 Python 虚拟环境..." -ForegroundColor Cyan
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & py -3 -m venv $venvDir
    } else {
        & python -m venv $venvDir
    }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $python)) {
        throw "创建虚拟环境失败。请安装 Python 3.11+ 并确认 py 或 python 可用。"
    }
}

Write-Host "[2/5] 正在安装运行与打包依赖..." -ForegroundColor Cyan
& $python -m pip install --upgrade pip --disable-pip-version-check
& $python -m pip install -r (Join-Path $root "requirements-dev.txt") --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "安装依赖失败。" }

$ffmpeg = Join-Path $root "vendor\ffmpeg.exe"
if (-not (Test-Path $ffmpeg) -or (Get-Item $ffmpeg).Length -lt 1000000) {
    Write-Host "    未找到 vendor\ffmpeg.exe，尝试从 imageio-ffmpeg 复制..." -ForegroundColor Yellow
    $source = & $python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
    if (-not $source -or -not (Test-Path $source)) { throw "缺少 ffmpeg.exe，且无法自动获取。" }
    Copy-Item -LiteralPath $source -Destination $ffmpeg -Force
}

if (-not $SkipTests) {
    Write-Host "[3/5] 正在运行单元测试..." -ForegroundColor Cyan
    & $python -m unittest discover -s (Join-Path $root "tests") -p "test_*.py"
    if ($LASTEXITCODE -ne 0) { throw "单元测试失败。" }
} else {
    Write-Host "[3/5] 已跳过单元测试。" -ForegroundColor Yellow
}

Write-Host "[4/5] 正在打包 EXE（首次约 1-3 分钟）..." -ForegroundColor Cyan
& $python -m PyInstaller --noconfirm --clean (Join-Path $root "RecordOnTime.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败。" }

$distDir = Join-Path $root "dist\RecordOnTime"
if (-not (Test-Path $distDir)) { throw "未找到打包输出目录：$distDir" }
Copy-Item (Join-Path $root "README.md") $distDir -Force
Copy-Item (Join-Path $root "LICENSE") $distDir -Force
Copy-Item (Join-Path $root "screenshots") $distDir -Recurse -Force

Write-Host "[5/5] 正在生成 ZIP..." -ForegroundColor Cyan
$zip = Join-Path $root "dist\RecordOnTime_v1.1.3_win64.zip"
if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path (Join-Path $distDir "*") -DestinationPath $zip -CompressionLevel Optimal

Write-Host ""
Write-Host "完成！" -ForegroundColor Green
Write-Host "  程序: $distDir\RecordOnTime.exe"
Write-Host "  压缩包: $zip"
