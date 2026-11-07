# Computer E outbound remote dock worker; no public listener.
[CmdletBinding()]
param([string]$Root = (Join-Path $env:USERPROFILE 'PanDoc-Computer-E'))
$ErrorActionPreference = 'Stop'
$python = Join-Path $Root 'conda-env\python.exe'
$worker = Join-Path $Root 'source\computer_e_worker.py'
$log = Join-Path $Root 'computer_e_remote.log'
if (-not (Test-Path $python)) { throw "Python missing: $python" }
if (-not (Test-Path $worker)) { throw "Worker missing: $worker" }
$vinaFile = Join-Path $Root 'vina_path.txt'
if (Test-Path $vinaFile) {
    $env:PANDOC_VINA_EXE = (Get-Content $vinaFile -TotalCount 1).Trim()
}
$env:PANDOC_E_WORK_DIR = Join-Path $Root 'remote-worker'
Set-Location (Join-Path $Root 'source')
"=== Computer E remote worker started: $(Get-Date -Format o) ===" | Out-File -FilePath $log -Append -Encoding utf8
& $python -u $worker *>> $log
if ($LASTEXITCODE -ne 0) {
    "Computer E remote worker stopped with exit code $LASTEXITCODE" | Out-File -FilePath $log -Append -Encoding utf8
    exit $LASTEXITCODE
}
