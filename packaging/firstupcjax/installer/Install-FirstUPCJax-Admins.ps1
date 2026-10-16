#requires -Version 5.1
#requires -RunAsAdministrator
[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
$Package=Split-Path -Parent $PSScriptRoot
$Root=Join-Path $env:ProgramData 'VisionPC'
try {
    foreach ($File in @('private\pc-config.json','vendor\node-v24.21.0-win-x64.zip','source\vision-pc\Setup-Vision-PC.ps1','source\vision-pc\FirstUPCJax-Admins.ps1')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Package $File) -PathType Leaf)) { throw 'Extract the entire ZIP before running the installer.' }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Root 'config.json'))) { throw 'Run this package on the FUPCJ Server with the existing Vision PC installation.' }
    if ((Get-CimInstance Win32_OperatingSystem).OSArchitecture -notmatch '64') { throw 'This package requires 64-bit Windows.' }
    # Verify the exact packaged files before replacing any installed source.
    foreach ($Line in (Get-Content -LiteralPath (Join-Path $Package 'checksums.sha256'))) {
        if ($Line -notmatch '^([0-9a-f]{64})  (.+)$') { throw 'The package checksum inventory is invalid.' }
        $Expected=$Matches[1]; $Relative=$Matches[2]
        $Item=[IO.Path]::GetFullPath((Join-Path $Package $Relative))
        if (-not $Item.StartsWith([IO.Path]::GetFullPath($Package)+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid package path.' }
        if ((Get-FileHash -LiteralPath $Item -Algorithm SHA256).Hash -ne $Expected) { throw "Package verification failed: $Relative" }
    }
    $Source=Join-Path $Package 'source\vision-pc'
    # Pin the bundled processor revision. The updater accepts revision names
    # without path separators; a development branch name is not a valid input.
    & (Join-Path $Source 'Setup-Vision-PC.ps1') -Action Update -SourceDirectory $Source -SourceRef '065c7d3541137fc9e724cf60b8c10761a6ac2ecc'
    if ($LASTEXITCODE -ne 0) { throw 'Vision source update failed. Check the retained processor backup.' }
    & (Join-Path $Root 'FirstUPCJax-Admins.ps1') -Action Setup -PrivateConfiguration (Join-Path $Package 'private\pc-config.json') -NodeArchive (Join-Path $Package 'vendor\node-v24.21.0-win-x64.zip')
    if (-not $?) { throw 'FirstUPCJax Admins setup did not finish.' }
    $Shell=New-Object -ComObject WScript.Shell
    $Shortcut=$Shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'FirstUPCJax Admins - Planning Center Sign In.lnk'))
    $Shortcut.TargetPath=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $Shortcut.Arguments='-NoLogo -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell.exe -Verb RunAs -ArgumentList (''-NoLogo -NoProfile -ExecutionPolicy Bypass -File ''+[char]34+'''+(Join-Path $Root 'FirstUPCJax-Admins.ps1')+'''+[char]34+'' -Action Login'')"'
    $Shortcut.WorkingDirectory=$Root; $Shortcut.Save()
    Write-Host 'Installation checks passed. Open the Planning Center Sign In shortcut once on this PC.' -ForegroundColor Green
    Write-Host 'Then open Vision > FirstUPCJax Admins and connect the packaged plugin in ChatGPT.'
    Read-Host 'Press Enter to close'
    exit 0
} catch {
    Write-Host ('Installation did not complete: '+$_.Exception.Message) -ForegroundColor Red
    Write-Host ('Server log: '+(Join-Path $Root 'data\server.log'))
    Read-Host 'Press Enter to close'
    exit 1
}
