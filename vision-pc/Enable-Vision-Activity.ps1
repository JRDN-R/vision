#requires -Version 5.1
# Enable the private activity API without reinstalling models or changing Firebase.
[CmdletBinding()]
param([string]$AdminEmail = '', [string]$SourceRef = 'main')
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open Windows PowerShell as Administrator on the Vision PC, then run this command again.'
}
if ($SourceRef -notmatch '^(main|[0-9a-f]{40})$') { throw 'SourceRef must be main or a full commit SHA.' }
$Root = Join-Path $env:ProgramData 'VisionPC'
$Python = Join-Path $Root 'runtime\python.exe'
$Server = Join-Path $Root 'server.py'
$TaskName = 'Vision Private PC'
foreach ($Path in @($Root,(Join-Path $Root 'runtime'),(Join-Path $Root 'data'),(Join-Path $Root 'downloads'))) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container) -or ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'The standard Vision PC installation is missing or uses linked folders. No changes were made.'
    }
}
$Owner = (Get-Acl -LiteralPath $Root).GetOwner([Security.Principal.SecurityIdentifier]).Value
if ($Owner -notin @('S-1-5-18','S-1-5-32-544')) { throw 'The installation has an unexpected owner. No changes were made.' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'The private Vision runtime is missing.' }
$Task = Get-ScheduledTask -TaskName $TaskName
if (@($Task.Actions).Count -ne 1 -or $Task.Actions[0].Execute -ne $Python -or $Task.Actions[0].Arguments -ne ('"' + $Server + '"')) {
    throw 'The Vision startup task has been customized. Review it before installing activity access.'
}
if ($Task.State -eq 'Disabled') { throw 'Enable the existing Vision startup task before running this installer.' }
$Config = Get-Content -LiteralPath (Join-Path $Root 'config.json') -Raw | ConvertFrom-Json
if ($Config.firebaseAuth.enabled -ne $true) { throw 'Enable Google sign-in for Vision first.' }
$WasRunning = $Task.State -eq 'Running'
$Stage = Join-Path (Join-Path $Root 'downloads') ('activity-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Stage | Out-Null
function Run-Helper([string]$Phase) {
    $Arguments = @((Join-Path $Stage 'setup_activity.py'),$Phase,'--root',$Root,'--stage',$Stage)
    if ($Phase -eq 'prepare' -and $AdminEmail) { $Arguments += @('--admin-email',$AdminEmail) }
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Activity setup did not complete its $Phase step." }
}
function Stop-Vision {
    Stop-ScheduledTask -TaskName $TaskName
    for ($i=0; $i -lt 50; $i++) {
        if ((Get-ScheduledTask -TaskName $TaskName).State -ne 'Running') { return }
        Start-Sleep -Milliseconds 200
    }
    throw 'The Vision task did not stop. No application files should be changed while it is running.'
}
function Wait-Vision {
    for ($i=0; $i -lt 20; $i++) {
        try {
            $Status = Invoke-RestMethod -Uri ("http://127.0.0.1:" + [int]$Config.port + '/api/status') -TimeoutSec 3
            if ($Status.service -eq 'vision' -and $Status.googleSignInRequired -eq $true) { return $true }
        } catch { }
        Start-Sleep -Seconds 2
    }
    return $false
}
$TaskDisabled = $false
$Applying = $false
try {
    Write-Host 'Preparing Vision Activity...' -ForegroundColor Cyan
    # Resolve main once so all downloads come from one immutable revision.
    $Commit = (Invoke-RestMethod -Uri "https://api.github.com/repos/JRDN-R/vision/commits/$SourceRef" -Headers @{'User-Agent'='Vision-Activity-Setup'} -TimeoutSec 30).sha
    if ($Commit -notmatch '^[0-9a-f]{40}$') { throw 'GitHub did not return a valid source revision.' }
    foreach ($File in @('activity_dashboard.py','account_administration.py','setup_activity.py')) {
        $Destination = Join-Path $Stage $File
        Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/JRDN-R/vision/$Commit/vision-pc/$File" -OutFile $Destination -TimeoutSec 60
        if ((Get-Item -LiteralPath $Destination).Length -eq 0) { throw 'A required download was empty.' }
    }
    Run-Helper 'prepare'
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
    $TaskDisabled = $true
    Stop-Vision
    $Applying = $true
    Run-Helper 'apply'
    & $Python $Server --check
    if ($LASTEXITCODE -ne 0) { throw 'The updated Vision configuration did not pass its check.' }
    Push-Location $Root
    try {
        & $Python -c "import server; assert 'vision_activity' in server.app.view_functions, 'Activity route missing'"
        if ($LASTEXITCODE -ne 0) { throw 'The private activity route was not registered.' }
    } finally { Pop-Location }
    Enable-ScheduledTask -TaskName $TaskName | Out-Null
    $TaskDisabled = $false
    Start-ScheduledTask -TaskName $TaskName
    if (-not (Wait-Vision)) { throw 'Vision did not become reachable after the update.' }
    Write-Host "`nVision Activity is enabled. Open this page and use the selected Google account:" -ForegroundColor Green
    Write-Host 'https://jrdn-r.github.io/vision/activity.html'
    Write-Host 'Existing projects, models, text logs, and Firebase settings were preserved.'
    Write-Host "Protected rollback files: $Stage"
} catch {
    $Failure = $_.Exception.Message
    if ($Applying) {
        try {
            Disable-ScheduledTask -TaskName $TaskName | Out-Null
            $TaskDisabled = $true
            Stop-Vision
            Run-Helper 'rollback'
            Enable-ScheduledTask -TaskName $TaskName | Out-Null
            $TaskDisabled = $false
            if ($WasRunning) {
                Start-ScheduledTask -TaskName $TaskName
                if (-not (Wait-Vision)) { throw 'The restored Vision processor needs attention.' }
            }
        } catch { throw "$Failure Recovery needs attention: $($_.Exception.Message). Protected backup: $Stage" }
        throw "$Failure Previous server files and activity permissions were restored."
    }
    if ($TaskDisabled) {
        Enable-ScheduledTask -TaskName $TaskName | Out-Null
        $TaskDisabled = $false
        if ($WasRunning -and (Get-ScheduledTask -TaskName $TaskName).State -ne 'Running') {
            Start-ScheduledTask -TaskName $TaskName
        }
    }
    throw $Failure
} finally {
    if ($TaskDisabled) { Enable-ScheduledTask -TaskName $TaskName | Out-Null }
}
