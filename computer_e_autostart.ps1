# Register Computer E auto-start for this Windows user at sign-in.
# Does not change the existing PanDoc Computer D worker/task.
[CmdletBinding()]
param(
    [ValidateSet('Install','Status','Start','Remove')]
    [string]$Mode = 'Install',
    [string]$Root = (Join-Path $env:USERPROFILE 'PanDoc-Computer-E')
)
$ErrorActionPreference = 'Stop'
$taskName = 'PanDoc Computer E'
$runner = Join-Path $Root 'source\computer_e_run.ps1'
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if ($Mode -eq 'Remove') {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
    Write-Host "Removed only scheduled task '$taskName'. Computer D untouched."
    exit 0
}
if ($Mode -eq 'Status') {
    Get-ScheduledTask -TaskName $taskName -ErrorAction Stop |
        Select-Object TaskName,State,TaskPath
    Get-ScheduledTaskInfo -TaskName $taskName |
        Select-Object LastRunTime,LastTaskResult,NextRunTime
    Get-NetTCPConnection -LocalPort 8502 -State Listen -ErrorAction SilentlyContinue |
        Select-Object LocalAddress,LocalPort,OwningProcess
    exit 0
}
if ($Mode -eq 'Start') {
    if (Get-NetTCPConnection -LocalPort 8502 -State Listen -ErrorAction SilentlyContinue) {
        Write-Host 'Computer E already listening on port 8502; no duplicate launch.'
        exit 0
    }
    Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
    Write-Host "Start requested for '$taskName'. Check -Mode Status."
    exit 0
}

if (-not (Test-Path -LiteralPath $runner)) { throw "Computer E launcher not found: $runner" }
if (-not (Test-Path -LiteralPath (Join-Path $Root 'conda-env\python.exe'))) {
    throw 'Computer E Conda environment missing. Finish installation first.'
}
$escapedRunner = '"' + $runner.Replace('"','') + '"'
$argument = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File $escapedRunner"
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $argument -WorkingDirectory (Join-Path $Root 'source')
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Computer E full local PanDoc Streamlit runtime on Windows sign-in; independent of Computer D' -Force | Out-Null
Write-Host "Registered '$taskName' for $user at Windows sign-in." -ForegroundColor Green
Write-Host 'It starts at user sign-in, not before sign-in. No password was saved.'
if (Get-NetTCPConnection -LocalPort 8502 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host 'Computer E already listening on 8502; keeping the existing server untouched.'
} else {
    Start-ScheduledTask -TaskName $taskName
    Write-Host 'Start requested now. Check: http://127.0.0.1:8502/_stcore/health'
}
