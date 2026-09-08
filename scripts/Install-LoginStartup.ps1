param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$taskName = 'MemoryHub'
$startupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'MemoryHub.lnk'
$portableLauncher = Join-Path $root 'portable\MemoryHub\Start-MemoryHub.cmd'
$sourceLauncher = Join-Path $root 'scripts\Start-MemoryHub.cmd'
$launcher = if (Test-Path -LiteralPath $portableLauncher) { $portableLauncher } else { $sourceLauncher }
if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $startupLink -Force -ErrorAction SilentlyContinue
    Write-Host 'Removed Memory Hub login startup.'
    exit 0
}
$launcherArguments = ('/d /c call "{0}" -Startup' -f $launcher)
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $launcherArguments
$trigger = New-ScheduledTaskTrigger -AtLogOn
try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Description "Start local Memory Hub at user logon ($launcher)" -Force -ErrorAction Stop | Out-Null
    Write-Host "Installed scheduled task $taskName using $launcher. Remove with: .\Install-LoginStartup.ps1 -Remove"
} catch {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($startupLink)
    $shortcut.TargetPath = "$env:ComSpec"
    $shortcut.Arguments = $launcherArguments
    $shortcut.WorkingDirectory = Split-Path -Parent $launcher
    $shortcut.WindowStyle = 7
    $shortcut.Description = 'Start local Memory Hub at user logon'
    $shortcut.Save()
    Write-Host "Scheduled task was unavailable; installed Startup shortcut: $startupLink"
}
