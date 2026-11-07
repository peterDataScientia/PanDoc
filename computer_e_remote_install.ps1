# Install outbound Computer E online docking worker; do not change Computer D.
[CmdletBinding()]
param([ValidateSet('Install','Status','Remove')][string]$Mode='Install',
      [string]$Root=(Join-Path $env:USERPROFILE 'PanDoc-Computer-E'))
$ErrorActionPreference='Stop'
$task='PanDoc Computer E Remote Worker'
$python=Join-Path $Root 'conda-env\python.exe'
$worker=Join-Path $Root 'source\computer_e_worker.py'
$runner=Join-Path $Root 'source\computer_e_worker_run.ps1'

if ($Mode -eq 'Remove') {
    Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction Stop
    Write-Host "Only '$task' removed."
    exit 0
}
if ($Mode -eq 'Status') {
    Get-ScheduledTask -TaskName $task -ErrorAction Stop | Select-Object TaskName,State
    Get-ScheduledTaskInfo -TaskName $task | Select-Object LastRunTime,LastTaskResult
    $log=Join-Path $Root 'computer_e_remote.log'
    if (Test-Path $log) { Get-Content $log -Tail 25 }
    exit 0
}
foreach ($file in @($python,$worker,$runner)) {
    if (-not (Test-Path $file)) { throw "Missing $file; run git pull first." }
}
$vinaFile=Join-Path $Root 'vina_path.txt'
if (Test-Path $vinaFile) {
    $env:PANDOC_VINA_EXE=(Get-Content $vinaFile -TotalCount 1).Trim()
} else {
    $env:PANDOC_VINA_EXE='C:\Program Files (x86)\vina_1.2\vina.exe'
}
if (-not (Test-Path $env:PANDOC_VINA_EXE)) { throw "Vina executable missing: $env:PANDOC_VINA_EXE" }
& $python -m pip install 'keyring>=25,<27'
if ($LASTEXITCODE -ne 0) { throw 'Could not install Windows Credential Manager support (keyring).' }
& $python -m py_compile $worker
if ($LASTEXITCODE -ne 0) { throw 'Worker syntax check failed.' }
Write-Host 'Verifying remote-worker credentials (may request hidden GitHub token and encryption key once)...'
Push-Location (Join-Path $Root 'source')
try {
    & $python $worker --check-credentials
    if ($LASTEXITCODE -ne 0) { throw 'Computer E credentials not ready. Scheduled task was not installed.' }
} finally { Pop-Location }
$user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$arg='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$runner+'"'
$action=New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $arg -WorkingDirectory (Join-Path $Root 'source')
$trigger=New-ScheduledTaskTrigger -AtLogOn -User $user
$principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force -Description 'Computer E encrypted outbound docking worker for online PanDoc' | Out-Null
Start-ScheduledTask -TaskName $task
Write-Host 'Computer E remote worker task installed and started.' -ForegroundColor Green
Write-Host 'Check with: schtasks /Query /TN "PanDoc Computer E Remote Worker" /V /FO LIST'
Write-Host "Check logs at: $Root\computer_e_remote.log"
