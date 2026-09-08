[CmdletBinding()]
param(
    [switch]$SkipGeminiExport,
    [switch]$SkipYuanbaoExport,
    [switch]$GeminiOnly,
    [switch]$SkipEmbedding,
    [switch]$SkipAudit,
    [int]$BatchSize = 32,
    [double]$Sleep = 0.1,
    [int]$GeminiPort = 9222,
    [int]$YuanbaoPort = 9223,
    [switch]$PauseOnExit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$ProjectRoot = $PSScriptRoot
$TaskRoot = Split-Path -Parent $ProjectRoot
$GeminiRoot = Join-Path $TaskRoot "2026-06-08_gemini聊天批量导出"
$YuanbaoRoot = Join-Path $TaskRoot "2026-06-08_yuanbao聊天批量导出"
$ConfigPath = Join-Path $env:LOCALAPPDATA 'MemoryHub\config.json'
if (Test-Path -LiteralPath $ConfigPath) {
    $RuntimeConfig = [System.IO.File]::ReadAllText($ConfigPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($RuntimeConfig.PSObject.Properties.Name -contains 'web_export_roots') {
        $ExportRoots = $RuntimeConfig.web_export_roots
        if ($ExportRoots -and $ExportRoots.PSObject.Properties.Name -contains 'gemini' -and $ExportRoots.gemini) { $GeminiRoot = $ExportRoots.gemini }
        if ($ExportRoots -and $ExportRoots.PSObject.Properties.Name -contains 'yuanbao' -and $ExportRoots.yuanbao) { $YuanbaoRoot = $ExportRoots.yuanbao }
    }
}
$LogsDir = Join-Path $ProjectRoot "logs"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogsDir "refresh-web-memory_$Timestamp.log"

New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Test-CdpEndpoint {
    param([int]$Port)
    foreach ($hostName in @("127.0.0.1", "localhost")) {
        $uri = "http://$hostName`:$Port/json/version"
        try {
            $response = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                return $uri
            }
        } catch {
            continue
        }
    }
    return $null
}

function Invoke-Python {
    param([string[]]$Arguments)
    & (Join-Path $ProjectRoot '.venv\Scripts\python.exe') -X utf8 @Arguments
    if ($LASTEXITCODE -ne 0) {
        $argumentText = $Arguments -join " "
        throw "python failed with exit code ${LASTEXITCODE}: $argumentText"
    }
}

function Invoke-NodeScript {
    param(
        [string]$WorkingDirectory,
        [string[]]$Arguments
    )
    Push-Location -LiteralPath $WorkingDirectory
    try {
        & node @Arguments
        if ($LASTEXITCODE -ne 0) {
            $argumentText = $Arguments -join " "
            throw "node failed with exit code ${LASTEXITCODE}: $argumentText"
        }
    } finally {
        Pop-Location
    }
}

try {
    Start-Transcript -LiteralPath $LogPath -Force | Out-Null
    Set-Location -LiteralPath $ProjectRoot

    Write-Step "Checking prerequisites"
    $pythonCmd = Get-Item -LiteralPath (Join-Path $ProjectRoot '.venv\Scripts\python.exe')
    $nodeCmd = Get-Command node -ErrorAction Stop
    Write-Host "python: $($pythonCmd.FullName)"
    Write-Host "node: $($nodeCmd.Source)"


    Write-Step "Current status"
    Invoke-Python @("-c", "from memory_hub.db import embedding_status; print(embedding_status('BAAI/bge-m3'))")

    if ($SkipGeminiExport) {
        Write-Step "Skipping Gemini browser export"
    } elseif ($GeminiEndpoint = Test-CdpEndpoint -Port $GeminiPort) {
        Write-Step "Incrementally exporting Gemini browser chats"
        $out = Join-Path $GeminiRoot "browser-export-json-md"
        Invoke-NodeScript -WorkingDirectory $GeminiRoot -Arguments @(
            ".\scripts\export-gemini.mjs",
            "--endpoint", $GeminiEndpoint -replace "/json/version$", "",
            "--output", $out,
            "--no-html"
        )
        Invoke-NodeScript -WorkingDirectory $GeminiRoot -Arguments @(
            ".\scripts\rename-export-files.mjs",
            "--output", $out,
            "--endpoint", $GeminiEndpoint -replace "/json/version$", ""
        )
    } else {
        Write-Host "Gemini Chrome CDP endpoint not available on 127.0.0.1:$GeminiPort; keeping existing Gemini export." -ForegroundColor Yellow
    }

    if ($GeminiOnly -or $SkipYuanbaoExport) {
        Write-Step "Skipping Yuanbao browser export"
    } elseif ($YuanbaoEndpoint = Test-CdpEndpoint -Port $YuanbaoPort) {
        Write-Step "Incrementally exporting Yuanbao browser chats"
        $out = Join-Path $YuanbaoRoot "browser-export-json-md"
        Invoke-NodeScript -WorkingDirectory $YuanbaoRoot -Arguments @(
            ".\scripts\export-yuanbao.mjs",
            "--endpoint", $YuanbaoEndpoint -replace "/json/version$", "",
            "--output", $out,
            "--no-html"
        )
    } else {
        Write-Host "Yuanbao Chrome CDP endpoint not available on 127.0.0.1:$YuanbaoPort; keeping existing Yuanbao export." -ForegroundColor Yellow
    }

    Write-Step "Classifying web conversations by owner/topic"
    $classificationArgs = @(
        "-m", "memory_hub.classify_web",
        "--report", ".\logs\web-classification-report-latest.json"
    )
    if ($GeminiOnly) {
        $classificationArgs += @("--platform", "gemini")
    }
    Invoke-Python $classificationArgs

    Write-Step "Syncing the unified Memory Hub"
    & (Join-Path $ProjectRoot 'sync-memory.ps1') -Embed:(-not $SkipEmbedding) -BatchSize $BatchSize -Sleep $Sleep

    Write-Step "Final status"
    Invoke-Python @("-c", "from memory_hub.db import embedding_status; print(embedding_status('BAAI/bge-m3'))")

    Write-Host ""
    Write-Host "Web memory refresh completed successfully." -ForegroundColor Green
    Write-Host "Log: $LogPath"
    Write-Host "Classification report: $(Join-Path $LogsDir 'web-classification-report-latest.json')"
}
catch {
    Write-Host ""
    Write-Host "Web memory refresh failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Log: $LogPath"
    exit 1
}
finally {
    try { Stop-Transcript | Out-Null } catch {}
    if ($PauseOnExit) {
        Write-Host ""
        Read-Host "Press Enter to close"
    }
}
