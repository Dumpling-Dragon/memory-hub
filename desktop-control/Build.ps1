param([switch]$InstallShortcut)
$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$outDir = Join-Path $PSScriptRoot 'bin'
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$exe = Join-Path $outDir 'MemoryHubControl.exe'
$iconPath = Join-Path $outDir 'MemoryHub.ico'

Add-Type -AssemblyName System.Drawing
$bitmap = New-Object System.Drawing.Bitmap 64,64
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.Clear([System.Drawing.ColorTranslator]::FromHtml('#FBF8F1'))
$brush = New-Object System.Drawing.SolidBrush ([System.Drawing.ColorTranslator]::FromHtml('#8B3A32'))
$font = New-Object System.Drawing.Font 'Georgia',38,([System.Drawing.FontStyle]::Bold)
$graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$graphics.DrawString('M', $font, $brush, 1, 0)
$graphics.FillRectangle($brush, 5, 57, 54, 3)
$icon = [System.Drawing.Icon]::FromHandle($bitmap.GetHicon())
$stream = [System.IO.File]::Create($iconPath)
try { $icon.Save($stream) } finally { $stream.Dispose(); $icon.Dispose(); $font.Dispose(); $brush.Dispose(); $graphics.Dispose(); $bitmap.Dispose() }

& $compiler /nologo /target:winexe /platform:anycpu /optimize+ /codepage:65001 "/out:$exe" "/win32icon:$iconPath" "/win32manifest:$(Join-Path $PSScriptRoot 'app.manifest')" /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll /reference:System.Net.Http.dll /reference:System.Web.Extensions.dll (Join-Path $PSScriptRoot 'MemoryHubControl.cs')
if ($LASTEXITCODE -ne 0) { throw 'Native control build failed.' }
if ($InstallShortcut) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktop 'Memory Hub.lnk'
    if (Test-Path -LiteralPath $shortcutPath) {
        throw 'Desktop shortcut already exists; inspect it before replacing.'
    }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $exe
    $shortcut.WorkingDirectory = $outDir
    $shortcut.IconLocation = $exe + ',0'
    $shortcut.Description = 'Memory Hub - desktop control'
    $shortcut.Save()
    Write-Output $shortcutPath
}
Get-Item -LiteralPath $exe | Select-Object FullName,Length
