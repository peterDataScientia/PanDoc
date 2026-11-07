# Secure online access to Computer E through Tailscale Serve.
# No public Funnel, firewall ports, or modifications to Computer D.
[CmdletBinding()]
param([ValidateSet('Enable','Status','Disable')][string]$Mode='Enable')
$ErrorActionPreference = 'Stop'

$tailscale = (Get-Command tailscale.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
if (-not $tailscale) {
    $candidate = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
    if (Test-Path $candidate) { $tailscale = $candidate }
}
if (-not $tailscale) { throw 'Tailscale CLI not found. Install and sign in to Tailscale on Computer E.' }

if ($Mode -eq 'Status') {
    & $tailscale serve status
    & $tailscale status
    exit $LASTEXITCODE
}
if ($Mode -eq 'Disable') {
    # Only the HTTPS:443 Serve endpoint; do not reset unrelated services.
    & $tailscale serve --https=443 off
    if ($LASTEXITCODE -ne 0) { throw 'Failed to disable Serve port 443.' }
    & $tailscale serve status
    exit 0
}
$health = 'http://127.0.0.1:8502/_stcore/health'
try {
    $response = Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 8
} catch {
    throw "Computer E is not reachable on localhost:8502. Check the Windows scheduled task first. $($_.Exception.Message)"
}
if ($response.StatusCode -ne 200 -or $response.Content.Trim() -ne 'ok') {
    throw "Computer E health returned unexpected response: $($response.StatusCode)"
}

Write-Host 'Local Computer E is healthy.' -ForegroundColor Green
Write-Host 'Enabling private HTTPS; access requires authorization in your Tailscale network.'
& $tailscale serve --bg --https=443 http://127.0.0.1:8502
if ($LASTEXITCODE -ne 0) { throw 'Tailscale Serve failed. If prompted, enable HTTPS in your Tailscale admin console and retry.' }
& $tailscale serve status
Write-Host 'Open the HTTPS URL listed above on another device signed into the same tailnet.' -ForegroundColor Green
