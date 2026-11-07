# Full PanDoc local Streamlit instance in its own Conda environment.
[CmdletBinding()]
param([string]$Root = (Join-Path $env:USERPROFILE "PanDoc-Computer-E"),[int]$Port = 8502)
$ErrorActionPreference = "Stop"
$source = Join-Path $Root "source"
$python = Join-Path $Root "conda-env\python.exe"
if (-not (Test-Path $python)) { throw "Computer E Conda environment missing: run computer_e_install.ps1 first." }
if (-not (Test-Path (Join-Path $source "app.py"))) { throw "PanDoc source is missing." }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port already in use."
}
$env:PANDOC_DATA_DIR = Join-Path $Root "data"
$env:PANDOC_RUNTIME_NAME = "Computer E"
$vinaPathFile = Join-Path $Root "vina_path.txt"
if (Test-Path $vinaPathFile) {
    $candidateVina = (Get-Content $vinaPathFile -TotalCount 1).Trim()
    if ($candidateVina -and (Test-Path $candidateVina)) {
        $env:PANDOC_VINA_EXE = $candidateVina
        Write-Host "Computer E Vina executable: $candidateVina"
    } else {
        Write-Warning "Saved Vina executable not found. Docking via native CLI is unavailable."
    }
} else {
    Write-Warning "Vina executable is not configured. Docking needs PANDOC_VINA_EXE."
}
$env:STREAMLIT_SERVER_HEADLESS = "true"
New-Item -ItemType Directory -Force -Path $env:PANDOC_DATA_DIR | Out-Null
Set-Location $source
Write-Host "Starting Computer E at http://127.0.0.1:$Port" -ForegroundColor Green
& $python -m streamlit run app.py --server.address 127.0.0.1 --server.port $Port --server.headless true --browser.gatherUsageStats false
if ($LASTEXITCODE -ne 0) { throw "Computer E failed with exit code $LASTEXITCODE" }
