#requires -Version 5.1
<# CI-only disposable checks. No task registration, system configuration, model
downloads, Tailscale calls or production data is touched. #>
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path $PSScriptRoot -Parent
foreach ($Name in @('Setup-Vision-PC.ps1','Context-Tools.ps1','Update-Venture.ps1')) {
    $Tokens = $null
    $ParseErrors = $null
    $Ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $RepoRoot ('vision-pc\' + $Name)), [ref]$Tokens, [ref]$ParseErrors)
    if ($ParseErrors.Count) { throw ($ParseErrors | Out-String) }
}
& python (Join-Path $RepoRoot 'vision-pc\setup_context.py') --self-test
if ($LASTEXITCODE -ne 0) { throw 'Disposable context engine smoke failed.' }
& python -m unittest discover -s (Join-Path $RepoRoot 'tests') -p test_context_setup.py -v
if ($LASTEXITCODE -ne 0) { throw 'Context installation/backup unit checks failed.' }

# Exercise the actual rollback function using fake task/health functions and an
# isolated ProgramData-shaped tree, verifying that newer user data survives.
$InstallRoot = Join-Path ([IO.Path]::GetTempPath()) ('vision-context-ci-' + [Guid]::NewGuid().ToString('N'))
$DownloadDir = Join-Path $InstallRoot 'downloads'
$ConfigPath = Join-Path $InstallRoot 'config.json'
$TaskName = 'CI only, never an actual scheduled task'
$PythonExe = (Get-Command python).Source
$ProcessorFiles = @('server.py','context_engine.py')
$Backup = Join-Path $DownloadDir ('processor-update-backup-' + [Guid]::NewGuid().ToString('N'))
$Utf8 = New-Object Text.UTF8Encoding($false)
function Write-Utf8([string]$Path,[string]$Value) { [IO.File]::WriteAllText($Path,$Value,$Utf8) }
function Set-PrivateDirectory([string]$Path) { }
function Assert-PrivateSoundPath([string]$Path) { if (-not (Test-Path -LiteralPath $Path)) { throw 'Missing CI path.' } }
function Get-ScheduledTask { return [pscustomobject]@{ State = 'Running' } }
function Stop-ProcessorForChange { }
function Start-ScheduledTask { }
function Wait-Processor { return $true }
function Invoke-Checked([string]$Executable,[string[]]$Arguments) { & $Executable @Arguments; if ($LASTEXITCODE -ne 0) { throw 'CI native check failed.' } }
try {
    New-Item -ItemType Directory -Path (Join-Path $Backup 'snapshot'),(Join-Path $InstallRoot 'data') -Force | Out-Null
    Write-Utf8 $ConfigPath ('{"port":8765,"token":"' + ('a' * 64) + '","marker":"current"}')
    Write-Utf8 (Join-Path $Backup 'config.json') ('{"port":8765,"token":"' + ('a' * 64) + '","marker":"previous"}')
    Write-Utf8 (Join-Path $InstallRoot 'server.py') "# current`n"
    Write-Utf8 (Join-Path $InstallRoot 'context_engine.py') "# new derived engine`n"
    Write-Utf8 (Join-Path $InstallRoot 'data\new-conversation.json') '{"createdAfterUpdate":true}'
    Write-Utf8 (Join-Path $Backup 'server.py') "# previous`n"
    Write-Utf8 (Join-Path $Backup 'snapshot\backup-manifest.json') '{"complete":true}'
    $Manifest = [ordered]@{
        schemaVersion=1; complete=$true;
        configSha256=(Get-FileHash -LiteralPath (Join-Path $Backup 'config.json') -Algorithm SHA256).Hash;
        files=@(
            [pscustomobject]@{name='server.py';existed=$true;sha256=(Get-FileHash -LiteralPath (Join-Path $Backup 'server.py') -Algorithm SHA256).Hash},
            [pscustomobject]@{name='context_engine.py';existed=$false}
        )
    }
    Write-Utf8 (Join-Path $Backup 'processor-backup.json') ($Manifest | ConvertTo-Json -Depth 10)
    . (Join-Path $RepoRoot 'vision-pc\Context-Tools.ps1')
    Restore-ProcessorBackup $Backup
    if ((Get-Content -LiteralPath (Join-Path $InstallRoot 'server.py') -Raw) -notmatch '# previous') { throw 'Rollback did not restore code.' }
    if (Test-Path -LiteralPath (Join-Path $InstallRoot 'context_engine.py')) { throw 'Rollback retained a newly introduced application module.' }
    if ((Get-Content -LiteralPath (Join-Path $InstallRoot 'data\new-conversation.json') -Raw) -ne '{"createdAfterUpdate":true}') { throw 'Rollback changed newer user data.' }
    $Rejected = $false
    try { Restore-ProcessorBackup (Join-Path $InstallRoot 'data') } catch { $Rejected = $true }
    if (-not $Rejected) { throw 'Rollback accepted a non-backup path.' }
    Write-Host 'PASS: PowerShell parsing, offline smoke, backup integrity and code rollback preserving newer user data.' -ForegroundColor Green
} finally {
    if (Test-Path -LiteralPath $InstallRoot) { Remove-Item -LiteralPath $InstallRoot -Recurse -Force }
}
