#requires -Version 5.1
[CmdletBinding()]
param([switch]$RealBrowser)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $Repo 'vision-pc'
$Python = (Get-Command python.exe).Source
$Tokens = $null; $ParseErrors = $null
$Ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $Source 'FirstUPCJax-Admins.ps1'),[ref]$Tokens,[ref]$ParseErrors)
if ($ParseErrors.Count) { throw 'The extension setup must parse.' }
function Get-PythonCommand([string]$Name) {
    $Commands = @($Ast.FindAll({param($Node)
        $Node -is [System.Management.Automation.Language.CommandAst] -and
        $Node.CommandElements[0] -is [System.Management.Automation.Language.VariableExpressionAst] -and
        $Node.CommandElements[0].VariablePath.UserPath -eq 'Python'
    }, $true) | Where-Object { $_.Extent.Text -match [regex]::Escape($Name) } | ForEach-Object {$_.Extent.Text} | Select-Object -Unique)
    if ($Commands.Count -ne 1) { throw ('Expected exactly one native Python invocation for '+$Name) }
    return [ScriptBlock]::Create($Commands[0])
}
$InstallCommand = Get-PythonCommand 'church_browser_install.py'
$PipCommand = Get-PythonCommand 'requirements-church.txt'
$Fixture = Join-Path $env:TEMP ("FirstUPCJax Sound Room's caf"+[char]0x00e9+' '+[Guid]::NewGuid().ToString('N'))
$Root = Join-Path $Fixture 'VisionPC'
$Packages = Join-Path $Root 'isolated packages'
$Browsers = Join-Path $Root 'browser cache'
$Capture = Join-Path $Root 'capture.json'
$Utf8 = New-Object Text.UTF8Encoding($false)
$KeepForFollowingTests = $false
New-Item -ItemType Directory -Path $Root -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $Source 'church_browser_install.py') -Destination $Root
$PreviousCapture = $env:BOOTSTRAP_CAPTURE
$PreviousExit = $env:BOOTSTRAP_EXIT
try {
    if ($RealBrowser) {
        # Run the production pip command and production bootstrap command, not a
        # handwritten substitute. This only downloads into the isolated fixture.
        Copy-Item -LiteralPath (Join-Path $Source 'requirements-church.txt') -Destination $Root
        . $PipCommand
        if ($LASTEXITCODE -ne 0) { throw 'Isolated notification dependency install failed.' }
        . $InstallCommand
        if ($LASTEXITCODE -ne 0) { throw 'The real installer command could not install Chromium.' }
        Copy-Item -LiteralPath (Join-Path $Source 'church_login.py') -Destination $Root
        $Config = @{churchAdmins=@{enabled=$true;packagesPath=$Packages;browsersPath=$Browsers}}
        [IO.File]::WriteAllText((Join-Path $Root 'config.json'),($Config | ConvertTo-Json -Depth 5),$Utf8)
        # Extract the same post-install smoke-check command used by setup.
        $Checks = @($Ast.FindAll({param($Node)
            $Node -is [System.Management.Automation.Language.CommandAst] -and
            $Node.CommandElements[0] -is [System.Management.Automation.Language.VariableExpressionAst] -and
            $Node.CommandElements[0].VariablePath.UserPath -eq 'Python'
        },$true) | Where-Object {$_.Extent.Text -match 'church_login.py.*--check'} | ForEach-Object {$_.Extent.Text} | Select-Object -Unique)
        if ($Checks.Count -ne 1) { throw 'Expected one unique production browser smoke-check command.' }
        $CheckCommand = [ScriptBlock]::Create($Checks[0])
        . $CheckCommand
        if ($LASTEXITCODE -ne 0) { throw 'The browser installed by the production command did not launch.' }
        if ($env:GITHUB_ENV) {
            # Reuse this downloaded browser for the remaining browser fixtures.
            [IO.File]::AppendAllText($env:GITHUB_ENV,('PLAYWRIGHT_BROWSERS_PATH='+$Browsers+[Environment]::NewLine),$Utf8)
            $KeepForFollowingTests = $true
        }
        Write-Host 'PASS: production Windows commands installed isolated dependencies and Chromium; the installed browser launched and closed successfully.'
    } else {
        $Module = Join-Path $Packages 'playwright'
        New-Item -ItemType Directory -Path $Module -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $Module '__init__.py'),'',$Utf8)
        $FixtureCode = @'
import json, os, sys
from pathlib import Path
Path(os.environ['BOOTSTRAP_CAPTURE']).write_text(json.dumps({
    'args': sys.argv, 'browsers': os.environ['PLAYWRIGHT_BROWSERS_PATH'],
    'module': __file__}), encoding='utf-8')
raise SystemExit(int(os.environ.get('BOOTSTRAP_EXIT', '0')))
'@
        [IO.File]::WriteAllText((Join-Path $Module '__main__.py'),$FixtureCode,$Utf8)
        $env:BOOTSTRAP_CAPTURE = $Capture
        $env:BOOTSTRAP_EXIT = '0'
        . $InstallCommand
        if ($LASTEXITCODE -ne 0) { throw 'The production browser installer invocation failed.' }
        $Recorded = Get-Content -LiteralPath $Capture -Raw -Encoding UTF8 | ConvertFrom-Json
        if (($Recorded.args -join '|') -ne 'playwright|install|chromium' -or $Recorded.browsers -ne $Browsers -or $Recorded.module -ne (Join-Path $Module '__main__.py')) {
            throw 'The production invocation changed arguments, paths, or the selected dependency package.'
        }
        $env:BOOTSTRAP_EXIT = '23'
        . $InstallCommand
        if ($LASTEXITCODE -ne 23) { throw 'The production invocation hid a failed browser install.' }
        Write-Host 'PASS: Windows PowerShell 5.1 preserved the production command arguments and spaced/Unicode paths; install failures remain failures.'
    }
} finally {
    $env:BOOTSTRAP_CAPTURE = $PreviousCapture
    $env:BOOTSTRAP_EXIT = $PreviousExit
    if (-not $KeepForFollowingTests) { Remove-Item -LiteralPath $Fixture -Recurse -Force }
}
exit 0
