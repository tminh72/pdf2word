# Đóng gói app desktop bằng PyInstaller.
#
#   .\packaging\build.ps1
#   .\packaging\build.ps1 -BundleTesseract   # copy luôn Tesseract-OCR vào dist
#
# Kết quả: dist\pdf2word\pdf2word.exe (one-folder, chạy offline).

param(
    [switch]$BundleTesseract,
    [switch]$NoClean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

Write-Host "==> Cài dependencies (desktop + dev + deepdoc)…"
& $Python -m pip install -e ".[desktop,dev,deepdoc]" --quiet

if (-not $NoClean) {
    Write-Host "==> Dọn build/dist cũ…"
    Remove-Item -Recurse -Force "$Root\build", "$Root\dist" -ErrorAction SilentlyContinue
}

Write-Host "==> Chạy PyInstaller…"
& $Python -m PyInstaller --noconfirm "$Root\packaging\pdf2word-desktop.spec"

if ($BundleTesseract) {
    $Src = "C:\Program Files\Tesseract-OCR"
    if (Test-Path $Src) {
        Write-Host "==> Copy Tesseract-OCR vào dist…"
        $Dst = Join-Path $Root "dist\pdf2word\tesseract"
        Copy-Item -Recurse -Force $Src $Dst
    } else {
        Write-Warning "Không thấy $Src — bỏ qua đóng gói Tesseract."
    }
}

Write-Host ""
Write-Host "Xong: $Root\dist\pdf2word\pdf2word.exe"
Write-Host "Lưu ý: máy đích cần Tesseract 5 (hoặc build kèm -BundleTesseract)."
