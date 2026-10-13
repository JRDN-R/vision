#requires -Version 5.1
#requires -RunAsAdministrator
<# Optional 27B installation for an existing Vision PC. Run outside livestreams.
Install updates the server from ONE resolved GitHub commit, downloads the pinned
16.54 GB model, benchmarks CPU / partial Vulkan offload, and enables Ultra only
after checks pass. Existing model files, accounts, and preferences are preserved.
Download / Check / Tune / Disable operate on already updated server code.
#>
[CmdletBinding()]
param(
    [ValidateSet('Install','Download','Check','Tune','Disable')]
    [string]$Action = 'Install',
    [string]$SourceRef = 'main',
    [ValidateSet(8192,16384,32768,65536)][int]$ContextTokens = 8192,
    [switch]$SkipCodeUpdate,
    [switch]$Boost
)
if ($Boost -and $Action -notin @('Install','Tune')) { throw 'Boost is only available with Install or Tune.' }
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$InstallRoot = Join-Path $env:ProgramData 'VisionPC'
$ConfigPath = Join-Path $InstallRoot 'config.json'
$Python = Join-Path $InstallRoot 'runtime\python.exe'
$Helper = Join-Path $InstallRoot 'setup_qwen_ultra.py'
if (-not (Test-Path -LiteralPath $ConfigPath) -or -not (Test-Path -LiteralPath $Python)) {
    throw 'Complete the existing Vision PC and Qwen Local installation first.'
}
if ($Action -in @('Install','Tune')) {
    $Busy = @(Get-Process -Name obs64,obs32,'Streamlabs OBS' -ErrorAction SilentlyContinue)
    if ($Busy.Count -gt 0) { throw 'Close OBS/Streamlabs before installation or tuning. No livestream process was stopped.' }
}
if ($Action -eq 'Install' -and -not $SkipCodeUpdate) {
    if ($SourceRef -notmatch '^[A-Za-z0-9._/-]+$' -or $SourceRef.Contains('..')) { throw 'Invalid source reference.' }
    $Headers = @{Accept='application/vnd.github+json';'User-Agent'='Vision-Qwen-Ultra'}
    $Reference = [Uri]::EscapeDataString($SourceRef)
    $Revision = Invoke-RestMethod -Headers $Headers -Uri "https://api.github.com/repos/JRDN-R/vision/commits/$Reference" -TimeoutSec 60
    $Commit = [string]$Revision.sha
    if ($Commit -notmatch '^[0-9a-f]{40}$') { throw 'Could not resolve a fixed source commit.' }
    $Updater = Join-Path $env:TEMP ('Vision-Ultra-' + [Guid]::NewGuid().ToString('N') + '.ps1')
    try {
        Write-Host "Updating Venture server from $Commit. This restarts the Vision processor." -ForegroundColor Cyan
        Invoke-WebRequest -UseBasicParsing -Headers $Headers -Uri "https://raw.githubusercontent.com/JRDN-R/vision/$Commit/vision-pc/Setup-Vision-PC.ps1" -OutFile $Updater -TimeoutSec 120
        if ((Get-Item -LiteralPath $Updater).Length -lt 1000) { throw 'Incomplete updater download.' }
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Updater -Action Update -SourceRef $Commit
        if ($LASTEXITCODE -ne 0) { throw 'Server update failed. Ultra has not been enabled.' }
    } finally {
        if (Test-Path -LiteralPath $Updater) { Remove-Item -LiteralPath $Updater -Force }
    }
}
if (-not (Test-Path -LiteralPath $Helper)) { throw 'Run Install first to update Venture with Qwen Ultra support.' }
Write-Host 'Qwen Ultra adds 16.54 GB (15.40 GiB). Keep at least 18.7 GB free for download and working headroom.'
Write-Host 'Tuning compares measured thread counts and partial GPU offload. Utilization percentages cannot be fixed.'
if ($Boost) { Write-Host 'Optional Boost: benchmark up to 16 physical CPU cores and wider GPU offload with at least 2 GiB free VRAM reserved. No overclocking, BIOS or power-limit changes.' }
if ($ContextTokens -ge 32768) {
    Write-Host 'Large-context validation can take hours. Memory protections remain enabled; the setting is offered only after it passes.'
}
$HelperAction = $Action.ToLowerInvariant()
$HelperArgs = @($Helper, '--config', $ConfigPath, '--action', $HelperAction, '--context', [string]$ContextTokens)
if ($Boost) { $HelperArgs += '--boost' }
& $Python @HelperArgs
if ($LASTEXITCODE -ne 0) { throw "Qwen Ultra $Action failed. Read the message above; rerun to resume the download or tuning." }
if ($Action -in @('Install','Tune')) {
    Write-Host 'Qwen Ultra is ready. Refresh the updated Vision app and select the gold Qwen Ultra model.' -ForegroundColor Green
    Write-Host 'Select the tested context in Ultra settings. Long responses reserve answer space and save visible progress; completion time is not guaranteed.'
    if ($Boost) { Write-Host 'Venture now offers a verified Boost profile. Only select it outside OBS/Streamlabs sessions.' }
}
