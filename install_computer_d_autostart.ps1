# Install Computer D as an on-logon task under the current Windows account.
# Uses the same user's Windows Credential Manager; no password or GitHub key in the task.
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$worker = Join-Path $dir 'computer_d_worker.py'
if (-not (Test-Path -LiteralPath $worker)) {
    throw "Missing worker: $worker"
}
$python = (Get-Command python.exe -ErrorAction Stop).Source
& $python -m py_compile $worker
if ($LASTEXITCODE -ne 0) { throw 'Worker syntax check failed; task was not registered.' }
$name = 'PanDoc Computer D'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $python -Argument ('-u "' + $worker + '"') -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Installed: $name"
Write-Host "Runs automatically after $identity logs in, including paths with spaces."
Get-ScheduledTask -TaskName $name | Select-Object TaskName,State
