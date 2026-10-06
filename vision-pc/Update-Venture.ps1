#requires -Version 5.1
#requires -RunAsAdministrator
<#
Fetch a current updater and pin every server file to the same repository commit.
The installed older updater may not yet include Venture's additional modules.
Existing setup preserves account data, credentials, configuration and models.
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$InstallRoot = Join-Path $env:ProgramData 'VisionPC'
if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot 'config.json'))) {
    throw 'This command updates an existing FUPCJ Server. Complete the initial Vision PC setup first.'
}
$Headers = @{ 'Accept' = 'application/vnd.github+json'; 'User-Agent' = 'Vision-Venture-Updater' }
$Branch = Invoke-RestMethod -Headers $Headers -Uri 'https://api.github.com/repos/JRDN-R/vision/branches/main' -TimeoutSec 60
$Commit = [string]$Branch.commit.sha
if ($Commit -notmatch '^[0-9a-f]{40}$') { throw 'GitHub did not return a valid source commit. Nothing was changed.' }
$Updater = Join-Path $env:TEMP ('Setup-Vision-Venture-' + [Guid]::NewGuid().ToString('N') + '.ps1')
try {
    Write-Host "Updating Vision Venture from verified repository commit $Commit" -ForegroundColor Cyan
    Invoke-WebRequest -UseBasicParsing -Headers $Headers -Uri "https://raw.githubusercontent.com/JRDN-R/vision/$Commit/vision-pc/Setup-Vision-PC.ps1" -OutFile $Updater -TimeoutSec 120
    if ((Get-Item -LiteralPath $Updater).Length -lt 1000) { throw 'The updater download is incomplete. Nothing was changed.' }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Updater -Action Update -SourceRef $Commit
    if ($LASTEXITCODE -ne 0) { throw "Vision update reported an error ($LASTEXITCODE). Review its message before retrying." }
    Write-Host 'Venture server update complete. Refresh Vision or download and reopen the latest portable HTML.' -ForegroundColor Green
} finally {
    if (Test-Path -LiteralPath $Updater) { Remove-Item -LiteralPath $Updater -Force }
}
