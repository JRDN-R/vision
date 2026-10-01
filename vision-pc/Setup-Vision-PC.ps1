#requires -Version 5.1
<#
Vision private PC processor. Run from Windows PowerShell as Administrator.
The application downloads its own private runtime; no existing Python/Node install is needed.
#>
[CmdletBinding()]
param(
    [ValidateSet('Setup','ExportConnection','Start','Stop')]
    [string]$Action = 'Setup',
    [string]$OutputDirectory = [Environment]::GetFolderPath('Desktop'),
    [string]$SourceRef = 'main'
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
    return Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
}
function Test-Processor([string]$BaseUrl, [string]$Token) {
    try {
        $Result = Invoke-RestMethod -Uri "$BaseUrl/api/health" -Headers @{ Authorization = "Bearer $Token" } -TimeoutSec 10
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
function Export-Connection {
    $Config = Read-Configuration
    if (-not $Config.backendUrl) { throw 'Tailscale HTTPS is not configured yet. Run Setup again.' }
    $LocalOnline = Test-Processor "http://127.0.0.1:$($Config.port)" $Config.token
    $RemoteOnline = Test-Processor $Config.backendUrl $Config.token
    if (-not ($LocalOnline -and $RemoteOnline)) {
        throw 'The processor is not reachable over its private HTTPS address yet. Keep Tailscale connected, then run Setup again.'
    }
    if (-not (Test-Path -LiteralPath $OutputDirectory)) { New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null }
    $Connection = [ordered]@{ kind = 'private-pc'; backendUrl = $Config.backendUrl; accessToken = $Config.token }
    $ConnectionJson = $Connection | ConvertTo-Json
    $PrivatePath = Join-Path $OutputDirectory 'Vision-Connection.txt'
    $PublicPath = Join-Path $OutputDirectory 'Vision-Connection-public.txt'
    $PrivateText = @"
VISION PRIVATE CONNECTION - KEEP PRIVATE
This file contains the access token for your processor. Import it into Vision on your own devices.
Do not post this file publicly or send it for support. Send Vision-Connection-public.txt instead.
Keep Tailscale connected on this PC and on each device using Vision.

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
Connection type: private-pc / Tailscale Serve HTTPS
Local processor: online
Private HTTPS: online
Startup task: $TaskName
Installed at: $InstallRoot
Computer: $env:COMPUTERNAME
Windows: $([Environment]::OSVersion.VersionString)
Access requires Tailscale on the same private network and your private Vision connection file.
This report can be shared for help. It cannot authorize connections by itself.
"@
    Write-Utf8 $PublicPath $PublicText
    Write-Host "`nReady. Connection files saved to:" -ForegroundColor Green
    Write-Host $PrivatePath
    Write-Host $PublicPath
    Write-Host 'Import Vision-Connection.txt in Vision logo > Processor connection. Share only the -public file for help.'
}

try {
    $Principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Open Windows PowerShell with Run as administrator, then run this script again.'
    }
    if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
        throw 'This installer needs 64-bit Windows on an Intel/AMD PC and 64-bit Windows PowerShell.'
    }
    if ($SourceRef -notmatch '^[A-Za-z0-9._-]+$') { throw 'Invalid source revision.' }
    if ($Action -eq 'Start') { Start-ScheduledTask -TaskName $TaskName; Write-Host 'Processor started.'; exit 0 }
    if ($Action -eq 'Stop') { Stop-ScheduledTask -TaskName $TaskName; Write-Host 'Processor stopped. The startup task remains installed.'; exit 0 }
    if ($Action -eq 'ExportConnection') { Export-Connection; exit 0 }

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
    if ([string]$Config.token -notmatch '^[A-Za-z0-9_-]{40,128}$' -or [int]$Config.port -ne 8765) { throw 'Existing config.json is invalid. No credentials were replaced.' }
    if ($PSCommandPath -and [IO.Path]::GetFullPath($PSCommandPath) -ne (Join-Path $InstallRoot 'Setup-Vision-PC.ps1')) {
        Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $InstallRoot 'Setup-Vision-PC.ps1') -Force
    }

    Write-Stage '2/5 Downloading the processor and its private runtime'
    $SourceBase = "https://raw.githubusercontent.com/JRDN-R/vision/$SourceRef/vision-pc"
    foreach ($FileName in @('server.py','media.py','requirements.txt')) {
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
    Start-Service -Name 'Tailscale'
    $StatusText = & $Tailscale status --json
    $Status = $StatusText | ConvertFrom-Json
    if ($Status.BackendState -ne 'Running') {
        Write-Host 'Sign in using the Tailscale link below. Use this same account on your phone/computers.'
        & $Tailscale up --unattended=true --timeout=3m
        if ($LASTEXITCODE -ne 0) { throw 'Finish the Tailscale sign-in, then run this same script again.' }
    }
    Invoke-Checked $Tailscale @('set','--unattended=true')
    $Status = (& $Tailscale status --json) | ConvertFrom-Json
    $DnsName = ([string]$Status.Self.DNSName).TrimEnd('.')
    if ($Status.BackendState -ne 'Running' -or $DnsName -notmatch '^[A-Za-z0-9.-]+\.ts\.net$') { throw 'Tailscale is not connected with a private DNS name yet. Complete sign-in and rerun Setup.' }
    $Config.backendUrl = "https://$DnsName"
    Write-Utf8 $ConfigPath ($Config | ConvertTo-Json)

    Write-Stage '4/5 Starting the background processor'
    $TaskAction = New-ScheduledTaskAction -Execute $PythonExe -Argument ('"' + (Join-Path $InstallRoot 'server.py') + '"') -WorkingDirectory $InstallRoot
    $Trigger = New-ScheduledTaskTrigger -AtStartup
    $TaskPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $TaskAction -Trigger $Trigger -Principal $TaskPrincipal -Settings $Settings -Description 'Vision private YouTube processor. Listens only on this computer; Tailscale provides the private HTTPS connection.' -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    if (-not (Wait-Processor 'http://127.0.0.1:8765' $Config.token)) { throw "The processor did not start. Check $InstallRoot\data\server.log and Task Scheduler > $TaskName." }

    # Do not reset, replace or publish another application's existing Tailscale route.
    $ServeText = & $Tailscale serve status --json
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the existing Tailscale Serve configuration.' }
    $ServeConfig = $ServeText | ConvertFrom-Json
    $WebEntries = @($ServeConfig.Web.PSObject.Properties)
    $HasRoute = $false
    foreach ($Entry in $WebEntries) {
        if (-not $Entry) { continue }
        foreach ($Handler in @($Entry.Value.Handlers.PSObject.Properties)) {
            $HasRoute = $true
            if ($Entry.Name -ne ($DnsName + ':443') -or $Handler.Name -ne '/' -or $Handler.Value.Proxy -ne 'http://127.0.0.1:8765') {
                throw 'This PC already has a different Tailscale Serve route. Nothing was overwritten. Describe that existing setup for help.'
            }
        }
    }
    $TcpEntries = @($ServeConfig.TCP.PSObject.Properties | Where-Object { $_ })
    if ($TcpEntries.Count -gt 0 -and (-not $HasRoute -or $TcpEntries.Count -ne 1 -or $TcpEntries[0].Name -ne '443' -or $TcpEntries[0].Value.HTTPS -ne $true)) {
        throw 'An existing Tailscale TCP service uses this PC. Nothing was overwritten.'
    }
    if (@($ServeConfig.AllowFunnel.PSObject.Properties | Where-Object { $_.Value -eq $true }).Count -gt 0) {
        throw 'Tailscale Funnel is enabled here. This installer needs a private-only route and did not modify your existing setup.'
    }
    Write-Host 'If Tailscale prints an HTTPS approval link, open it and enable HTTPS for your private network.'
    & $Tailscale serve --bg --yes http://127.0.0.1:8765
    if ($LASTEXITCODE -ne 0) { throw 'Approve HTTPS using the Tailscale link, then rerun this script. Your installation and token are saved.' }
    Write-Stage '5/5 Checking HTTPS and exporting your connection files'
    if (-not (Wait-Processor $Config.backendUrl $Config.token)) { throw 'Private HTTPS is not ready. Complete any Tailscale HTTPS approval, then run Setup again.' }
    Export-Connection
    Write-Host "`nKeep this PC awake while processing. Closing this setup window is fine."
    Write-Host 'Windows Settings > System > Power & sleep > Sleep > Never (while plugged in).'
} catch {
    Write-Host "`nSetup stopped: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'You can run the same command again after fixing the issue. Existing connection tokens are preserved.'
    exit 1
}
