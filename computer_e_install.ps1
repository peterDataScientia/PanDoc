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
        (Join-Path $env:USERPROFILE "miniforge3\Scripts\conda.exe"),
        "C:\Miniforge3\Scripts\conda.exe",
        "C:\conda\Scripts\conda.exe",
        "C:\conda\condabin\conda.bat",
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
    Write-Host "Updating existing Computer E source (fast-forward only)..."
    & git -C $source pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        throw "Computer E source has local modifications or cannot fast-forward. Resolve manually; other workers are unchanged."
    }
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
# The Vina Python binding has no Windows 1.2.7 wheel.
# Computer E uses the existing Windows Vina executable instead.
$requirements = Join-Path $source "requirements.txt"
$windowsRequirements = Join-Path $Root "requirements-windows.txt"
Get-Content -LiteralPath $requirements |
    Where-Object {
        ($_ -notmatch '^\s*--only-binary=vina\s*$') -and
        ($_ -notmatch '^\s*vina==1\.2\.7\s*$')
    } |
    ForEach-Object { $_ -replace '^streamlit==1\\.66\\.1& $python -m pip install -r $windowsRequirements
if ($LASTEXITCODE -ne 0) {
    throw "PanDoc Windows requirements failed; inspect the package error. Computer D is unchanged."
}
& $python -c "import streamlit, rdkit, meeko, openmm, pdbfixer, propka; print('Computer E core imports passed')"
if ($LASTEXITCODE -ne 0) { throw "Core scientific import verification failed." }

$vinaExe = if ($env:PANDOC_VINA_EXE -and (Test-Path $env:PANDOC_VINA_EXE)) {
    $env:PANDOC_VINA_EXE
} else {
    @(
        "C:\Program Files (x86)\vina_1.2\vina.exe",
        "C:\Program Files\vina\vina.exe",
        (Join-Path $Root "tools\vina.exe")
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $vinaExe) {
    Write-Warning "Vina CLI not found. Other modules may install, but docking is not ready. Set PANDOC_VINA_EXE to a valid Vina executable before launching Computer E."
} else {
    Write-Host "Computer E will use Vina executable: $vinaExe" -ForegroundColor Cyan
    Set-Content -Path (Join-Path $Root "vina_path.txt") -Value $vinaExe -Encoding UTF8
}
Write-Host "Computer E Conda environment installed: $envPath" -ForegroundColor Green
Write-Host "Start: & `"$source\computer_e_run.ps1`""
Write-Host "Computer D worker remains unchanged."
, 'streamlit==1.66.0' } |
    Set-Content -LiteralPath $windowsRequirements -Encoding UTF8
& $python -m pip install -r $windowsRequirements
if ($LASTEXITCODE -ne 0) {
    throw "PanDoc Windows requirements failed; inspect the package error. Computer D is unchanged."
}
& $python -c "import streamlit, rdkit, meeko, openmm, pdbfixer, propka; print('Computer E core imports passed')"
if ($LASTEXITCODE -ne 0) { throw "Core scientific import verification failed." }

$vinaExe = if ($env:PANDOC_VINA_EXE -and (Test-Path $env:PANDOC_VINA_EXE)) {
    $env:PANDOC_VINA_EXE
} else {
    @(
        "C:\Program Files (x86)\vina_1.2\vina.exe",
        "C:\Program Files\vina\vina.exe",
        (Join-Path $Root "tools\vina.exe")
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $vinaExe) {
    Write-Warning "Vina CLI not found. Other modules may install, but docking is not ready. Set PANDOC_VINA_EXE to a valid Vina executable before launching Computer E."
} else {
    Write-Host "Computer E will use Vina executable: $vinaExe" -ForegroundColor Cyan
    Set-Content -Path (Join-Path $Root "vina_path.txt") -Value $vinaExe -Encoding UTF8
}
Write-Host "Computer E Conda environment installed: $envPath" -ForegroundColor Green
Write-Host "Start: & `"$source\computer_e_run.ps1`""
Write-Host "Computer D worker remains unchanged."
