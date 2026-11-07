# Computer E full local PanDoc runtime — isolated Conda environment.
# No changes to Computer D or system Python.
[CmdletBinding()]
param([string]$Root = (Join-Path $env:USERPROFILE "PanDoc-Computer-E"))
$ErrorActionPreference = "Stop"
$repoUrl = "https://github.com/peterDataScientia/PanDoc.git"
$conda = $null
$command = Get-Command conda.exe -ErrorAction SilentlyContinue
if ($command) { $conda = $command.Source }
if (-not $conda) {
    foreach ($candidate in @(
        (Join-Path $env:USERPROFILE "miniconda3\Scripts\conda.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\Scripts\conda.exe"),
        (Join-Path $env:LOCALAPPDATA "miniconda3\Scripts\conda.exe"),
        "C:\ProgramData\miniconda3\Scripts\conda.exe",
        "C:\ProgramData\anaconda3\Scripts\conda.exe",
        "C:\Miniconda3\Scripts\conda.exe"
    )) {
        if (Test-Path $candidate) { $conda = $candidate; break }
    }
}
if (-not $conda) {
    throw "Conda not found. Install Miniforge or Miniconda (free), reopen PowerShell and rerun. Computer D was not modified."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git is required." }
$source = Join-Path $Root "source"
$envPath = Join-Path $Root "conda-env"
$data = Join-Path $Root "data"
$logs = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $Root,$data,$logs | Out-Null
if (Test-Path (Join-Path $source ".git")) {
    Write-Host "Existing source preserved; use git pull explicitly to update it."
} elseif (Test-Path $source) {
    throw "Source directory exists but is not a Git repository."
} else {
    & git clone --depth 1 $repoUrl $source
    if ($LASTEXITCODE -ne 0) { throw "Git clone failed." }
}
$python = Join-Path $envPath "python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Creating isolated Python 3.11 Conda environment..."
    & $conda create --prefix $envPath python=3.11 pip -y
    if ($LASTEXITCODE -ne 0) { throw "Conda environment creation failed." }
}
& $python --version
if ($LASTEXITCODE -ne 0) { throw "Conda Python is not working." }
& $python -m pip install -r (Join-Path $source "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "PanDoc requirements failed; inspect the package error. Computer D is unchanged."
}
& $python -c "import streamlit, rdkit, meeko, vina, openmm, pdbfixer, propka; print('Computer E core imports passed')"
if ($LASTEXITCODE -ne 0) { throw "Core scientific import verification failed." }
Write-Host "Computer E Conda environment installed: $envPath" -ForegroundColor Green
Write-Host "Start: & `"$source\computer_e_run.ps1`""
Write-Host "Computer D worker remains unchanged."
