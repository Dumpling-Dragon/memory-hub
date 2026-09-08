param([switch]$Embed, [int]$BatchSize = 32, [double]$Sleep = 1)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
Push-Location -LiteralPath $PSScriptRoot
try {
    & $python -X utf8 -m memory_hub.cli sync
    if ($LASTEXITCODE -ne 0) { throw "Memory Hub sync failed: $LASTEXITCODE" }
    if ($Embed) {
        & $python -X utf8 -m memory_hub.cli embed --all --limit $BatchSize --pause-seconds ([int]$Sleep)
        if ($LASTEXITCODE -ne 0) { throw "Memory Hub embedding failed: $LASTEXITCODE" }
    }
} finally { Pop-Location }
