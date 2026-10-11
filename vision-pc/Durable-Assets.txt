#requires -Version 5.1
<# Retention diagnostics only. This does not install Qwen or activate any migration. #>
[CmdletBinding()]
param(
    [ValidateSet('DryRun','Check','Backup')][string]$Action = 'DryRun',
    [string]$InstallRoot = (Join-Path $env:ProgramData 'VisionPC'),
    [string]$ConfigPath = '',
    [string]$OutputPath = '',
    [string]$BackupDirectory = ''
)
$ErrorActionPreference = 'Stop'
if (-not $ConfigPath) { $ConfigPath = Join-Path $InstallRoot 'config.json' }
$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
$Python = Join-Path $InstallRoot 'runtime\python.exe'
$Inspector = Join-Path $InstallRoot 'inspect_owned_assets.py'
foreach ($File in @($Python, $Inspector)) {
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) { throw 'Install this reviewed code revision before using its retention diagnostics.' }
}
if ($Action -eq 'Backup') {
    $Principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run the cold-backup check as Administrator.' }
    if (-not $BackupDirectory) { throw 'Choose a new -BackupDirectory outside DATA_DIR.' }
    $Task = Get-ScheduledTask -TaskName 'Vision Private PC' -ErrorAction Stop
    if ($Task.State -eq 'Running') { throw 'The processor is running. This command will not stop it. Arrange a maintenance boundary first.' }
    $Processes = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'")
    foreach ($Process in $Processes) {
        if (-not $Process.CommandLine) { throw 'Cannot establish that all Python workers are stopped. No backup was started.' }
        if ($Process.CommandLine -match 'server\.py') {
            throw 'A Vision server process is still running. No backup was started.'
        }
    }
    $Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    $DataDirectory = [string]$Config.dataDir
    if (-not $DataDirectory) { $DataDirectory = 'data' }
    if (-not [IO.Path]::IsPathRooted($DataDirectory)) { $DataDirectory = Join-Path (Split-Path -Parent $ConfigPath) $DataDirectory }
    $DataDirectory = [IO.Path]::GetFullPath($DataDirectory)
    $BackupDirectory = [IO.Path]::GetFullPath($BackupDirectory)
    if ((Test-Path -LiteralPath $BackupDirectory) -or
        $BackupDirectory.TrimEnd('\').Equals($DataDirectory.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase) -or
        $BackupDirectory.StartsWith($DataDirectory.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $DataDirectory.StartsWith($BackupDirectory.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The backup destination must be new and outside the data directory tree.'
    }
    # Protect the new envelope before copying any private data into it.
    New-Item -ItemType Directory -Path $BackupDirectory -ErrorAction Stop | Out-Null
    $Acl = Get-Acl -LiteralPath $BackupDirectory
    $Acl.SetAccessRuleProtection($true, $false)
    foreach ($Sid in @('S-1-5-18', 'S-1-5-32-544', [Security.Principal.WindowsIdentity]::GetCurrent().User.Value)) {
        $Identity = New-Object Security.Principal.SecurityIdentifier($Sid)
        $Rule = New-Object Security.AccessControl.FileSystemAccessRule($Identity, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
        $Acl.AddAccessRule($Rule)
    }
    Set-Acl -LiteralPath $BackupDirectory -AclObject $Acl -ErrorAction Stop
    $SnapshotDirectory = Join-Path $BackupDirectory 'snapshot'
    # Use the existing cold-data copier, deliberately INCLUDING leased Vortex
    # sources. This is DATA_DIR + configuration, not a full installation rollback.
    & $Python (Join-Path $InstallRoot 'setup_context.py') --backup-data $DataDirectory --destination $SnapshotDirectory
    if ($LASTEXITCODE -ne 0) { throw 'The cold-data backup failed. Do not treat it as a valid recovery set.' }
    Copy-Item -LiteralPath $ConfigPath -Destination (Join-Path $SnapshotDirectory 'config.json') -ErrorAction Stop
    Write-Host 'DATA_DIR and configuration copied. No database was restored, no service was restarted, and no model was installed.'
    Write-Host 'Protect this backup: it contains private account data and configuration. External configured paths and code are not included.'
    exit 0
}
$Arguments = @($Inspector, '--config', $ConfigPath)
if ($Action -eq 'Check') { $Arguments += '--verify' }
if ($OutputPath) { $Arguments += @('--output', $OutputPath) }
& $Python @Arguments
if ($LASTEXITCODE -ne 0) { throw 'Retention inspection found an issue or could not complete. Review its report before enabling any new behavior.' }
Write-Host 'Retention inspection only. Local-model readiness and server-side board attachment are not implemented by this command.'
