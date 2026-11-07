# Computer E - independent full PanDoc runtime installer.
# Run in an ordinary PowerShell window on the machine hosting Computer D.
# Does not change the existing Computer D worker or scheduled task.
[CmdletBinding()]
param(
    [string]$Root = (Join-Path $env:USERPROFILE "PanDoc-Computer-E")
)
$ErrorActionPreference = "Stop"
$repoUrl = "https://github.com/peterDataScientia/PanDoc.git"
# Detect Python 3.11 without letting PowerShell treat py.exe's error as a script failure.
$python = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $candidate = & py -3.11 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $candidate) { $python = ($candidate | Select-Object -First 1).Trim() }
}
if (-not $python) {
    $locations = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        "C:\Program Files\Python311\python.exe",
        "C:\Python311\python.exe"
    )
    foreach ($location in $locations) {
        if (Test-Path $location) { $python = $location; break }
    }
}
if (-not $python) {
    throw "Python 3.11 was not found. Run: winget install --id Python.Python.3.11 --exact --source winget --accept-package-agreements --accept-source-agreements ; then reopen PowerShell and rerun this installer. Python 3.13 and Computer D remain unchanged."
}
Write-Host "Computer E Python: $python" -ForegroundColor Cyan
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required to install the PanDoc repository."
}
$source = Join-Path $Root "source"
$venv = Join-Path $Root ".venv"
$data = Join-Path $Root "data"
$logs = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $Root,$data,$logs | Out-Null
if (Test-Path (Join-Path $source ".git")) {
    Write-Host "Repository already exists. Preserving it; update explicitly with git pull if desired."
} elseif (Test-Path $source) {
    throw "Source folder exists without a git repository: $source"
} else {
    & git clone --depth 1 $repoUrl $source
    if ($LASTEXITCODE -ne 0) { throw "PanDoc clone failed" }
}
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    & $python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Computer E venv creation failed" }
}
$venvPython = Join-Path $venv "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
& $venvPython -m pip install -r (Join-Path $source "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed; Computer D is untouched" }
& $venvPython -c "import streamlit,rdkit,meeko,vina,openmm,pdbfixer,propka; print('Computer E core packages OK')"
if ($LASTEXITCODE -ne 0) { throw "Computer E dependency verification failed" }
Write-Host ""
Write-Host "Computer E installed in $Root" -ForegroundColor Green
Write-Host "Launch: powershell -ExecutionPolicy Bypass -File `"$source\computer_e_run.ps1`" -Root `"$Root`""
Write-Host "Then, in an Administrator terminal (private tailnet only): tailscale serve --bg 8502"
Write-Host "Existing Computer D scheduled task and worker were not changed."
