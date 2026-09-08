$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Create the project virtual environment first.' }

& $python -m pip install --disable-pip-version-check pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed.' }
& $python -m PyInstaller --noconfirm --clean --onedir --windowed --name MemoryHub `
    --paths $root `
    --collect-all pystray `
    --collect-all PIL `
    --add-data "$root\memory_hub\static;memory_hub\static" `
    --distpath "$root\portable" `
    --workpath "$root\.build" `
    --specpath "$root\.build" `
    "$root\memory_hub\desktop_app.py"
if ($LASTEXITCODE -ne 0) { throw 'Portable build failed.' }

Copy-Item -LiteralPath "$root\README.md" -Destination "$root\portable\MemoryHub\README.md" -Force
Copy-Item -LiteralPath "$PSScriptRoot\Start-Portable.cmd" -Destination "$root\portable\MemoryHub\Start-MemoryHub.cmd" -Force
Copy-Item -LiteralPath "$PSScriptRoot\Start-MemoryHub.ps1" -Destination "$root\portable\MemoryHub\Start-MemoryHub.ps1" -Force
Write-Host "Portable build: $root\portable\MemoryHub\MemoryHub.exe"
