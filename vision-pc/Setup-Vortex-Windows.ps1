#requires -Version 5.1
<#
Run in 64-bit Windows PowerShell as administrator, using the Windows account
that normally signs into this PC. Installs optional WSL/Docker prerequisites
and Cobalt for an existing Vision PC. Exit 3010 means restart Windows and rerun.
No automatic restart, autologon, public extraction API, or Linux desktop.
#>
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9._-]+$')][string]$SourceRef = 'main',
    [switch]$AcceptDockerLicense
)

function Write-VortexStage([string]$Message) { Write-Host "`n$Message" -ForegroundColor Cyan }

function Set-VortexPrivateDirectory([string]$Path) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    if ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'The installer workspace must not be a linked directory.'
    }
    $Acl = New-Object Security.AccessControl.DirectorySecurity
    $Acl.SetAccessRuleProtection($true, $false)
    $Acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    foreach ($Value in @('S-1-5-18','S-1-5-32-544')) {
        $Sid = New-Object Security.Principal.SecurityIdentifier($Value)
        $Rule = New-Object Security.AccessControl.FileSystemAccessRule($Sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
        $Acl.AddAccessRule($Rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $Acl
}

function Write-VortexJson([string]$Path, $Value) {
    $Text = $Value | ConvertTo-Json -Depth 64
    [IO.File]::WriteAllText("$Path.part", $Text, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath "$Path.part" -Destination $Path -Force
}

function Get-VortexDownload([string]$Url, [string]$Path) {
    if (([uri]$Url).Scheme -ne 'https') { throw 'Installer downloads require HTTPS.' }
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile "$Path.part" -TimeoutSec 900
        if ((Get-Item -LiteralPath "$Path.part").Length -eq 0) { throw 'The downloaded file was empty.' }
        Move-Item -LiteralPath "$Path.part" -Destination $Path -Force
    } finally {
        Remove-Item -LiteralPath "$Path.part" -Force -ErrorAction SilentlyContinue
    }
}

function Get-VortexReleaseAsset([string]$Repository, [string]$Pattern) {
    if ($Repository -notin @('microsoft/WSL','git-for-windows/git')) { throw 'Unexpected dependency repository.' }
    $Release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repository/releases/latest" -TimeoutSec 60 -Headers @{'User-Agent'='Vision-PC-Setup'}
    if ($Release.draft -or $Release.prerelease) { throw 'A stable dependency release is required.' }
    $Assets = @($Release.assets | Where-Object { $_.name -match $Pattern })
    if ($Assets.Count -ne 1) { throw "Could not identify the Windows x64 release for $Repository." }
    $Asset = $Assets[0]
    if (-not ([string]$Asset.browser_download_url).StartsWith("https://github.com/$Repository/releases/download/", [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Unexpected dependency download origin.'
    }
    return $Asset
}

function Assert-VortexDigest([string]$Path, [string]$Digest) {
    if ($Digest -notmatch '^sha256:([a-fA-F0-9]{64})$') { throw 'The release did not provide a SHA-256 checksum.' }
    $Expected = $Matches[1]
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Expected) { throw 'Dependency checksum verification failed.' }
}

function Assert-VortexSignature([string]$Path, [string]$PublisherPattern) {
    $Signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($Signature.Status -ne 'Valid' -or -not $Signature.SignerCertificate -or
        $Signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) -notmatch $PublisherPattern) {
        throw 'The installer signature or publisher could not be verified.'
    }
}

function Join-VortexArguments([string[]]$Arguments) {
    # Windows CommandLineToArgvW quoting, including spaces and trailing slashes.
    return (($Arguments | ForEach-Object { '"' + ($_ -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"' }) -join ' ')
}

function Invoke-VortexProgram([string]$File, [string[]]$Arguments, [int]$TimeoutSeconds = 180, [switch]$ShowOutput) {
    $Id = [Guid]::NewGuid().ToString('N')
    $OutPath = Join-Path $script:VortexWork "$Id.out"
    $ErrPath = Join-Path $script:VortexWork "$Id.err"
    $Process = $null
    try {
        $Process = Start-Process -FilePath $File -ArgumentList (Join-VortexArguments $Arguments) -PassThru -WindowStyle Hidden -RedirectStandardOutput $OutPath -RedirectStandardError $ErrPath
        $null = $Process.Handle
        $Watch = [Diagnostics.Stopwatch]::StartNew()
        $NextNotice = 30
        while (-not $Process.WaitForExit(1000)) {
            if ($ShowOutput -and $Watch.Elapsed.TotalSeconds -ge $NextNotice) {
                Write-Host 'Installation is still running...'
                $NextNotice += 30
            }
            if ($Watch.Elapsed.TotalSeconds -gt $TimeoutSeconds) {
                $Process.Kill()
                throw (New-Object TimeoutException("Timed out waiting for $([IO.Path]::GetFileName($File)). Rerun setup after checking the installer."))
            }
        }
        $Stdout = ([string](Get-Content -LiteralPath $OutPath -Raw -ErrorAction SilentlyContinue)).Replace("`0", '').Trim()
        $Stderr = ([string](Get-Content -LiteralPath $ErrPath -Raw -ErrorAction SilentlyContinue)).Replace("`0", '').Trim()
        $Output = ($Stdout, $Stderr) -join "`n"
        if ($ShowOutput -and $Output.Trim()) { Write-Host $Output.Trim() }
        return [pscustomobject]@{ ExitCode = $Process.ExitCode; Output = $Output.Trim(); Stdout = $Stdout; Stderr = $Stderr }
    } finally {
        if ($Process) { $Process.Dispose() }
        Remove-Item -LiteralPath $OutPath,$ErrPath -Force -ErrorAction SilentlyContinue
    }
}

function Get-VortexBootId { return (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().Ticks.ToString() }

function Request-VortexRestart([string]$Reason) {
    Write-VortexJson $script:VortexStatePath ([pscustomobject]@{ restartBoot = (Get-VortexBootId); reason = $Reason })
    $Failure = New-Object System.Exception("$Reason Restart Windows, sign in, and run the same setup command again. Setup has not restarted the PC.")
    $Failure.Data['VortexRestart'] = $true
    throw $Failure
}

function Test-VortexPendingRestart {
    foreach ($Key in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending',
                        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')) {
        if (Test-Path -LiteralPath $Key) { return $true }
    }
    return $false
}

function Enable-VortexWindowsFeatures {
    $NeedsRestart = $false
    foreach ($Name in @('Microsoft-Windows-Subsystem-Linux','VirtualMachinePlatform')) {
        $Feature = Get-WindowsOptionalFeature -Online -FeatureName $Name
        if ([string]$Feature.State -match 'Pending') { $NeedsRestart = $true; continue }
        if ([string]$Feature.State -ne 'Enabled') {
            Enable-WindowsOptionalFeature -Online -FeatureName $Name -All -NoRestart | Out-Null
            # Require a new boot even if DISM omits RestartNeeded in its output.
            $NeedsRestart = $true
        }
    }
    if ($NeedsRestart -or (Test-VortexPendingRestart)) { Request-VortexRestart 'The Windows features required by WSL need a restart.' }
}

function Install-VortexWsl {
    $Wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
    $Version = Invoke-VortexProgram $Wsl @('--version')
    $Match = [regex]::Match($Version.Output, '(?m)^.*?:\s*(\d+\.\d+\.\d+)')
    # The old Windows wsl.exe prints help for --version/--update, sometimes with
    # exit 0. Do not confuse that output with an installed modern WSL runtime.
    if ($Version.ExitCode -ne 0 -or -not $Match.Success -or [version]$Match.Groups[1].Value -lt [version]'2.1.5') {
        Write-VortexStage 'Installing the current stable Microsoft WSL runtime'
        $Asset = Get-VortexReleaseAsset 'microsoft/WSL' '^wsl\.[0-9.]+\.x64\.msi$'
        $Package = Join-Path $script:VortexWork 'wsl-x64.msi'
        Get-VortexDownload $Asset.browser_download_url $Package
        Assert-VortexDigest $Package $Asset.digest
        Assert-VortexSignature $Package '^Microsoft Corporation$'
        $Result = Invoke-VortexProgram (Join-Path $env:SystemRoot 'System32\msiexec.exe') @('/i',$Package,'/qn','/norestart') 900
        if ($Result.ExitCode -in @(3010,1641)) { Request-VortexRestart 'The WSL runtime installation requires a restart.' }
        if ($Result.ExitCode -ne 0) { throw "WSL installation failed (exit $($Result.ExitCode))." }
        $Version = Invoke-VortexProgram $Wsl @('--version')
        $Match = [regex]::Match($Version.Output, '(?m)^.*?:\s*(\d+\.\d+\.\d+)')
        if ($Version.ExitCode -ne 0 -or -not $Match.Success -or [version]$Match.Groups[1].Value -lt [version]'2.1.5') { throw 'Modern WSL could not be verified. Restart Windows and rerun setup.' }
    }
    $Result = Invoke-VortexProgram $Wsl @('--set-default-version','2')
    if ($Result.Output -match 'WSL_E_WSL_OPTIONAL_COMPONENT_REQUIRED') { Request-VortexRestart 'WSL reports that its Windows component is not active.' }
    if ($Result.ExitCode -ne 0) { throw "WSL 2 could not start. Check BIOS virtualization and restart Windows. $($Result.Output)" }
}

function Find-VortexDockerDesktop {
    foreach ($Directory in @((Join-Path $env:ProgramFiles 'Docker\Docker'), (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop'))) {
        $File = Join-Path $Directory 'Docker Desktop.exe'
        if (Test-Path -LiteralPath $File) { return $File }
    }
    return $null
}

function Set-VortexDockerSettings($Settings) {
    foreach ($Name in @('AutoStart','OpenUIOnStartupDisabled','WslEngineEnabled')) {
        # Preserve the spelling used by older camelCase settings.json files.
        $Existing = @($Settings.PSObject.Properties | Where-Object { $_.Name -ieq $Name })
        $Key = if ($Existing.Count) { $Existing[0].Name } else { $Name }
        $Settings | Add-Member -NotePropertyName $Key -NotePropertyValue $true -Force
    }
    $Existing = @($Settings.PSObject.Properties | Where-Object { $_.Name -ieq 'UseWindowsContainers' })
    $Key = if ($Existing.Count) { $Existing[0].Name } else { 'UseWindowsContainers' }
    $Settings | Add-Member -NotePropertyName $Key -NotePropertyValue $false -Force
    return $Settings
}

function Start-VortexDocker {
    $Desktop = Find-VortexDockerDesktop
    if (-not $Desktop) {
        if (-not $AcceptDockerLicense) { throw 'Unattended Docker installation requires -AcceptDockerLicense (Docker Subscription Service Agreement).' }
        Write-VortexStage 'Downloading and installing Docker Desktop with the WSL 2 backend'
        $Installer = Join-Path $script:VortexWork 'Docker-Desktop-Installer.exe'
        Get-VortexDownload 'https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe' $Installer
        Assert-VortexSignature $Installer '^Docker Inc\.?$'
        $Result = Invoke-VortexProgram $Installer @('install','--quiet','--accept-license','--backend=wsl-2') 1800 -ShowOutput
        if ($Result.ExitCode -in @(3010,1641) -or (Test-VortexPendingRestart)) { Request-VortexRestart 'Docker installation requires a Windows restart.' }
        if ($Result.ExitCode -ne 0) { throw "Docker installation failed (exit $($Result.ExitCode))." }
        $Desktop = Find-VortexDockerDesktop
        if (-not $Desktop) { throw 'Docker Desktop was not found after installation.' }
    }
    $DockerBin = Join-Path (Split-Path $Desktop) 'resources\bin'
    $script:VortexDocker = Join-Path $DockerBin 'docker.exe'
    if (-not (Test-Path -LiteralPath $script:VortexDocker)) { throw 'Docker Desktop CLI is missing. Repair the Docker Desktop installation.' }
    $env:Path = "$DockerBin;$env:Path"
    # Always target this PC. Never provision onto a pre-existing remote context.
    $env:DOCKER_HOST = $null
    $env:DOCKER_CONTEXT = 'desktop-linux'
    $Processes = @(Get-Process -Name 'Docker Desktop','com.docker.backend' -ErrorAction SilentlyContinue)
    if ($Processes.Count) {
        Write-VortexStage 'Restarting Docker Desktop to apply background and WSL settings'
        $Result = Invoke-VortexProgram $script:VortexDocker @('desktop','stop','--timeout','60') 75
        if ($Result.ExitCode -ne 0) { throw 'Docker Desktop could not stop cleanly. Quit it from its tray menu and rerun this script.' }
    }
    $SettingsDirectory = Join-Path $env:APPDATA 'Docker'
    New-Item -ItemType Directory -Path $SettingsDirectory -Force | Out-Null
    $SettingsPath = Join-Path $SettingsDirectory 'settings-store.json'
    $Legacy = Join-Path $SettingsDirectory 'settings.json'
    if (-not (Test-Path -LiteralPath $SettingsPath) -and (Test-Path -LiteralPath $Legacy)) { $SettingsPath = $Legacy }
    $Settings = [pscustomobject]@{}
    if (Test-Path -LiteralPath $SettingsPath) {
        $Settings = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
        if ($null -eq $Settings -or $Settings -is [array] -or $Settings -isnot [pscustomobject]) { throw 'Docker settings are invalid. Restore or repair Docker settings before continuing.' }
        Copy-Item -LiteralPath $SettingsPath -Destination "$SettingsPath.vision-backup-$([Guid]::NewGuid().ToString('N'))"
    }
    Write-VortexJson $SettingsPath (Set-VortexDockerSettings $Settings)
    $RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    if (-not (Test-Path -LiteralPath $RunKey)) { New-Item -Path $RunKey -Force | Out-Null }
    New-ItemProperty -Path $RunKey -Name 'Docker Desktop' -Value ('"' + $Desktop + '"') -PropertyType String -Force | Out-Null
    Write-VortexStage 'Starting Docker in the background; waiting for its Linux engine'
    Start-Process -FilePath $Desktop -WindowStyle Hidden | Out-Null
    $Deadline = [DateTime]::UtcNow.AddMinutes(5)
    do {
        try {
            $Result = Invoke-VortexProgram $script:VortexDocker @('info','--format','{{.OSType}}') 20
            if ($Result.ExitCode -eq 0 -and $Result.Stdout -eq 'linux') { return }
            if ($Result.Output -match 'WSL_E_WSL_OPTIONAL_COMPONENT_REQUIRED') { Request-VortexRestart 'Docker reports that the WSL Windows component is not active.' }
        } catch [TimeoutException] { Write-Host 'Docker is still starting...' }
        Start-Sleep -Seconds 3
    } while ([DateTime]::UtcNow -lt $Deadline)
    throw 'Docker did not become ready within five minutes. Check Docker Desktop for a WSL, virtualization, or first-run message, then rerun setup.'
}

function Install-VortexGit {
    $Existing = Get-Command git -CommandType Application -ErrorAction SilentlyContinue
    if ($Existing) {
        $Check = Invoke-VortexProgram $Existing.Source @('--version')
        if ($Check.ExitCode -eq 0) { return }
    }
    # Official MinGit supplies noninteractive Git without installing Git Bash or
    # editing the machine PATH. Only this setup process and its children use it.
    $GitDirectory = Join-Path $script:VortexWork 'mingit'
    $Git = Join-Path $GitDirectory 'cmd\git.exe'
    if (-not (Test-Path -LiteralPath $Git)) {
        Write-VortexStage 'Installing the official portable Git dependency'
        $Asset = Get-VortexReleaseAsset 'git-for-windows/git' '^MinGit-[0-9.]+-64-bit\.zip$'
        $Archive = Join-Path $script:VortexWork 'mingit.zip'
        Get-VortexDownload $Asset.browser_download_url $Archive
        Assert-VortexDigest $Archive $Asset.digest
        Expand-Archive -LiteralPath $Archive -DestinationPath $GitDirectory -Force
    }
    $Result = Invoke-VortexProgram $Git @('--version')
    if ($Result.ExitCode -ne 0) { throw 'The portable Git dependency could not start.' }
    $env:Path = "$(Split-Path $Git);$env:Path"
}

function Install-VortexFromUpdater {
    $Updater = Join-Path $script:VortexWork 'Setup-Vision-PC.ps1'
    Get-VortexDownload "https://raw.githubusercontent.com/JRDN-R/vision/$SourceRef/vision-pc/Setup-Vision-PC.ps1" $Updater
    $Python = Join-Path $script:VortexRoot 'runtime\python.exe'
    $NativeCheck = 'import psutil,json,sys; c=json.load(open(sys.argv[1],encoding="utf-8-sig")); sys.path.insert(0,c.get("vortex",{}).get("packagesPath") or ""); import yt_dlp,gallery_dl,spotdl'
    $Native = Invoke-VortexProgram $Python @('-c',$NativeCheck,(Join-Path $script:VortexRoot 'config.json'))
    $Actions = @('InstallVortexServices','CheckVortexServices')
    if ($Native.ExitCode -ne 0) { $Actions = @('Update','InstallVortexTools') + $Actions }
    foreach ($Action in $Actions) {
        Write-VortexStage "Running Vision PC: $Action"
        $Result = Invoke-VortexProgram (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') @('-NoProfile','-ExecutionPolicy','Bypass','-File',$Updater,'-Action',$Action,'-SourceRef',$SourceRef) 3600 -ShowOutput
        if ($Result.ExitCode -ne 0) { throw "$Action stopped. Correct the reported issue and rerun this script." }
    }
}

function Invoke-VortexWindowsSetup {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
    if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Open Windows PowerShell using Run as administrator, then paste the setup command again.' }
    if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64' -or -not [Environment]::Is64BitProcess -or [Environment]::OSVersion.Version.Build -lt 19045) { throw 'This installer requires 64-bit Windows 10 22H2 or Windows 11 on an Intel/AMD PC.' }
    $Computer = Get-CimInstance Win32_ComputerSystem
    if (-not $Computer.UserName -or $Computer.UserName -ine $Identity.Name) { throw 'Run this script as administrator from the Windows account currently signed into the desktop.' }
    if (-not $Computer.HypervisorPresent -and -not (@(Get-CimInstance Win32_Processor | Where-Object { $_.VirtualizationFirmwareEnabled }).Count)) { throw 'Enable hardware virtualization (AMD SVM or Intel VT-x) in the BIOS, then rerun. Software cannot enable this firmware setting.' }
    $script:VortexRoot = Join-Path $env:ProgramData 'VisionPC'
    if (-not (Test-Path -LiteralPath (Join-Path $script:VortexRoot 'config.json'))) { throw 'Install Vision PC before adding Vortex services.' }
    $script:VortexWork = Join-Path $script:VortexRoot 'downloads\vortex-windows-setup'
    Set-VortexPrivateDirectory $script:VortexWork
    $script:VortexStatePath = Join-Path $script:VortexWork 'state.json'
    if (Test-Path -LiteralPath $script:VortexStatePath) {
        $State = Get-Content -LiteralPath $script:VortexStatePath -Raw | ConvertFrom-Json
        if ($State.restartBoot -eq (Get-VortexBootId)) { Request-VortexRestart 'The previously requested Windows restart has not happened yet.' }
    }
    Write-VortexStage 'Checking the Windows features required by WSL 2'
    Enable-VortexWindowsFeatures
    Install-VortexWsl
    Start-VortexDocker
    Install-VortexGit
    Install-VortexFromUpdater
    Remove-Item -LiteralPath $script:VortexStatePath -Force -ErrorAction SilentlyContinue
    Write-Host "`nVortex services are ready. You can close PowerShell. Docker starts in the background after this Windows account signs in; keep the PC awake." -ForegroundColor Green
}

# Dot-sourcing loads only functions for tests. Normal -File execution installs.
if ($MyInvocation.InvocationName -ne '.') {
    $Mutex = New-Object Threading.Mutex($false, 'Global\VisionVortexWindowsSetup')
    $OwnsMutex = $false
    try {
        try { $OwnsMutex = $Mutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $OwnsMutex = $true }
        if (-not $OwnsMutex) { throw 'Another Vortex Windows setup is already running. Wait for it to finish.' }
        Invoke-VortexWindowsSetup
        exit 0
    }
    catch {
        if ($_.Exception.Data['VortexRestart']) { Write-Host $_.Exception.Message -ForegroundColor Yellow; exit 3010 }
        Write-Host ("Setup stopped: " + $_.Exception.Message) -ForegroundColor Red
        exit 1
    }
    finally {
        if ($OwnsMutex) { $Mutex.ReleaseMutex() }
        $Mutex.Dispose()
    }
}
