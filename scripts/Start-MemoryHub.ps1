param(
    [switch]$Startup,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$serviceHost = '127.0.0.1'
$servicePort = 17888
$configPath = Join-Path $env:LOCALAPPDATA 'MemoryHub\config.json'
if (Test-Path -LiteralPath $configPath) {
    $config = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($config.port) { $servicePort = [int]$config.port }
    if ($config.host) { $serviceHost = [string]$config.host }
}
if ($serviceHost -eq '0.0.0.0') { $serviceHost = '127.0.0.1' }
if ($serviceHost -eq '::') { $serviceHost = '::1' }
$endpoint = New-Object System.UriBuilder('http', $serviceHost, $servicePort)
$appUrl = $endpoint.Uri.AbsoluteUri
$healthUrl = $appUrl + 'api/health/live'
$logRoot = Join-Path $env:LOCALAPPDATA 'MemoryHub\logs'
$logPath = Join-Path $logRoot 'launcher.log'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

function Write-LauncherLog([string]$Message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $Message
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Test-MemoryHubHealth {
    try {
        $request = [System.Net.HttpWebRequest]::Create($healthUrl)
        $request.Proxy = $null
        $request.Timeout = 2000
        $response = $request.GetResponse()
        try {
            $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
            try { $body = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
            return [int]$response.StatusCode -eq 200 -and $body.status -eq 'ok'
        } finally { $response.Close() }
    } catch {
        return $false
    }
}

function Wait-MemoryHubHealth([int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        if (Test-MemoryHubHealth) { return $true }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $false
}

$portableExe = Join-Path $PSScriptRoot 'MemoryHub.exe'
$isPortable = Test-Path -LiteralPath $portableExe
if ($isPortable) {
    $targetPath = $portableExe
    $targetArguments = @()
    $workingDirectory = $PSScriptRoot
} else {
    $projectRoot = Split-Path -Parent $PSScriptRoot
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    $targetPath = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { 'python.exe' }
    $targetArguments = @('-m', 'memory_hub.cli', 'serve')
    $workingDirectory = $projectRoot
}

if (-not (Test-MemoryHubHealth)) {
    $listener = Get-NetTCPConnection -LocalPort $servicePort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        $owner = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
        $samePortable = $isPortable -and $owner -and $owner.Path -and ([System.IO.Path]::GetFullPath($owner.Path) -eq [System.IO.Path]::GetFullPath($portableExe))
        if ($samePortable) {
            Write-LauncherLog "Unhealthy listener owned by MemoryHub.exe (PID $($owner.Id)); restarting it once."
            Stop-Process -Id $owner.Id -Force
            Start-Sleep -Seconds 1
        } else {
            Write-LauncherLog "Port $servicePort is occupied but health check failed; owner PID $($listener.OwningProcess). No duplicate was started."
            exit 2
        }
    }

    Write-LauncherLog "Starting $targetPath"
    if ($targetArguments.Count -gt 0) {
        Start-Process -FilePath $targetPath -ArgumentList $targetArguments -WorkingDirectory $workingDirectory -WindowStyle Hidden | Out-Null
    } else {
        Start-Process -FilePath $targetPath -WorkingDirectory $workingDirectory -WindowStyle Hidden | Out-Null
    }
    if (-not (Wait-MemoryHubHealth 30)) {
        Write-LauncherLog 'Service did not become reachable in 30 seconds. If other fresh local listeners also fail, restart Windows networking or reboot.'
        exit 3
    }
    Write-LauncherLog 'Service became healthy.'
} else {
    Write-LauncherLog 'Service was already healthy; skipped duplicate launch.'
}

if (-not $Startup -and -not $NoBrowser) {
    Start-Process $appUrl | Out-Null
}
