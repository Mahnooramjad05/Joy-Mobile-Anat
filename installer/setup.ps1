<#
    Registers the daily task and the desktop shortcut.

    Called by Install.bat. Lives in its own file because building the task XML
    and the shortcut from inside a .bat means several layers of nested quoting,
    which is exactly the sort of thing that works on one machine and not the
    next.

        powershell -ExecutionPolicy Bypass -File setup.ps1 -InstallDir <path>
        powershell -ExecutionPolicy Bypass -File setup.ps1 -InstallDir <path> -Remove
#>
param(
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [string]$TaskName = 'KSP Price Update',
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Update KSP prices now.lnk'

function Remove-Everything {
    # Each step is independent: a missing task must not stop the shortcut going.
    try {
        schtasks /Delete /TN $TaskName /F 2>&1 | Out-Null
        Write-Host '  removed the daily schedule'
    } catch {
        Write-Host '  (no daily schedule was registered)'
    }

    if (Test-Path -LiteralPath $shortcutPath) {
        Remove-Item -LiteralPath $shortcutPath -Force -ErrorAction SilentlyContinue
        Write-Host '  removed the desktop shortcut'
    }
}

if ($Remove) {
    Remove-Everything
    exit 0
}

# --- the scheduled task ----------------------------------------------------
$templatePath = Join-Path $InstallDir 'task.xml.template'
if (-not (Test-Path -LiteralPath $templatePath)) {
    Write-Error "task.xml.template is missing from $InstallDir"
    exit 1
}

$userId = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }

$xml = Get-Content -Raw -LiteralPath $templatePath
$xml = $xml.Replace('{{USERID}}',  $userId)
$xml = $xml.Replace('{{COMMAND}}', (Join-Path $InstallDir 'run-sync.bat'))
$xml = $xml.Replace('{{WORKDIR}}', $InstallDir)

# schtasks wants UTF-16. Written to the install folder so it can be inspected
# later, and so an upgrade re-registers from a file that matches this version.
$xmlPath = Join-Path $InstallDir 'task.xml'
Set-Content -LiteralPath $xmlPath -Value $xml -Encoding Unicode

schtasks /Create /TN $TaskName /XML $xmlPath /F | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Windows would not accept the schedule (schtasks exit $LASTEXITCODE)."
    exit 1
}

# --- the desktop shortcut ---------------------------------------------------
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath       = Join-Path $InstallDir 'run-sync.bat'
$shortcut.WorkingDirectory = $InstallDir
$shortcut.IconLocation     = 'shell32.dll,44'
$shortcut.Description      = 'Update the KSP trade-in prices in the Google Sheet now'
$shortcut.Save()

exit 0
