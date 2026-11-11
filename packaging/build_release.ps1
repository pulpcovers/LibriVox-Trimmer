# Builds the Windows app, portable ZIP, per-user installer, and SHA-256 checksums.
# Usage: .\packaging\build_release.ps1 [-Python path\to\python.exe] [-SkipInstaller]
# Requires app dependencies, PyInstaller, and NSIS (unless -SkipInstaller is used).
param(
    [string]$Python = "python",
    [string]$MakeNSIS = "",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = (Select-String -Path "pyproject.toml" -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$name = "LibriVox Trimmer"
$zipPath = Join-Path $root "dist\LibriVox-Trimmer-$version-windows.zip"
$installerPath = Join-Path $root "dist\LibriVox-Trimmer-$version-windows-setup.exe"

if (-not $SkipInstaller) {
    if (-not $MakeNSIS) {
        $compiler = Get-Command makensis.exe -ErrorAction SilentlyContinue
        if ($compiler) {
            $MakeNSIS = $compiler.Source
        } else {
            $MakeNSIS = Join-Path ${env:ProgramFiles(x86)} "NSIS\makensis.exe"
        }
    }
    if (-not (Test-Path -LiteralPath $MakeNSIS -PathType Leaf)) {
        throw "NSIS compiler not found. Install NSIS, pass -MakeNSIS, or use -SkipInstaller."
    }
}

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

if (-not $SkipInstaller) {
    Remove-Item -LiteralPath $installerPath, "$installerPath.sha256" -ErrorAction SilentlyContinue
    & $MakeNSIS "/DAPP_VERSION=$version" "/DPAYLOAD_DIR=$root\dist\$name" "/DOUTPUT_FILE=$installerPath" "$PSScriptRoot\installer.nsi"
    if ($LASTEXITCODE -ne 0) { throw "NSIS failed with exit code $LASTEXITCODE" }
    $installerHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installerPath).Hash.ToLower()
    "$installerHash  $(Split-Path -Leaf $installerPath)" | Set-Content -Encoding ascii "$installerPath.sha256"
    Write-Output "Built $installerPath"
    Write-Output "SHA-256: $installerHash"
}
