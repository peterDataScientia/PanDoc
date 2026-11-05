$dir = "$env:USERPROFILE\PanDoc-Vina-Worker"
$file = Join-Path $dir "computer_d_worker.py"
$backup = Join-Path $dir "computer_d_worker_backup.py"

# Stop the existing worker
schtasks /End /TN "PanDoc Computer D"

Start-Sleep -Seconds 3

# Preserve old worker
Copy-Item $file $backup -Force

# Download updated worker
curl.exe -fL --retry 5 --retry-delay 5 `
  -o "$file.new" `
  "https://raw.githubusercontent.com/peterDataScientia/PanDoc/main/computer_d_worker.py"

if ($LASTEXITCODE -ne 0) {
    throw "Download failed. Backup preserved."
}

# Verify Python syntax before installing
& "C:\Program Files\Python313\python.exe" -m py_compile "$file.new"

if ($LASTEXITCODE -ne 0) {
    throw "Syntax check failed. Original worker preserved."
}

# Install and restart
Move-Item "$file.new" $file -Force

schtasks /Run /TN "PanDoc Computer D"

Start-Sleep -Seconds 5

Get-CimInstance Win32_Process |
Where-Object { $_.CommandLine -like "*computer_d_worker.py*" } |
Select-Object ProcessId, CommandLine
