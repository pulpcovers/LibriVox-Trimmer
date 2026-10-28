# Builds the Windows one-folder release and writes a ZIP plus its SHA-256 checksum to dist/.
# Usage: .\packaging\build_release.ps1 [-Python path\to\python.exe]
# The Python environment needs the app's dependencies and PyInstaller installed.
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = (Select-String -Path "pyproject.toml" -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$name = "LibriVox Trimmer"
$zipPath = Join-Path $root "dist\LibriVox-Trimmer-$version-windows.zip"

& $Python -m PyInstaller --noconfirm --clean --distpath dist --workpath build "packaging\$name.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

Remove-Item -LiteralPath $zipPath, "$zipPath.sha256" -ErrorAction SilentlyContinue
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    (Join-Path $root "dist\$name"), $zipPath,
    [System.IO.Compression.CompressionLevel]::Optimal, $true)

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLower()
"$hash  $(Split-Path -Leaf $zipPath)" | Set-Content -Encoding ascii "$zipPath.sha256"

Write-Output "Built $zipPath"
Write-Output "SHA-256: $hash"
