param(
    [Parameter(Mandatory=$true, Position=0)][string]$Query,
    [int]$Top = 8,
    [string]$Owner = '',
    [string]$Platform = '',
    [switch]$NoRerank,
    [switch]$KeywordOnly,
    [switch]$Json
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$arguments = @('-X', 'utf8', '-m', 'memory_hub.cli', 'search', $Query, '--limit', [string]$Top)
if ($Owner) { $arguments += @('--owner', $Owner) }
if ($Platform) { $arguments += @('--source', $Platform) }
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
Push-Location -LiteralPath $PSScriptRoot
try {
    # Local CLI remains available even if the desktop service is stopped.
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "Memory Hub search failed: $LASTEXITCODE" }
} finally { Pop-Location }
