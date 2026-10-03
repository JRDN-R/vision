#requires -Version 5.1
<#
FUPCJ Server. Run from Windows PowerShell as Administrator.
The application downloads its own private runtime; no existing Python/Node install is needed.
#>
[CmdletBinding()]
param(
    [ValidateSet('Setup','Update','EnablePublic','EnableGoogleSignIn','ExportConnection','Start','Stop','InstallLocalTranscription','EnableLocalTranscription','DisableLocalTranscription','CheckLocalTranscription','InstallSoundEvents','DisableSoundEvents','CheckSoundEvents','InstallDocumentTools')]
    [string]$Action = 'Setup',
    [string]$OutputDirectory = [Environment]::GetFolderPath('Desktop'),
    [string]$SourceRef = 'main',
    [string]$FirebaseProjectId = 'visionboard-api',
    [ValidateSet('Full','Standard')][string]$DocumentProfile = 'Standard'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$InstallRoot = Join-Path $env:ProgramData 'VisionPC'
$TaskName = 'Vision Private PC'
$ConfigPath = Join-Path $InstallRoot 'config.json'
$RuntimeDir = Join-Path $InstallRoot 'runtime'
$ToolsDir = Join-Path $InstallRoot 'tools'
$DownloadDir = Join-Path $InstallRoot 'downloads'
$PythonExe = Join-Path $RuntimeDir 'python.exe'
$Utf8 = New-Object Text.UTF8Encoding($false)
# Keep every activation/rollback path in sync, including optional workers.
$ProcessorFiles = @('server.py','trials.py','media.py','uploaded_media.py','sessions.py','transcription.py','firebase_auth.py','audit_logs.py','setup_local.py','requirements.txt','sound_model.py','setup_sound_events.py','sound-model-manifest.json','requirements-sound.txt','documents.py','document_worker.py','setup_documents.py','requirements-documents.txt','requirements-documents-full.txt','Document-Tools.ps1')

function Write-Stage([string]$Text) { Write-Host "`n$Text" -ForegroundColor Cyan }
function Write-Utf8([string]$Path, [string]$Content) { [IO.File]::WriteAllText($Path, $Content, $Utf8) }
function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Executable" }
}
function Get-Download([string]$Url, [string]$Destination) {
    if (-not $Url.StartsWith('https://')) { throw 'Downloads require HTTPS.' }
    $Part = "$Destination.part"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Part -TimeoutSec 600
        if ((Get-Item -LiteralPath $Part).Length -eq 0) { throw "Empty download: $Url" }
        Move-Item -LiteralPath $Part -Destination $Destination -Force
    } finally {
        if (Test-Path -LiteralPath $Part) { Remove-Item -LiteralPath $Part -Force }
    }
}
function Assert-Checksum([string]$File, [string]$ChecksumUrl) {
    $Text = (Invoke-WebRequest -UseBasicParsing -Uri $ChecksumUrl -TimeoutSec 60).Content
    $Match = [regex]::Match([string]$Text, '(?i)\b[0-9a-f]{64}\b')
    if (-not $Match.Success) { throw "Missing download checksum: $ChecksumUrl" }
    if ((Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash -ne $Match.Value) {
        throw "Download checksum did not match: $File"
    }
}
function Set-PrivateDirectory([string]$Path) {
    if ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Refusing linked installation folder: $Path"
    }
    $Acl = New-Object Security.AccessControl.DirectorySecurity
    $Acl.SetAccessRuleProtection($true, $false)
    $Acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    foreach ($SidValue in @('S-1-5-18','S-1-5-32-544')) {
        $Sid = New-Object Security.Principal.SecurityIdentifier($SidValue)
        $Rule = New-Object Security.AccessControl.FileSystemAccessRule($Sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
        $Acl.AddAccessRule($Rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $Acl
}
function Get-Tailscale {
    $Path = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
    if (Test-Path -LiteralPath $Path) { return $Path }
    return $null
}
function Read-Configuration {
    if (-not (Test-Path -LiteralPath $ConfigPath)) { throw 'Run Setup first.' }
    $Configuration = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    if ([string]$Configuration.token -notmatch '^[A-Za-z0-9_-]{40,128}$' -or [int]$Configuration.port -ne 8765) {
        throw 'Existing config.json is invalid. No credentials were replaced.'
    }
    return $Configuration
}
function Initialize-ServerConfiguration($Configuration) {
    # This credential stays in the protected config. It is never exported to a
    # browser and authorizes only direct loopback diagnostic requests.
    if (-not $Configuration.diagnosticToken) {
        $DiagnosticBytes = New-Object byte[] 32
        $DiagnosticGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $DiagnosticGenerator.GetBytes($DiagnosticBytes) } finally { $DiagnosticGenerator.Dispose() }
        $DiagnosticToken = [BitConverter]::ToString($DiagnosticBytes).Replace('-','').ToLowerInvariant()
        $Configuration | Add-Member -NotePropertyName diagnosticToken -NotePropertyValue $DiagnosticToken -Force
    } elseif ([string]$Configuration.diagnosticToken -notmatch '^[A-Za-z0-9_-]{40,128}$') {
        throw 'The saved diagnostic credential is invalid. No credentials were replaced.'
    }
    $AuditDirectory = [string]$Configuration.auditLogDir
    if (-not $AuditDirectory) {
        $UserDesktop = [Environment]::GetFolderPath('Desktop')
        if (-not $UserDesktop -or -not (Test-Path -LiteralPath $UserDesktop -PathType Container)) {
            throw 'The current Windows user has no accessible Desktop. Run the installer from the usual signed-in administrator account.'
        }
        $AuditDirectory = Join-Path $UserDesktop 'Vision Logs'
    }
    if (-not [IO.Path]::IsPathRooted($AuditDirectory)) { throw 'The saved audit log folder must be an absolute path.' }
    New-Item -ItemType Directory -Path $AuditDirectory -Force | Out-Null
    if ((Get-Item -LiteralPath $AuditDirectory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'The Vision Logs folder is a link. Choose a regular folder for server logs.'
    }
    # The task runs as SYSTEM. Keep the installing user and existing access rules
    # while giving the service and administrators access to this Desktop folder.
    $AuditAcl = Get-Acl -LiteralPath $AuditDirectory
    foreach ($SidValue in @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value,'S-1-5-18','S-1-5-32-544')) {
        $Sid = New-Object Security.Principal.SecurityIdentifier($SidValue)
        $Rule = New-Object Security.AccessControl.FileSystemAccessRule($Sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
        $AuditAcl.AddAccessRule($Rule)
    }
    Set-Acl -LiteralPath $AuditDirectory -AclObject $AuditAcl
    $WriteProbe = Join-Path $AuditDirectory ('.vision-write-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try { Write-Utf8 $WriteProbe 'Vision log folder write check' }
    finally { if (Test-Path -LiteralPath $WriteProbe) { Remove-Item -LiteralPath $WriteProbe -Force } }
    $Configuration | Add-Member -NotePropertyName auditLogDir -NotePropertyValue $AuditDirectory -Force
}
function Get-LocalDiagnosticHeaders($Configuration) {
    $Headers = @{ Authorization = "Bearer $($Configuration.token)" }
    if ($Configuration.diagnosticToken) { $Headers['X-Vision-Diagnostic-Token'] = [string]$Configuration.diagnosticToken }
    return $Headers
}
function Connect-Tailscale([string]$Executable, $Configuration) {
    Start-Service -Name 'Tailscale'
    $StatusText = & $Executable status --json
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the Tailscale connection status.' }
    $Status = $StatusText | ConvertFrom-Json
    if ($Status.BackendState -ne 'Running') {
        Write-Host 'Sign in using the Tailscale link below to connect this FUPCJ Server.'
        & $Executable up --unattended=true --timeout=3m | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Finish the Tailscale sign-in, then run this same command again.' }
    }
    Invoke-Checked $Executable @('set','--unattended=true') | Out-Host
    $StatusText = & $Executable status --json
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the Tailscale connection status.' }
    $Status = $StatusText | ConvertFrom-Json
    $DnsName = ([string]$Status.Self.DNSName).TrimEnd('.')
    if ($Status.BackendState -ne 'Running' -or $DnsName -notmatch '^[A-Za-z0-9.-]+\.ts\.net$') {
        throw 'Tailscale is not connected with a DNS name yet. Complete sign-in and run the same command again.'
    }
    $Configuration.backendUrl = "https://$DnsName"
    Write-Utf8 $ConfigPath ($Configuration | ConvertTo-Json -Depth 20)
    return $DnsName
}
function Assert-VisionRoute([string]$Executable, [string]$DnsName, [bool]$PublicAccess) {
    # Never reset or replace another application's Tailscale routes.
    $ServeText = & $Executable serve status --json
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the existing Tailscale Serve configuration.' }
    $ServeConfig = $ServeText | ConvertFrom-Json
    $HasRoute = $false
    foreach ($Entry in @($ServeConfig.Web.PSObject.Properties)) {
        if (-not $Entry) { continue }
        foreach ($Handler in @($Entry.Value.Handlers.PSObject.Properties)) {
            $HasRoute = $true
            if ($Entry.Name -ne ($DnsName + ':443') -or $Handler.Name -ne '/' -or $Handler.Value.Proxy -ne 'http://127.0.0.1:8765') {
                throw 'This FUPCJ Server already has a different Tailscale Serve route. Nothing was overwritten. Describe that existing setup for help.'
            }
        }
    }
    $TcpEntries = @($ServeConfig.TCP.PSObject.Properties | Where-Object { $_ })
    if ($TcpEntries.Count -gt 0 -and (-not $HasRoute -or $TcpEntries.Count -ne 1 -or $TcpEntries[0].Name -ne '443' -or $TcpEntries[0].Value.HTTPS -ne $true)) {
        throw 'An existing Tailscale TCP service uses this FUPCJ Server. Nothing was overwritten.'
    }
    foreach ($Entry in @($ServeConfig.AllowFunnel.PSObject.Properties | Where-Object { $_.Value -eq $true })) {
        if (-not $HasRoute -or $Entry.Name -ne ($DnsName + ':443')) {
            throw 'Another Tailscale Funnel route uses this FUPCJ Server. Nothing was overwritten.'
        }
        if (-not $PublicAccess) {
            throw 'Vision already has a public Funnel route. Run this script with -Action EnablePublic to preserve public access.'
        }
    }
    if (@($ServeConfig.Foreground.PSObject.Properties | Where-Object { $_ }).Count -gt 0) {
        throw 'An active foreground Tailscale service uses this FUPCJ Server. Nothing was overwritten.'
    }
}
function Start-VisionRoute([string]$Executable, $Configuration) {
    $DnsName = ([Uri]$Configuration.backendUrl).DnsSafeHost
    $PublicAccess = ($Configuration.publicAccess -eq $true)
    Assert-VisionRoute $Executable $DnsName $PublicAccess
    if ($PublicAccess) {
        Write-Host 'If Tailscale prints an approval link, open it and enable Funnel for this FUPCJ Server.'
        & $Executable funnel --bg --yes --https=443 http://127.0.0.1:8765
    } else {
        Write-Host 'If Tailscale prints an HTTPS approval link, open it and enable HTTPS for your private network.'
        & $Executable serve --bg --yes --https=443 http://127.0.0.1:8765
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'Complete the approval at the Tailscale link, then run the same command again. Your installation and token are saved.'
    }
}
function Register-ProcessorTask {
    $TaskAction = New-ScheduledTaskAction -Execute $PythonExe -Argument ('"' + (Join-Path $InstallRoot 'server.py') + '"') -WorkingDirectory $InstallRoot
    $Trigger = New-ScheduledTaskTrigger -AtStartup
    $TaskPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $TaskAction -Trigger $Trigger -Principal $TaskPrincipal -Settings $Settings -Description 'Vision YouTube processor. Listens only on this computer; Tailscale provides its configured HTTPS connection.' -Force | Out-Null
}
function Test-Processor([string]$BaseUrl, [string]$Token) {
    try {
        $Configuration = Read-Configuration
        if ($BaseUrl -eq 'http://127.0.0.1:8765') {
            $Result = Invoke-RestMethod -Uri "$BaseUrl/api/health" -Headers (Get-LocalDiagnosticHeaders $Configuration) -TimeoutSec 10
        } elseif ($Configuration.firebaseAuth.enabled -eq $true) {
            # Public reachability is checked without either installation secret.
            $Status = Invoke-RestMethod -Uri "$BaseUrl/api/status" -TimeoutSec 10
            return ($Status.service -eq 'vision' -and $Status.mode -eq 'private-pc' -and $Status.googleSignInRequired -eq $true)
        } else {
            $Result = Invoke-RestMethod -Uri "$BaseUrl/api/health" -Headers @{ Authorization = "Bearer $Token" } -TimeoutSec 10
        }
        return ($Result.ok -eq $true -and $Result.mode -eq 'private-pc')
    } catch { return $false }
}
function Wait-Processor([string]$BaseUrl, [string]$Token) {
    for ($Attempt = 0; $Attempt -lt 12; $Attempt++) {
        if (Test-Processor $BaseUrl $Token) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}
function Assert-InstalledProcessor {
    $Owner = (Get-Acl -LiteralPath $InstallRoot).GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($Owner -notin @('S-1-5-18','S-1-5-32-544')) { throw 'The installation folder has an unexpected owner. No changes were made.' }
    foreach ($Path in @($InstallRoot,$RuntimeDir,$ToolsDir,$DownloadDir)) {
        if (-not (Test-Path -LiteralPath $Path) -or ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'The existing installation is incomplete or uses linked folders. Run Setup first.'
        }
    }
    if (-not (Test-Path -LiteralPath $PythonExe)) { throw 'The private runtime is missing. Run Setup first.' }
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if (@($Task.Actions).Count -ne 1 -or $Task.Actions[0].Execute -ne $PythonExe -or $Task.Actions[0].Arguments -ne ('"' + (Join-Path $InstallRoot 'server.py') + '"')) {
        throw 'The Vision task has a custom action. No changes were made; review it before installing a plugin.'
    }
}
function Invoke-LocalLogged([string[]]$Arguments, [string]$LogBase) {
    # Start-Process avoids PowerShell 5.1 treating native progress on stderr as
    # a terminating error. No credentials are passed to these commands.
    $Quoted = @($Arguments | ForEach-Object { '"' + $_.Replace('"','\"') + '"' })
    $Process = Start-Process -FilePath $PythonExe -ArgumentList $Quoted -Wait -PassThru -NoNewWindow -RedirectStandardOutput "$LogBase.log" -RedirectStandardError "$LogBase-errors.log"
    if ($Process.ExitCode -ne 0) {
        Get-Content -LiteralPath "$LogBase.log" -Tail 8 | Out-Host
        Get-Content -LiteralPath "$LogBase-errors.log" -Tail 12 | Out-Host
        throw "Local transcription command failed ($($Process.ExitCode)). See $LogBase.log and $LogBase-errors.log. No paid fallback was enabled."
    }
    Get-Content -LiteralPath "$LogBase.log" -Tail 4 | Out-Host
}
function Stop-ProcessorForChange {
    Stop-ScheduledTask -TaskName $TaskName
    for ($Attempt = 0; $Attempt -lt 50; $Attempt++) {
        if ((Get-ScheduledTask -TaskName $TaskName).State -ne 'Running') { return }
        Start-Sleep -Milliseconds 200
    }
    throw 'The Vision task did not stop. No application files should be changed until it stops.'
}
function Set-ProcessorUpdate($Configuration, [string]$StageDirectory, [bool]$CheckGoogleSignIn = $false, [bool]$CheckSoundEvents = $false, [bool]$CheckDocuments = $false) {
    Initialize-ServerConfiguration $Configuration
    $Files = $ProcessorFiles
    $BackupDirectory = Join-Path $DownloadDir ('processor-update-backup-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $BackupDirectory | Out-Null
    Copy-Item -LiteralPath $ConfigPath -Destination (Join-Path $BackupDirectory 'config.json')
    $PreviousFiles = @{}
    foreach ($File in $Files) {
        $PreviousFiles[$File] = Test-Path -LiteralPath (Join-Path $InstallRoot $File)
        if ($PreviousFiles[$File]) { Copy-Item -LiteralPath (Join-Path $InstallRoot $File) -Destination (Join-Path $BackupDirectory $File) }
    }
    $WasRunning = ((Get-ScheduledTask -TaskName $TaskName).State -eq 'Running')
    $Changed = $false
    try {
        Stop-ProcessorForChange
        $Changed = $true
        foreach ($File in $Files) { Copy-Item -LiteralPath (Join-Path $StageDirectory $File) -Destination (Join-Path $InstallRoot $File) -Force }
        $TempConfig = "$ConfigPath.update-part"
        Write-Utf8 $TempConfig ($Configuration | ConvertTo-Json -Depth 20)
        Move-Item -LiteralPath $TempConfig -Destination $ConfigPath -Force
        Invoke-Checked $PythonExe @((Join-Path $InstallRoot 'server.py'),'--check')
        Start-ScheduledTask -TaskName $TaskName
        if (-not (Wait-Processor 'http://127.0.0.1:8765' $Configuration.token)) { throw 'The updated processor did not become healthy.' }
        if ($CheckDocuments) {
            $Health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -Headers (Get-LocalDiagnosticHeaders $Configuration) -TimeoutSec 10
            if ($Health.documentProcessing.ready -ne $true) { throw 'The processor did not confirm document processing as ready.' }
        }
        if ($CheckSoundEvents) {
            $Health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -Headers (Get-LocalDiagnosticHeaders $Configuration) -TimeoutSec 10
            if ($Health.localSoundEvents.available -ne $true) { throw 'The processor did not confirm the tested sound-event worker as available.' }
        }
        if ($CheckGoogleSignIn) {
            $Health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -Headers (Get-LocalDiagnosticHeaders $Configuration) -TimeoutSec 10
            if ($Health.firebaseAuth.enabled -ne $true -or $Health.firebaseAuth.projectId -ne $Configuration.firebaseAuth.projectId -or $Health.capabilities.accountProjects -ne $true) {
                throw 'The processor did not confirm Google sign-in for the requested Firebase project.'
            }
        }
    } catch {
        $Failure = $_.Exception.Message
        if (-not $Changed) {
            Remove-Item -LiteralPath $BackupDirectory -Recurse -Force
            throw "$Failure The application and configuration were not changed."
        }
        try {
            Stop-ProcessorForChange
            Copy-Item -LiteralPath (Join-Path $BackupDirectory 'config.json') -Destination $ConfigPath -Force
            foreach ($File in $Files) {
                if ($PreviousFiles[$File]) {
                    Copy-Item -LiteralPath (Join-Path $BackupDirectory $File) -Destination (Join-Path $InstallRoot $File) -Force
                } elseif (Test-Path -LiteralPath (Join-Path $InstallRoot $File)) {
                    Remove-Item -LiteralPath (Join-Path $InstallRoot $File) -Force
                }
            }
            if ($WasRunning) {
                Start-ScheduledTask -TaskName $TaskName
                if (-not (Wait-Processor 'http://127.0.0.1:8765' $Configuration.token)) { throw 'The restored processor did not become healthy.' }
            }
        } catch {
            throw "$Failure Recovery also needs attention: $($_.Exception.Message) Protected backup: $BackupDirectory"
        }
        throw "$Failure Previous application and configuration restored."
    }
    Remove-Item -LiteralPath $BackupDirectory -Recurse -Force
}
function Set-LocalTranscription($Configuration, [bool]$Enabled, [string]$StageDirectory = '') {
    Initialize-ServerConfiguration $Configuration
    $BackupDirectory = Join-Path $DownloadDir ('local-activation-backup-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $BackupDirectory | Out-Null
    Copy-Item -LiteralPath $ConfigPath -Destination (Join-Path $BackupDirectory 'config.json')
    $Files = $ProcessorFiles
    $PreviousFiles = @{}
    foreach ($File in $Files) {
        $PreviousFiles[$File] = Test-Path -LiteralPath (Join-Path $InstallRoot $File)
        if ($PreviousFiles[$File]) { Copy-Item -LiteralPath (Join-Path $InstallRoot $File) -Destination (Join-Path $BackupDirectory $File) }
    }
    $WasRunning = ((Get-ScheduledTask -TaskName $TaskName).State -eq 'Running')
    $Changed = $false
    try {
        Stop-ProcessorForChange
        $Changed = $true
        if ($StageDirectory) {
            foreach ($File in $Files) { Copy-Item -LiteralPath (Join-Path $StageDirectory $File) -Destination (Join-Path $InstallRoot $File) -Force }
        }
        $TempConfig = "$ConfigPath.local-part"
        Write-Utf8 $TempConfig ($Configuration | ConvertTo-Json -Depth 20)
        Move-Item -LiteralPath $TempConfig -Destination $ConfigPath -Force
        Invoke-Checked $PythonExe @((Join-Path $InstallRoot 'server.py'),'--check')
        Start-ScheduledTask -TaskName $TaskName
        if (-not (Wait-Processor 'http://127.0.0.1:8765' $Configuration.token)) { throw 'The processor did not restart after the local transcription change.' }
        if ($Enabled) {
            $Health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -Headers (Get-LocalDiagnosticHeaders $Configuration) -TimeoutSec 10
            if ($Health.localTranscription.available -ne $true) { throw 'The processor did not report local transcription as available.' }
        }
    } catch {
        $Failure = $_.Exception.Message
        if (-not $Changed) {
            Remove-Item -LiteralPath $BackupDirectory -Recurse -Force
            throw "$Failure The application and configuration were not changed."
        }
        try {
            Stop-ProcessorForChange
            Copy-Item -LiteralPath (Join-Path $BackupDirectory 'config.json') -Destination $ConfigPath -Force
            foreach ($File in $Files) {
                if ($PreviousFiles[$File]) {
                    Copy-Item -LiteralPath (Join-Path $BackupDirectory $File) -Destination (Join-Path $InstallRoot $File) -Force
                } elseif (Test-Path -LiteralPath (Join-Path $InstallRoot $File)) {
                    Remove-Item -LiteralPath (Join-Path $InstallRoot $File) -Force
                }
            }
            if ($WasRunning) {
                Start-ScheduledTask -TaskName $TaskName
                if (-not (Wait-Processor 'http://127.0.0.1:8765' $Configuration.token)) { throw 'The restored processor did not become healthy.' }
            }
        } catch {
            throw "$Failure Recovery also needs attention: $($_.Exception.Message) Protected backup: $BackupDirectory"
        }
        throw "$Failure Previous application and configuration restored."
    }
    Remove-Item -LiteralPath $BackupDirectory -Recurse -Force
}
function Install-LocalTranscription {
    $Configuration = Read-Configuration
    Assert-InstalledProcessor
    $SourceBase = "https://raw.githubusercontent.com/JRDN-R/vision/$SourceRef/vision-pc"
    $InstallId = [Guid]::NewGuid().ToString('N')
    $StageDirectory = Join-Path $DownloadDir ('local-transcription-' + $InstallId)
    $PluginRoot = Join-Path $InstallRoot 'plugins'
    $ModelRoot = Join-Path $InstallRoot 'models'
    $LogRoot = Join-Path $InstallRoot 'data'
    foreach ($Directory in @($StageDirectory,$PluginRoot,$ModelRoot,$LogRoot)) {
        New-Item -ItemType Directory -Path $Directory -Force | Out-Null
        if ((Get-Item -LiteralPath $Directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing linked folder: $Directory" }
    }
    Write-Stage '1/4 Preparing the optional CPU transcription plugin'
    foreach ($File in ($ProcessorFiles + @('requirements-local.txt'))) {
        Get-Download "$SourceBase/$File" (Join-Path $StageDirectory $File)
    }
    $CompileArguments = @('-m','py_compile') + @($ProcessorFiles | Where-Object { $_.EndsWith('.py') } | ForEach-Object { Join-Path $StageDirectory $_ })
    Invoke-Checked $PythonExe $CompileArguments
    # Unique directories isolate this attempt from a previously enabled model,
    # optional packages, and the base processor's site-packages.
    $PackagesPath = Join-Path $PluginRoot ('whisper-' + $InstallId)
    $ModelPath = Join-Path $ModelRoot ('whisper-small.en-' + $InstallId)
    New-Item -ItemType Directory -Path $PackagesPath | Out-Null
    Write-Stage '2/4 Installing private optional dependencies (the processor stays online)'
    Write-Host 'This can take several minutes. Download progress is saved in the data folder.'
    Invoke-LocalLogged @('-m','pip','--isolated','install','--index-url','https://pypi.org/simple','--disable-pip-version-check','--no-warn-script-location','--no-input','--only-binary=:all:','--no-cache-dir','--target',$PackagesPath,'-r',(Join-Path $StageDirectory 'requirements-local.txt')) (Join-Path $LogRoot ('local-install-' + $InstallId))
    Write-Stage '3/4 Downloading and testing the English model locally (about 486 MB)'
    Invoke-LocalLogged @((Join-Path $StageDirectory 'setup_local.py'),'--packages-path',$PackagesPath,'--model-path',$ModelPath,'--download') (Join-Path $LogRoot ('local-model-' + $InstallId))
    # Preserve configuration changes made while the download was in progress.
    $Configuration = Read-Configuration
    $Configuration | Add-Member -NotePropertyName localTranscription -NotePropertyValue ([pscustomobject]@{
        enabled = $true; modelPath = $ModelPath; packagesPath = $PackagesPath; cpuThreads = 4
    }) -Force
    Write-Stage '4/4 Enabling local transcription and restarting the processor'
    Write-Host 'The brief restart interrupts in-progress local media work; stored projects and conversations are retained.'
    Set-LocalTranscription $Configuration $true $StageDirectory
    Copy-Item -LiteralPath (Join-Path $StageDirectory 'setup_local.py') -Destination (Join-Path $InstallRoot 'setup_local.py') -Force
    if ($PSCommandPath -and [IO.Path]::GetFullPath($PSCommandPath) -ne (Join-Path $InstallRoot 'Setup-Vision-PC.ps1')) {
        Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $InstallRoot 'Setup-Vision-PC.ps1') -Force
    }
    Write-Host "`nLocal English transcription is ready: CPU int8, four threads, one worker." -ForegroundColor Green
    Write-Host 'No Gemini or OpenAI API calls are needed for this plugin. Keep the FUPCJ Server awake and online.'
    Write-Host 'Choose FUPCJ Server transcription in Vision. Other paid API features remain separate.'
}
function Invoke-SoundLogged([string]$Executable, [string[]]$Arguments, [string]$LogBase, [string]$Label = 'Sound-event') {
    $Quoted = @($Arguments | ForEach-Object { '"' + $_.Replace('"','\"') + '"' })
    $Process = Start-Process -FilePath $Executable -ArgumentList $Quoted -Wait -PassThru -NoNewWindow -RedirectStandardOutput "$LogBase.log" -RedirectStandardError "$LogBase-errors.log"
    if ($Process.ExitCode -ne 0) {
        Get-Content -LiteralPath "$LogBase.log" -Tail 10 | Out-Host
        Get-Content -LiteralPath "$LogBase-errors.log" -Tail 15 | Out-Host
        throw "$Label setup/check failed ($($Process.ExitCode)). See $LogBase.log and $LogBase-errors.log. Existing processor and Whisper settings were retained."
    }
    Get-Content -LiteralPath "$LogBase.log" -Tail 6 | Out-Host
}
function Assert-PrivateSoundPath([string]$Path, [bool]$File = $false) {
    if (-not $Path -or -not [IO.Path]::IsPathRooted($Path)) { throw 'The private sound runtime/assets path is invalid.' }
    $FullPath = [IO.Path]::GetFullPath($Path)
    $RootPrefix = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\') + '\'
    if (-not $FullPath.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Sound runtime/assets must stay in the protected Vision installation.' }
    $Current = Get-Item -LiteralPath $FullPath -Force
    if ($File -and $Current.PSIsContainer) { throw 'The sound Python executable is missing.' }
    while ($Current -and $Current.FullName.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        if ($Current.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing linked sound path: $($Current.FullName)" }
        if ($Current.PSIsContainer) { $Current = $Current.Parent } else { $Current = $Current.Directory }
    }
}
function Install-SoundEvents {
    $Configuration = Read-Configuration
    Assert-InstalledProcessor
    if ($Configuration.localTranscription.enabled -ne $true) {
        throw 'Combined speech and sound recognition requires local Whisper. Run -Action InstallLocalTranscription first (or EnableLocalTranscription if already installed), then InstallSoundEvents.'
    }
    $SpeechHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -Headers (Get-LocalDiagnosticHeaders $Configuration) -TimeoutSec 10
    if ($SpeechHealth.localTranscription.available -ne $true) {
        throw 'Local Whisper is not ready. Run -Action CheckLocalTranscription and resolve its reported issue before InstallSoundEvents.'
    }
    $InstallId = [Guid]::NewGuid().ToString('N')
    $SourceBase = "https://raw.githubusercontent.com/JRDN-R/vision/$SourceRef/vision-pc"
    $StageDirectory = Join-Path $DownloadDir ('sound-install-' + $InstallId)
    $PluginRoot = Join-Path $InstallRoot 'plugins'
    $ModelRoot = Join-Path $InstallRoot 'models'
    $LogRoot = Join-Path $InstallRoot 'data'
    foreach ($Directory in @($StageDirectory,$PluginRoot,$ModelRoot,$LogRoot)) {
        New-Item -ItemType Directory -Path $Directory -Force | Out-Null
        Assert-PrivateSoundPath $Directory
    }
    Write-Stage '1/5 Preparing the optional sound-event worker'
    foreach ($File in $ProcessorFiles) { Get-Download "$SourceBase/$File" (Join-Path $StageDirectory $File) }
    $CompileArguments = @('-m','py_compile') + @($ProcessorFiles | Where-Object { $_.EndsWith('.py') } | ForEach-Object { Join-Path $StageDirectory $_ })
    Invoke-Checked $PythonExe $CompileArguments
    $RequirementsHash = (Get-FileHash -LiteralPath (Join-Path $StageDirectory 'requirements-sound.txt') -Algorithm SHA256).Hash
    $SoundPythonVersion = '3.11.9'
    $SoundPythonHash = '009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b'
    $SoundRuntime = Join-Path $PluginRoot ('sound-runtime-' + $InstallId)
    $SoundPython = Join-Path $SoundRuntime 'python.exe'
    $ReuseRuntime = $false
    if ($Configuration.localSoundEvents.pythonPath) {
        $ExistingPython = [string]$Configuration.localSoundEvents.pythonPath
        Assert-PrivateSoundPath $ExistingPython $true
        $ExistingRuntime = Split-Path -Parent $ExistingPython
        $RuntimeMarker = Join-Path $ExistingRuntime 'vision-sound-runtime.json'
        if (Test-Path -LiteralPath $RuntimeMarker) {
            $SavedRuntime = Get-Content -LiteralPath $RuntimeMarker -Raw | ConvertFrom-Json
            if ($SavedRuntime.pythonVersion -eq $SoundPythonVersion -and $SavedRuntime.requirementsSha256 -eq $RequirementsHash) {
                $SoundRuntime = $ExistingRuntime
                $SoundPython = $ExistingPython
                $ReuseRuntime = $true
            }
        }
    }
    Write-Stage '2/5 Preparing a separate private Python runtime (Whisper stays unchanged)'
    if (-not $ReuseRuntime) {
        New-Item -ItemType Directory -Path $SoundRuntime | Out-Null
        Assert-PrivateSoundPath $SoundRuntime
        $Archive = Join-Path $DownloadDir "python-$SoundPythonVersion-sound-embed-amd64.zip"
        if (-not (Test-Path -LiteralPath $Archive) -or (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash -ne $SoundPythonHash) {
            Get-Download "https://www.python.org/ftp/python/$SoundPythonVersion/python-$SoundPythonVersion-embed-amd64.zip" $Archive
        }
        if ((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash -ne $SoundPythonHash) { throw 'The sound runtime download checksum did not match.' }
        Expand-Archive -LiteralPath $Archive -DestinationPath $SoundRuntime -Force
        $Signature = Get-AuthenticodeSignature -FilePath $SoundPython
        if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw 'Sound runtime signature could not be verified.' }
        # The path file contains only this runtime's packages and the protected
        # application source. It never imports the Whisper package directory.
        Write-Utf8 (Join-Path $SoundRuntime 'python311._pth') "python311.zip`r`n.`r`nLib\site-packages`r`n$InstallRoot`r`nimport site`r`n"
        Write-Stage '3/5 Installing the private CPU model dependencies'
        Write-Host 'The processor remains online during downloads. Progress is recorded in the data folder.'
        # pip's --python targets the optional interpreter, even without pip in it.
        # This does not install/upgrade anything in the running processor runtime.
        Invoke-SoundLogged $PythonExe @('-m','pip','--isolated','--python',$SoundPython,'install','--index-url','https://pypi.org/simple','--disable-pip-version-check','--no-warn-script-location','--no-input','--only-binary=:all:','-r',(Join-Path $StageDirectory 'requirements-sound.txt')) (Join-Path $LogRoot ('sound-install-' + $InstallId))
    } else {
        Write-Stage '3/5 Reusing the isolated sound runtime and cached dependencies'
    }
    $AssetsPath = Join-Path $ModelRoot 'sound-beats-db13a79ae90a'
    New-Item -ItemType Directory -Path $AssetsPath -Force | Out-Null
    Assert-PrivateSoundPath $AssetsPath
    Write-Stage '4/5 Verifying the model download and running offline inference'
    Write-Host 'The trained checkpoint is about 364 MB and is downloaded once. No training dataset or paid API is used.'
    $CheckScript = Join-Path $StageDirectory 'setup_sound_events.py'
    # setup_sound_events launches the worker with its own interpreter; the
    # worker imports its sibling source path explicitly.
    Invoke-SoundLogged $SoundPython @($CheckScript,'--assets',$AssetsPath,'--manifest',(Join-Path $StageDirectory 'sound-model-manifest.json'),'--worker',(Join-Path $StageDirectory 'sound_model.py'),'--download','--threads','2') (Join-Path $LogRoot ('sound-model-' + $InstallId))
    Write-Utf8 (Join-Path $SoundRuntime 'vision-sound-runtime.json') (([ordered]@{ pythonVersion = $SoundPythonVersion; requirementsSha256 = $RequirementsHash } | ConvertTo-Json))
    # Read current settings only after all downloads and actual inference pass.
    $Configuration = Read-Configuration
    $Configuration | Add-Member -NotePropertyName localSoundEvents -NotePropertyValue ([pscustomobject]@{
        enabled = $true; pythonPath = $SoundPython; assetsPath = $AssetsPath; device = 'cpu'; cpuThreads = 2
    }) -Force
    Write-Stage '5/5 Enabling sound events with a brief processor restart'
    Write-Host 'Stored projects, models and API settings are retained. Active processing may briefly reconnect.'
    Set-ProcessorUpdate $Configuration $StageDirectory ($Configuration.firebaseAuth.enabled -eq $true) $true
    if ($PSCommandPath -and [IO.Path]::GetFullPath($PSCommandPath) -ne (Join-Path $InstallRoot 'Setup-Vision-PC.ps1')) {
        Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $InstallRoot 'Setup-Vision-PC.ps1') -Force
    }
    Write-Host "`nSound-event recognition is ready on FUPCJ Server: CPU, two threads, one low-priority worker." -ForegroundColor Green
    Write-Host 'Refresh Vision and select Include sound effects when processing audio or video. Speech-only processing is unchanged.'
}
function Check-SoundEvents {
    $Configuration = Read-Configuration
    Assert-InstalledProcessor
    if (-not $Configuration.localSoundEvents.pythonPath -or -not $Configuration.localSoundEvents.assetsPath) { throw 'Install sound recognition first with -Action InstallSoundEvents.' }
    Assert-PrivateSoundPath ([string]$Configuration.localSoundEvents.pythonPath) $true
    Assert-PrivateSoundPath ([string]$Configuration.localSoundEvents.assetsPath)
    Invoke-SoundLogged ([string]$Configuration.localSoundEvents.pythonPath) @((Join-Path $InstallRoot 'setup_sound_events.py'),'--assets',[string]$Configuration.localSoundEvents.assetsPath,'--threads','2') (Join-Path $InstallRoot ('data\sound-check-' + [Guid]::NewGuid().ToString('N')))
    Write-Host 'The installed sound-event model passed offline inference. No downloads or server restart were needed.' -ForegroundColor Green
}
function Disable-SoundEvents {
    $Configuration = Read-Configuration
    Assert-InstalledProcessor
    if (-not $Configuration.localSoundEvents -or $Configuration.localSoundEvents.enabled -ne $true) {
        Write-Host 'Sound events are already disabled. No restart was needed.'
        return
    }
    # Use the same transaction and rollback path without downloading code.
    $StageDirectory = Join-Path $DownloadDir ('sound-disable-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $StageDirectory | Out-Null
    foreach ($File in $ProcessorFiles) { Copy-Item -LiteralPath (Join-Path $InstallRoot $File) -Destination (Join-Path $StageDirectory $File) }
    $Configuration.localSoundEvents.enabled = $false
    Set-ProcessorUpdate $Configuration $StageDirectory
    Write-Host 'Sound events disabled. Models and completed transcripts are retained; speech transcription remains available.'
}
function Export-Connection {
    $Config = Read-Configuration
    if (-not $Config.backendUrl) { throw 'Tailscale HTTPS is not configured yet. Run Setup again.' }
    $LocalOnline = Test-Processor "http://127.0.0.1:$($Config.port)" $Config.token
    $RemoteOnline = Test-Processor $Config.backendUrl $Config.token
    if (-not ($LocalOnline -and $RemoteOnline)) {
        throw 'The processor is not reachable over its HTTPS address yet. Keep this FUPCJ Server connected, then run this script with -Action Start.'
    }
    if (-not (Test-Path -LiteralPath $OutputDirectory)) { New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null }
    $PublicAccess = ($Config.publicAccess -eq $true)
    $Connection = [ordered]@{ kind = 'private-pc'; backendUrl = $Config.backendUrl; accessToken = $Config.token; publicAccess = $PublicAccess }
    $ConnectionJson = $Connection | ConvertTo-Json
    $PrivatePath = Join-Path $OutputDirectory 'Vision-Connection.txt'
    $PublicPath = Join-Path $OutputDirectory 'Vision-Connection-public.txt'
    $ConnectionType = 'private-pc / Tailscale Serve HTTPS'
    $ConnectionInstructions = 'Keep Tailscale connected on this FUPCJ Server and on each device using Vision.'
    $ReportInstructions = 'Access requires Tailscale on the same private network and your Vision connection settings.'
    $AccessInstructions = 'Anyone with these settings can use the processor while it is available.'
    if ($PublicAccess) {
        $ConnectionType = 'private-pc / public Tailscale Funnel HTTPS'
        $ConnectionInstructions = 'Keep this FUPCJ Server awake and online. Devices using Vision do not need Tailscale.'
        $ReportInstructions = 'Access works over the internet using the Vision connection settings. Only the FUPCJ Server needs Tailscale.'
    }
    if ($Config.firebaseAuth.enabled -eq $true) {
        $AccessInstructions = 'Google sign-in is required. These connection settings do not grant access to user accounts or projects.'
        $ReportInstructions = 'The HTTPS connection is reachable. Sign in with Google in Vision to access your own projects.'
    }
    $PrivateText = @"
VISION PROCESSOR CONNECTION
This file contains the access token for your processor. Vision HTML can embed these settings to connect automatically.
$AccessInstructions
$ConnectionInstructions

--- BEGIN VISION CONNECTION ---
$ConnectionJson
--- END VISION CONNECTION ---
"@
    Write-Utf8 $PrivatePath $PrivateText
    # Keep the exported credential readable only by this Windows user, SYSTEM and administrators.
    $FileAcl = New-Object Security.AccessControl.FileSecurity
    $FileAcl.SetAccessRuleProtection($true, $false)
    $Sids = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, 'S-1-5-18', 'S-1-5-32-544') | Select-Object -Unique
    foreach ($SidValue in $Sids) {
        $Sid = New-Object Security.Principal.SecurityIdentifier($SidValue)
        $FileAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($Sid,'FullControl','Allow')))
    }
    Set-Acl -LiteralPath $PrivatePath -AclObject $FileAcl
    $PublicText = @"
VISION PROCESSOR CONNECTION REPORT - NO ACCESS TOKEN
Created: $([DateTime]::UtcNow.ToString('yyyy-MM-dd HH:mm:ss')) UTC
Backend: $($Config.backendUrl)
Connection type: $ConnectionType
Public access: $PublicAccess
Local processor: online
HTTPS: online
Startup task: $TaskName
Installed at: $InstallRoot
Computer: $env:COMPUTERNAME
Windows: $([Environment]::OSVersion.VersionString)
$ReportInstructions
This report can be shared for help. It cannot authorize connections by itself.
"@
    Write-Utf8 $PublicPath $PublicText
    Write-Host "`nReady. Connection files saved to:" -ForegroundColor Green
    Write-Host $PrivatePath
    Write-Host $PublicPath
    Write-Host 'Vision HTML with these settings already embedded connects automatically. Otherwise import Vision-Connection.txt in Vision logo > Processor connection.'
}

try {
    $Principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Open Windows PowerShell with Run as administrator, then run this script again.'
    }
    if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
        throw 'This installer needs 64-bit Windows on an Intel/AMD computer and 64-bit Windows PowerShell.'
    }
    if ($SourceRef -notmatch '^[A-Za-z0-9._-]+$') { throw 'Invalid source revision.' }
    if ($Action -eq 'EnableGoogleSignIn' -and $FirebaseProjectId -notmatch '^[a-z][a-z0-9-]{4,28}[a-z0-9]$') {
        throw 'Use the Firebase project ID, such as visionboard-api, rather than an app ID or URL.'
    }
    if ($Action -eq 'Start') {
        $Config = Read-Configuration
        $Tailscale = Get-Tailscale
        if (-not $Tailscale) { throw 'Tailscale is missing. Run Setup again.' }
        $DnsName = Connect-Tailscale $Tailscale $Config
        Assert-VisionRoute $Tailscale $DnsName ($Config.publicAccess -eq $true)
        Start-ScheduledTask -TaskName $TaskName
        if (-not (Wait-Processor 'http://127.0.0.1:8765' $Config.token)) { throw "The processor did not start. Check $InstallRoot\data\server.log." }
        Start-VisionRoute $Tailscale $Config
        Export-Connection
        Write-Host 'Processor started. Its saved public/private connection mode has been restored.'
        exit 0
    }
    if ($Action -eq 'Stop') { Stop-ScheduledTask -TaskName $TaskName; Write-Host 'Processor stopped. The startup task remains installed.'; exit 0 }
    if ($Action -eq 'ExportConnection') { Export-Connection; exit 0 }
    if ($Action -eq 'InstallLocalTranscription') { Install-LocalTranscription; exit 0 }
    if ($Action -eq 'InstallDocumentTools') {
        Assert-InstalledProcessor
        $DocumentHelper = Join-Path $DownloadDir ('document-tools-'+[Guid]::NewGuid().ToString('N')+'.ps1')
        Get-Download "https://raw.githubusercontent.com/JRDN-R/vision/$SourceRef/vision-pc/Document-Tools.ps1" $DocumentHelper
        . $DocumentHelper
        try { Install-DocumentTools $PSCommandPath } finally { Remove-Item -LiteralPath $DocumentHelper -ErrorAction SilentlyContinue }
        exit 0
    }
    if ($Action -eq 'InstallSoundEvents') { Install-SoundEvents; exit 0 }
    if ($Action -eq 'CheckSoundEvents') { Check-SoundEvents; exit 0 }
    if ($Action -eq 'DisableSoundEvents') { Disable-SoundEvents; exit 0 }
    if ($Action -eq 'CheckLocalTranscription') {
        Assert-InstalledProcessor
        $CheckScript = Join-Path $InstallRoot 'setup_local.py'
        if (-not (Test-Path -LiteralPath $CheckScript)) { throw 'Run -Action Update once to install the local transcription check.' }
        $LocalLog = Join-Path $InstallRoot ('data\local-check-' + [Guid]::NewGuid().ToString('N'))
        Invoke-LocalLogged @($CheckScript,'--check-server',$ConfigPath) $LocalLog
        Write-Host 'The installed background processor passed. Retry your recording in Vision.' -ForegroundColor Green
        exit 0
    }
    if ($Action -eq 'EnableLocalTranscription') {
        $Config = Read-Configuration
        Assert-InstalledProcessor
        if (-not $Config.localTranscription -or -not (Test-Path -LiteralPath (Join-Path $InstallRoot 'setup_local.py'))) {
            throw 'Install the optional model first with -Action InstallLocalTranscription.'
        }
        $LocalLog = Join-Path $InstallRoot ('data\local-check-' + [Guid]::NewGuid().ToString('N'))
        Invoke-LocalLogged @((Join-Path $InstallRoot 'setup_local.py'),'--packages-path',[string]$Config.localTranscription.packagesPath,'--model-path',[string]$Config.localTranscription.modelPath) $LocalLog
        $Config.localTranscription.enabled = $true
        Set-LocalTranscription $Config $true
        Write-Host 'Local transcription enabled using the existing downloaded model. No downloads were needed.'
        exit 0
    }
    if ($Action -eq 'DisableLocalTranscription') {
        $Config = Read-Configuration
        Assert-InstalledProcessor
        if (-not $Config.localTranscription -or $Config.localTranscription.enabled -ne $true) {
            Write-Host 'Local transcription is already disabled. No restart was needed.'
            exit 0
        }
        $Config.localTranscription.enabled = $false
        Set-LocalTranscription $Config $false
        Write-Host 'Local transcription disabled. The processor restarted; model files and saved work are retained.'
        exit 0
    }

    if ($Action -in @('EnablePublic','Update','EnableGoogleSignIn')) {
        Write-Stage '1/3 Updating the installed processor and saved sessions'
        $Config = Read-Configuration
        Assert-InstalledProcessor
        $Tailscale = Get-Tailscale
        if (-not $Tailscale) { throw 'Tailscale is missing. Run Setup first.' }
        $DnsName = Connect-Tailscale $Tailscale $Config
        $TargetPublic = ($Action -eq 'EnablePublic' -or $Config.publicAccess -eq $true)
        Assert-VisionRoute $Tailscale $DnsName $TargetPublic
        $SourceBase = "https://raw.githubusercontent.com/JRDN-R/vision/$SourceRef/vision-pc"
        $StageDirectory = Join-Path $DownloadDir ('processor-update-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $StageDirectory | Out-Null
        # Stage source and install only missing/incompatible base dependencies
        # before the brief activation restart. Whisper packages/model are separate.
        foreach ($FileName in $ProcessorFiles) {
            Get-Download "$SourceBase/$FileName" (Join-Path $StageDirectory $FileName)
        }
        $CompileArguments = @('-m','py_compile') + @($ProcessorFiles | Where-Object { $_.EndsWith('.py') } | ForEach-Object { Join-Path $StageDirectory $_ })
        Invoke-Checked $PythonExe $CompileArguments
        Invoke-Checked $PythonExe @('-m','pip','--isolated','install','--index-url','https://pypi.org/simple','--disable-pip-version-check','--no-warn-script-location','--no-input','--only-binary=:all:','-r',(Join-Path $StageDirectory 'requirements.txt'))
        Invoke-Checked $PythonExe @('-c','import jwt; from jwt.algorithms import RSAAlgorithm')
        # Reload after downloads to retain any intervening configuration changes.
        $Config = Read-Configuration
        $Config | Add-Member -NotePropertyName publicAccess -NotePropertyValue $TargetPublic -Force
        if ($Action -eq 'EnableGoogleSignIn') {
            if ($Config.firebaseAuth.enabled -eq $true -and $Config.firebaseAuth.projectId -ne $FirebaseProjectId) {
                throw 'Google sign-in is already enabled for another Firebase project. No account configuration was replaced.'
            }
            $Config | Add-Member -NotePropertyName firebaseAuth -NotePropertyValue ([pscustomobject]@{
                enabled = $true; projectId = $FirebaseProjectId
            }) -Force
        }
        Write-Host 'Briefly restarting Vision. Saved projects, connection settings and installed models are retained.'
        Set-ProcessorUpdate $Config $StageDirectory ($Config.firebaseAuth.enabled -eq $true)
        if ($PSCommandPath -and [IO.Path]::GetFullPath($PSCommandPath) -ne (Join-Path $InstallRoot 'Setup-Vision-PC.ps1')) {
            Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $InstallRoot 'Setup-Vision-PC.ps1') -Force
        }
        Write-Stage '2/3 Restoring the persistent HTTPS connection'
        Start-VisionRoute $Tailscale $Config
        Write-Stage '3/3 Checking HTTPS and exporting your connection files'
        if (-not (Wait-Processor $Config.backendUrl $Config.token)) { throw 'HTTPS is not ready. Finish any Funnel approval, then run this same command again.' }
        Export-Connection
        Write-Host "`nProcessor updated. Project saves and background conversations are ready." -ForegroundColor Green
        if ($Config.firebaseAuth.enabled -eq $true) {
            Write-Host "Google sign-in is enabled for Firebase project: $($Config.firebaseAuth.projectId)" -ForegroundColor Green
            Write-Host 'In Firebase Authentication, enable Google and authorize jrdn-r.github.io. Then refresh Vision and sign in.'
            Write-Host 'Firebase verifies your Google login; projects and files stay on this FUPCJ Server. No service-account key is needed.'
        }
        if ($TargetPublic) { Write-Host 'Ready for local HTML and GitHub Pages. Only this FUPCJ Server needs Tailscale.' }
        Write-Host "User activity logs: $($Config.auditLogDir)"
        Write-Host 'Keep this FUPCJ Server awake and online. The processor and Funnel resume automatically after a Windows restart.'
        exit 0
    }

    Write-Stage '1/5 Preparing the private application folder'
    if (Test-Path -LiteralPath $InstallRoot) {
        $ExistingOwner = (Get-Acl -LiteralPath $InstallRoot).GetOwner([Security.Principal.SecurityIdentifier]).Value
        if ($ExistingOwner -notin @('S-1-5-18','S-1-5-32-544')) {
            throw 'The installation folder already exists with an unexpected owner. Choose an administrator-reviewed clean installation folder before continuing.'
        }
    }
    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    Set-PrivateDirectory $InstallRoot
    foreach ($Directory in @($RuntimeDir,$ToolsDir,$DownloadDir,(Join-Path $InstallRoot 'data'))) {
        New-Item -ItemType Directory -Path $Directory -Force | Out-Null
        if ((Get-Item -LiteralPath $Directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing linked folder: $Directory" }
    }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $TaskName }
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        $Bytes = New-Object byte[] 32
        $Generator = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $Generator.GetBytes($Bytes) } finally { $Generator.Dispose() }
        $Token = [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
        Write-Utf8 $ConfigPath (([ordered]@{ token = $Token; backendUrl = ''; port = 8765 } | ConvertTo-Json))
    }
    $Config = Read-Configuration
    Initialize-ServerConfiguration $Config
    Write-Utf8 $ConfigPath ($Config | ConvertTo-Json -Depth 20)
    if ($PSCommandPath -and [IO.Path]::GetFullPath($PSCommandPath) -ne (Join-Path $InstallRoot 'Setup-Vision-PC.ps1')) {
        Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $InstallRoot 'Setup-Vision-PC.ps1') -Force
    }

    Write-Stage '2/5 Downloading the processor and its private runtime'
    $SourceBase = "https://raw.githubusercontent.com/JRDN-R/vision/$SourceRef/vision-pc"
    foreach ($FileName in $ProcessorFiles) {
        Get-Download "$SourceBase/$FileName" (Join-Path $InstallRoot $FileName)
    }
    if (-not (Test-Path -LiteralPath $PythonExe)) {
        $PythonVersion = '3.13.13'
        $Archive = Join-Path $DownloadDir 'python.zip'
        Get-Download "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" $Archive
        Expand-Archive -LiteralPath $Archive -DestinationPath $RuntimeDir -Force
        $Signature = Get-AuthenticodeSignature -FilePath $PythonExe
        if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw 'Python runtime signature could not be verified.' }
    }
    # The embedded runtime uses only its own application paths, not a system Python install.
    $Pth = Get-ChildItem -LiteralPath $RuntimeDir -Filter 'python*._pth' | Select-Object -First 1
    if (-not $Pth) { throw 'The private runtime is incomplete. Remove its runtime folder and run Setup again.' }
    Write-Utf8 $Pth.FullName "python313.zip`r`n.`r`n..`r`nLib\site-packages`r`nimport site`r`n"
    if (-not (Test-Path -LiteralPath (Join-Path $RuntimeDir 'Lib\site-packages\pip'))) {
        $GetPip = Join-Path $DownloadDir 'get-pip.py'
        Get-Download 'https://bootstrap.pypa.io/get-pip.py' $GetPip
        Invoke-Checked $PythonExe @($GetPip,'--disable-pip-version-check','--no-warn-script-location')
    }
    Invoke-Checked $PythonExe @('-m','pip','install','--disable-pip-version-check','--no-warn-script-location','--upgrade','-r',(Join-Path $InstallRoot 'requirements.txt'))

    if (-not (Test-Path -LiteralPath (Join-Path $ToolsDir 'ffmpeg.exe')) -or -not (Test-Path -LiteralPath (Join-Path $ToolsDir 'ffprobe.exe'))) {
        Write-Host 'Downloading video tools...'
        $Archive = Join-Path $DownloadDir 'ffmpeg.zip'
        $FfmpegUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
        Get-Download $FfmpegUrl $Archive
        Assert-Checksum $Archive "$FfmpegUrl.sha256"
        $ExpandTo = Join-Path $DownloadDir 'ffmpeg'
        Expand-Archive -LiteralPath $Archive -DestinationPath $ExpandTo -Force
        foreach ($Executable in @('ffmpeg.exe','ffprobe.exe')) {
            $Found = Get-ChildItem -LiteralPath $ExpandTo -Recurse -Filter $Executable | Select-Object -First 1
            if (-not $Found) { throw "Video tool missing: $Executable" }
            Copy-Item -LiteralPath $Found.FullName -Destination (Join-Path $ToolsDir $Executable) -Force
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ToolsDir 'deno.exe'))) {
        $Archive = Join-Path $DownloadDir 'deno.zip'
        Get-Download 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' $Archive
        Expand-Archive -LiteralPath $Archive -DestinationPath $ToolsDir -Force
    }
    Invoke-Checked (Join-Path $ToolsDir 'ffmpeg.exe') @('-version')
    Invoke-Checked (Join-Path $ToolsDir 'deno.exe') @('--version')

    Write-Stage '3/5 Connecting Tailscale'
    $Tailscale = Get-Tailscale
    if (-not $Tailscale) {
        $Index = (Invoke-WebRequest -UseBasicParsing 'https://pkgs.tailscale.com/stable/' -TimeoutSec 60).Content
        $Match = [regex]::Match([string]$Index, 'tailscale-setup-[0-9.]+-amd64\.msi')
        if (-not $Match.Success) { throw 'Could not find the official Tailscale Windows installer.' }
        $Msi = Join-Path $DownloadDir 'tailscale.msi'
        $MsiUrl = "https://pkgs.tailscale.com/stable/$($Match.Value)"
        Get-Download $MsiUrl $Msi
        Assert-Checksum $Msi "$MsiUrl.sha256"
        $Signature = Get-AuthenticodeSignature -FilePath $Msi
        if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'Tailscale') { throw 'Tailscale installer signature could not be verified.' }
        $Installer = Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/i',('"' + $Msi + '"'),'/qn','/norestart') -Wait -PassThru
        if ($Installer.ExitCode -notin @(0,3010)) { throw "Tailscale installation failed ($($Installer.ExitCode))." }
        $Tailscale = Get-Tailscale
        if (-not $Tailscale) { throw 'Tailscale was not found after installation. Restart Windows, then run Setup again.' }
    }
    $DnsName = Connect-Tailscale $Tailscale $Config

    Write-Stage '4/5 Starting the background processor'
    Assert-VisionRoute $Tailscale $DnsName ($Config.publicAccess -eq $true)
    Register-ProcessorTask
    Start-ScheduledTask -TaskName $TaskName
    if (-not (Wait-Processor 'http://127.0.0.1:8765' $Config.token)) { throw "The processor did not start. Check $InstallRoot\data\server.log and Task Scheduler > $TaskName." }
    Start-VisionRoute $Tailscale $Config
    Write-Stage '5/5 Checking HTTPS and exporting your connection files'
    if (-not (Wait-Processor $Config.backendUrl $Config.token)) { throw 'HTTPS is not ready. Complete any Tailscale HTTPS/Funnel approval, then run Setup again.' }
    Export-Connection
    Write-Host "User activity logs: $($Config.auditLogDir)"
    Write-Host "`nKeep this FUPCJ Server awake while processing. Closing this setup window is fine."
    Write-Host 'Windows Settings > System > Power & sleep > Sleep > Never (while plugged in).'
} catch {
    Write-Host "`nSetup stopped: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'You can run the same command again after fixing the issue. Existing connection tokens are preserved.'
    exit 1
}
