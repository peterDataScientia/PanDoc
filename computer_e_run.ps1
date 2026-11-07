# Start a full independent PanDoc instance for Computer E.
[CmdletBinding()]
param([string]$Root = (Join-Path $env:USERPROFILE "PanDoc-Computer-E"),[int]$Port=8502)
$ErrorActionPreference = "Stop"
$source = Join-Path $Root "source"
$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Computer E not installed. Run computer_e_install.ps1 first." }
if (-not (Test-Path (Join-Path $source "app.py"))) { throw "PanDoc app.py is missing." }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already occupied. Use a different -Port and matching Tailscale Serve target."
}
$env:PANDOC_DATA_DIR = Join-Path $Root "data"
$env:PANDOC_RUNTIME_NAME = "Computer E"
$env:STREAMLIT_SERVER_HEADLESS = "true"
New-Item -ItemType Directory -Force -Path $env:PANDOC_DATA_DIR | Out-Null
Set-Location $source
Write-Host "Computer E Full PanDoc starting at http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "This instance runs all app operations locally on this PC."
Write-Host "Leave this process running; close with Ctrl+C. Computer D worker is independent."
& $python -m streamlit run app.py --server.address 127.0.0.1 --server.port $Port --server.headless true --browser.gatherUsageStats false
if ($LASTEXITCODE -ne 0) { throw "Computer E Streamlit exited with code $LASTEXITCODE" }
