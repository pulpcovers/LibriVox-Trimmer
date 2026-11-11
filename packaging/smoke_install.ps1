param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [Parameter(Mandatory = $true)][string]$Version
)

$ErrorActionPreference = "Stop"
if ($env:CI -ne "true") { throw "Run installer smoke tests only on a disposable CI runner." }
$installDir = Join-Path $env:LOCALAPPDATA "Programs\LibriVox Trimmer"
$dataDir = Join-Path $env:LOCALAPPDATA "LibriVoxTrimmer"
$registry = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LibriVoxTrimmer"
if ((Test-Path -LiteralPath $installDir) -or (Test-Path -LiteralPath $dataDir) -or (Test-Path -LiteralPath $registry)) {
    throw "Installer smoke tests require a clean user profile; existing data must not be touched."
}
New-Item -ItemType Directory -Path $dataDir | Out-Null
$sentinel = Join-Path $dataDir "installer-preservation-test.txt"
Set-Content -LiteralPath $sentinel -Value "Keep user projects and models" -Encoding ascii

foreach ($attempt in 1..2) {
    $process = Start-Process -FilePath $InstallerPath -ArgumentList "/S" -PassThru -Wait
    if ($process.ExitCode -ne 0) { throw "Installer failed with exit code $($process.ExitCode)" }
    if (-not (Test-Path -LiteralPath "$installDir\LibriVox Trimmer.exe")) { throw "Installed executable is missing." }
    if (-not (Test-Path -LiteralPath "$installDir\_internal")) { throw "Installed dependencies are missing." }
    if (-not (Test-Path -LiteralPath "$installDir\.librivox-trimmer-install")) { throw "Installer ownership marker is missing." }
    $registration = Get-ItemProperty -LiteralPath $registry
    if ($registration.DisplayVersion -ne $Version) { throw "Installed version is incorrect." }
    if (-not (Test-Path -LiteralPath $sentinel)) { throw "Installation removed user data." }
    Write-Output "Install/upgrade pass $attempt succeeded."
}

$process = Start-Process -FilePath "$installDir\Uninstall.exe" -ArgumentList "/S _?=$installDir" -PassThru -Wait
if ($process.ExitCode -ne 0) { throw "Uninstaller failed with exit code $($process.ExitCode)" }
if ((Test-Path -LiteralPath "$installDir\LibriVox Trimmer.exe") -or (Test-Path -LiteralPath $registry)) {
    throw "Uninstallation did not remove the application and its registration."
}
if (-not (Test-Path -LiteralPath $sentinel)) { throw "Uninstallation removed user data." }
Write-Output "Uninstall succeeded and user data was preserved."