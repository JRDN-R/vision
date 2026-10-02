#requires -Version 5.1
<#
.SYNOPSIS
Reports Vision PC availability, or explicitly enables/restores unattended availability.
.DESCRIPTION
Status is the default and changes no Windows settings. Every action writes a report
on the current user's Desktop. Enable/Restore require administrator PowerShell.
Enable changes AC sleep/hibernate timeouts and the existing Vision startup task only.
Wake-on-LAN configuration is separately opt-in and never restarts the network adapter.
No passwords, automatic desktop sign-in, router/BIOS/firewall changes, Tailscale route
changes, forced reboots, or unrestricted remote command endpoint are involved.
.EXAMPLE
.\Keep-Vision-PC-Available.ps1
.EXAMPLE
.\Keep-Vision-PC-Available.ps1 -Action Enable
.EXAMPLE
.\Keep-Vision-PC-Available.ps1 -Action Enable -EnableWakeOnLan -AdapterName 'Ethernet'
.EXAMPLE
.\Keep-Vision-PC-Available.ps1 -Action Restore
#>
[CmdletBinding()]
param(
    [ValidateSet('Status','Enable','Restore')]
    [string]$Action = 'Status',
    [switch]$EnableWakeOnLan,
    [string]$AdapterName,
    [string]$ReportDirectory = [Environment]::GetFolderPath('Desktop')
)

$ErrorActionPreference = 'Stop'
$InstallRoot = Join-Path $env:ProgramData 'VisionPC'
$BackupDirectory = Join-Path $InstallRoot 'availability-backup'
$BackupPath = Join-Path $BackupDirectory 'original-settings.json'
$TaskName = 'Vision Private PC'
$SleepGroup = '238c9fa8-0aad-41ed-83f4-97be242c8f20'
$PowerSettings = @(
    @{ Name = 'AC sleep timeout'; Guid = '29f6c1db-86da-48c5-9fdb-f2b67b1f44da' },
    @{ Name = 'AC hibernate timeout'; Guid = '9d7815a6-7ee4-497e-8888-515a05f02364' }
)
$PowerCfg = Join-Path $env:WINDIR 'System32\powercfg.exe'
$Notes = New-Object 'System.Collections.Generic.List[string]'
$Failure = $null
$Utf8 = New-Object Text.UTF8Encoding($false)

function Add-Note([string]$Text) { $script:Notes.Add($Text); Write-Host $Text }
function Test-Administrator {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
    return $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function Invoke-PowerCfg([string[]]$Arguments) {
    $Text = & $script:PowerCfg @Arguments 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw ('powercfg failed while applying/checking: ' + ($Arguments -join ' ')) }
    return $Text.Trim()
}
function Get-ActiveScheme {
    $Text = Invoke-PowerCfg @('/getactivescheme')
    $Match = [regex]::Match($Text, '(?i)\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b')
    if (-not $Match.Success) { throw 'Could not identify the active Windows power scheme.' }
    return $Match.Value.ToLowerInvariant()
}
function Get-PowerIndex([string]$Scheme, [string]$Setting) {
    # Values are locale-independent hexadecimal. The final two indices in this
    # single-setting query are AC and DC; preceding range/default values are ignored.
    $Text = Invoke-PowerCfg @('/query', $Scheme, $script:SleepGroup, $Setting)
    $Matches = [regex]::Matches($Text, '(?im)^.*?:\s*0x([0-9a-f]{1,8})\s*$')
    if ($Matches.Count -lt 2) { throw 'Could not read both power-setting indices. No guessed values will be used.' }
    return [uint32]::Parse($Matches[$Matches.Count - 2].Groups[1].Value, [Globalization.NumberStyles]::HexNumber)
}
function Assert-PlainDirectory([string]$Path) {
    if ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Refusing linked folder: $Path"
    }
}
function Protect-BackupDirectory {
    if (-not (Test-Path -LiteralPath $script:InstallRoot -PathType Container)) {
        throw 'Vision PC is not installed in ProgramData. Run its installer first.'
    }
    Assert-PlainDirectory $script:InstallRoot
    $Owner = (Get-Acl -LiteralPath $script:InstallRoot).GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($Owner -notin @('S-1-5-18','S-1-5-32-544')) { throw 'The Vision installation has an unexpected owner. No settings were changed.' }
    if (-not (Test-Path -LiteralPath $script:BackupDirectory)) {
        New-Item -ItemType Directory -Path $script:BackupDirectory | Out-Null
    }
    Assert-PlainDirectory $script:BackupDirectory
    $Acl = New-Object Security.AccessControl.DirectorySecurity
    $Acl.SetAccessRuleProtection($true, $false)
    $Acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    foreach ($SidValue in @('S-1-5-18','S-1-5-32-544')) {
        $Sid = New-Object Security.Principal.SecurityIdentifier($SidValue)
        $Rule = New-Object Security.AccessControl.FileSystemAccessRule($Sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
        $Acl.AddAccessRule($Rule)
    }
    Set-Acl -LiteralPath $script:BackupDirectory -AclObject $Acl
    if (Test-Path -LiteralPath $script:BackupPath) {
        if ((Get-Item -LiteralPath $script:BackupPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Refusing a linked availability backup.'
        }
    }
}
function Save-Backup($Value) {
    $Part = Join-Path $script:BackupDirectory ('settings-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::WriteAllText($Part, ($Value | ConvertTo-Json -Depth 12), $script:Utf8)
        Move-Item -LiteralPath $Part -Destination $script:BackupPath -Force
    } finally {
        if (Test-Path -LiteralPath $Part) { Remove-Item -LiteralPath $Part -Force }
    }
}
function Read-Backup {
    $Value = Get-Content -LiteralPath $script:BackupPath -Raw | ConvertFrom-Json
    if ($Value.Version -ne 1 -or $Value.ComputerName -ne $env:COMPUTERNAME -or
        [string]$Value.SchemeGuid -notmatch '^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$') {
        throw 'The availability backup is invalid or belongs to another PC.'
    }
    if (@($Value.Power).Count -ne 2) { throw 'The power-setting backup is incomplete.' }
    foreach ($Expected in $script:PowerSettings) {
        $Item = @($Value.Power | Where-Object { $_.Guid -eq $Expected.Guid })
        $Parsed = [uint32]0
        if ($Item.Count -ne 1 -or -not [uint32]::TryParse([string]$Item[0].OriginalIndex, [ref]$Parsed)) {
            throw 'The power-setting backup contains an invalid value.'
        }
    }
    return $Value
}
function Get-VisionTask {
    return Get-ScheduledTask -TaskName $script:TaskName -TaskPath '\' -ErrorAction SilentlyContinue
}
function Test-InstalledTask($Task) {
    if (-not $Task) { return $false }
    $User = [string]$Task.Principal.UserId
    if ($User -notin @('SYSTEM','NT AUTHORITY\SYSTEM','S-1-5-18') -or [string]$Task.Principal.LogonType -ne 'ServiceAccount') { return $false }
    $Actions = @($Task.Actions)
    if ($Actions.Count -ne 1) { return $false }
    $ExpectedPython = Join-Path $script:InstallRoot 'runtime\python.exe'
    $ExpectedServer = '"' + (Join-Path $script:InstallRoot 'server.py') + '"'
    return ([string]$Actions[0].Execute -eq $ExpectedPython -and [string]$Actions[0].Arguments -eq $ExpectedServer)
}
function Test-LocalProcessor {
    $ConfigPath = Join-Path $script:InstallRoot 'config.json'
    if (-not (Test-Path -LiteralPath $ConfigPath)) { return 'not installed' }
    try {
        # Credentials are used only for this loopback health check, never reported.
        $Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
        if ([int]$Config.port -ne 8765 -or [string]$Config.token -notmatch '^[A-Za-z0-9_-]{32,256}$') { return 'configuration unavailable' }
        $Result = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -Headers @{Authorization = 'Bearer ' + $Config.token} -TimeoutSec 3
        if ($Result.ok -eq $true -and ($Result.service -eq 'vision-pc' -or $Result.mode -eq 'private-pc')) { return 'online' }
        return 'not responding as Vision'
    } catch { return 'offline or unavailable to this Windows user' }
}
function Get-WiredAdapters {
    if (-not (Get-Command Get-NetAdapter -ErrorAction SilentlyContinue)) { return @() }
    # NDIS physical medium 14 is wired Ethernet. Some Ethernet drivers report 0;
    # include those as candidates unless they identify themselves as wireless.
    return @(Get-NetAdapter -Physical | Where-Object {
        ([string]$_.PhysicalMediaType -eq '802.3' -or [int]$_.NdisPhysicalMedium -in @(0,14)) -and
        [string]$_.InterfaceDescription -notmatch '(?i)wireless|wi-fi|wlan|bluetooth'
    })
}
function Get-WolManagement($Adapter) {
    if (-not (Get-Command Get-NetAdapterPowerManagement -ErrorAction SilentlyContinue)) { return $null }
    try { return Get-NetAdapterPowerManagement -Name ([WildcardPattern]::Escape([string]$Adapter.Name)) -ErrorAction Stop } catch { return $null }
}
function Set-WolNoRestart([string]$Name, [string]$Value) {
    $Command = Get-Command Set-NetAdapterPowerManagement -ErrorAction SilentlyContinue
    if (-not $Command -or -not $Command.Parameters.ContainsKey('NoRestart') -or -not $Command.Parameters.ContainsKey('WakeOnMagicPacket')) {
        throw 'This Windows networking cmdlet cannot change Wake-on-LAN without restarting the adapter. Use Device Manager manually.'
    }
    Set-NetAdapterPowerManagement -Name ([WildcardPattern]::Escape($Name)) -WakeOnMagicPacket $Value -NoRestart -Confirm:$false | Out-Null
}
function Enable-Availability {
    Protect-BackupDirectory
    $Scheme = Get-ActiveScheme
    $Task = Get-VisionTask
    if (Test-Path -LiteralPath $script:BackupPath) {
        $Backup = Read-Backup
        if ($Backup.SchemeGuid -ne $Scheme) { throw 'The active power scheme changed since the original backup. Restore that backup before enabling a different scheme.' }
        Add-Note 'Keeping the original availability backup from the first Enable run.'
    } else {
        $Power = @($script:PowerSettings | ForEach-Object {
            [pscustomobject]@{ Name = $_.Name; Guid = $_.Guid; OriginalIndex = Get-PowerIndex $Scheme $_.Guid }
        })
        $TaskXml = $null
        if (Test-InstalledTask $Task) { $TaskXml = Export-ScheduledTask -TaskName $script:TaskName -TaskPath '\' }
        $Backup = [pscustomobject]@{
            Version = 1; ComputerName = $env:COMPUTERNAME; CreatedUtc = [DateTime]::UtcNow.ToString('o')
            SchemeGuid = $Scheme; Power = $Power; TaskXml = $TaskXml; WakeOnLan = @()
        }
        Save-Backup $Backup
        Add-Note "Original settings backed up to $script:BackupPath"
    }
    # Obtain any optional adapter backup before changing that adapter.
    $WolTarget = $null
    if ($script:EnableWakeOnLan) {
        if ([string]::IsNullOrWhiteSpace($script:AdapterName)) { throw 'Use -AdapterName with -EnableWakeOnLan to select the physical wired adapter explicitly.' }
        $Found = @(Get-WiredAdapters | Where-Object { $_.Name -eq $script:AdapterName })
        if ($Found.Count -ne 1) { throw 'The selected name is not one physical wired Ethernet adapter.' }
        $WolTarget = $Found[0]
        $Management = Get-WolManagement $WolTarget
        if (-not $Management -or [string]$Management.WakeOnMagicPacket -notin @('Enabled','Disabled')) {
            throw 'The selected driver does not expose supported magic-packet wake settings. Review Device Manager and BIOS manually.'
        }
        $Command = Get-Command Set-NetAdapterPowerManagement -ErrorAction SilentlyContinue
        if (-not $Command -or -not $Command.Parameters.ContainsKey('NoRestart') -or -not $Command.Parameters.ContainsKey('WakeOnMagicPacket')) {
            throw 'Wake-on-LAN cannot be staged without a network restart on this PC. No adapter changes were made.'
        }
        $Guid = [string]$WolTarget.InterfaceGuid
        if (-not @($Backup.WakeOnLan | Where-Object { $_.InterfaceGuid -eq $Guid }).Count) {
            $Backup.WakeOnLan = @($Backup.WakeOnLan) + @([pscustomobject]@{
                InterfaceGuid = $Guid; AdapterName = $WolTarget.Name; OriginalMagicPacket = [string]$Management.WakeOnMagicPacket
            })
            Save-Backup $Backup
        }
    }
    foreach ($Setting in $script:PowerSettings) {
        Invoke-PowerCfg @('/setacvalueindex', $Scheme, $script:SleepGroup, $Setting.Guid, '0') | Out-Null
    }
    Invoke-PowerCfg @('/setactive', $Scheme) | Out-Null
    Add-Note 'AC sleep and AC hibernate timeouts are now Never in the current power scheme. Display and battery timeouts were left unchanged.'
    if (Test-InstalledTask $Task) {
        if (-not $Backup.TaskXml) {
            $Backup.TaskXml = Export-ScheduledTask -TaskName $script:TaskName -TaskPath '\'
            Save-Backup $Backup
        }
        $Triggers = @($Task.Triggers)
        $BootTriggers = @($Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskBootTrigger' })
        if ($BootTriggers.Count -eq 0) {
            $Triggers += New-ScheduledTaskTrigger -AtStartup
        } else {
            foreach ($BootTrigger in $BootTriggers) { $BootTrigger.Enabled = $true }
        }
        $Desired = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
        $Settings = $Task.Settings
        foreach ($Property in @('StartWhenAvailable','RestartCount','RestartInterval','ExecutionTimeLimit','MultipleInstances')) {
            $Settings.$Property = $Desired.$Property
        }
        Set-ScheduledTask -TaskName $script:TaskName -TaskPath '\' -Trigger $Triggers -Settings $Settings | Out-Null
        Enable-ScheduledTask -TaskName $script:TaskName -TaskPath '\' | Out-Null
        Add-Note 'The existing SYSTEM task is enabled, starts at Windows startup, and retries a failed process every minute (up to 999 retries). No desktop login is required.'
        $Task = Get-VisionTask
        if ((Test-LocalProcessor) -ne 'online' -and [string]$Task.State -ne 'Running') {
            Start-ScheduledTask -TaskName $script:TaskName -TaskPath '\'
            Add-Note 'Started the existing Vision task; the report below checks its current health.'
        }
    } else {
        Add-Note 'The expected installed SYSTEM task was not found or has a different action/principal. Its definition was not changed; rerun the Vision PC installer to restore its unattended task.'
    }
    if ($WolTarget) {
        Set-WolNoRestart $WolTarget.Name 'Enabled'
        Add-Note "Magic-packet wake was requested for '$($WolTarget.Name)' without restarting the adapter. Driver/BIOS support and a real wake test still need verification; some drivers apply this after a later restart."
    }
}
function Restore-Availability {
    Protect-BackupDirectory
    if (-not (Test-Path -LiteralPath $script:BackupPath)) { throw 'No original availability-settings backup exists.' }
    $Backup = Read-Backup
    foreach ($Setting in @($Backup.Power)) {
        Invoke-PowerCfg @('/setacvalueindex', [string]$Backup.SchemeGuid, $script:SleepGroup, [string]$Setting.Guid, [string]$Setting.OriginalIndex) | Out-Null
    }
    if ((Get-ActiveScheme) -eq $Backup.SchemeGuid) {
        Invoke-PowerCfg @('/setactive', [string]$Backup.SchemeGuid) | Out-Null
    }
    Add-Note 'Original AC sleep/hibernate indices restored to the backed-up scheme. The currently selected scheme and display settings were not changed.'
    if ($Backup.TaskXml) {
        # Backup is administrator-only and was captured only for the installed SYSTEM task.
        Register-ScheduledTask -TaskName $script:TaskName -TaskPath '\' -Xml ([string]$Backup.TaskXml) -Force | Out-Null
        Add-Note 'The original Vision task definition and enabled/disabled setting were restored. No running processor was forcibly stopped.'
    }
    foreach ($Wol in @($Backup.WakeOnLan)) {
        if ([string]$Wol.OriginalMagicPacket -notin @('Enabled','Disabled')) { throw 'The Wake-on-LAN backup has an invalid value.' }
        $Matches = @(Get-WiredAdapters | Where-Object { [string]$_.InterfaceGuid -eq [string]$Wol.InterfaceGuid })
        if ($Matches.Count -ne 1) { throw 'The original wired adapter is not present. The backup is retained so its setting can be restored later.' }
        Set-WolNoRestart $Matches[0].Name ([string]$Wol.OriginalMagicPacket)
        Add-Note "Original magic-packet setting restored for '$($Matches[0].Name)' without restarting it."
    }
    $Archive = Join-Path $script:BackupDirectory ('restored-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '.json')
    Move-Item -LiteralPath $script:BackupPath -Destination $Archive
    Add-Note "Restored settings backup retained at $Archive"
}
function Write-AvailabilityReport {
    $Lines = New-Object 'System.Collections.Generic.List[string]'
    $Lines.Add('VISION PC AVAILABILITY REPORT')
    $Lines.Add('Created UTC: ' + [DateTime]::UtcNow.ToString('o'))
    $Lines.Add('Computer: ' + $env:COMPUTERNAME)
    $Lines.Add('Action: ' + $script:Action)
    $Lines.Add('Administrator: ' + (Test-Administrator))
    $Lines.Add('')
    foreach ($Note in $script:Notes) { $Lines.Add($Note) }
    if ($script:Failure) { $Lines.Add('Action stopped: ' + $script:Failure) }
    $Lines.Add('')
    $Lines.Add('POWER')
    try {
        $Scheme = Get-ActiveScheme
        $Lines.Add('Active scheme: ' + $Scheme)
        foreach ($Setting in $script:PowerSettings) {
            $Value = Get-PowerIndex $Scheme $Setting.Guid
            $Description = if ($Value -eq 0) { 'Never (0 seconds)' } else { "$Value seconds" }
            $Lines.Add($Setting.Name + ': ' + $Description)
        }
        $Lines.Add('Available sleep states (powercfg /a):')
        $Lines.Add((Invoke-PowerCfg @('/a')))
        $Lines.Add('Wake-armed devices:')
        $Lines.Add((Invoke-PowerCfg @('/devicequery','wake_armed')))
        $Lines.Add('Last wake:')
        $Lines.Add((Invoke-PowerCfg @('/lastwake')))
    } catch { $Lines.Add('Power details unavailable: ' + $_.Exception.Message) }
    $Lines.Add('')
    $Lines.Add('VISION BACKGROUND PROCESSOR')
    try {
        $Task = Get-VisionTask
        if ($Task) {
            $Info = Get-ScheduledTaskInfo -TaskName $script:TaskName -TaskPath '\'
            $Lines.Add('Task state: ' + [string]$Task.State)
            $Lines.Add('Task account: ' + [string]$Task.Principal.UserId + '; logon type: ' + [string]$Task.Principal.LogonType)
            $EnabledBootTriggers = @($Task.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskBootTrigger' -and $_.Enabled })
            $Lines.Add('Enabled startup trigger: ' + [string]($EnabledBootTriggers.Count -gt 0))
            $Lines.Add('Restart count/interval: ' + [string]$Task.Settings.RestartCount + ' / ' + [string]$Task.Settings.RestartInterval)
            $Lines.Add('Execution time limit: ' + [string]$Task.Settings.ExecutionTimeLimit)
            $Lines.Add('Last task start: ' + [string]$Info.LastRunTime + '; scheduler result: ' + [string]$Info.LastTaskResult)
            $Lines.Add('A running scheduler result is not itself a processor health check.')
        } else { $Lines.Add('Vision Private PC task: not found') }
        $Lines.Add('Local authenticated processor health: ' + (Test-LocalProcessor))
    } catch { $Lines.Add('Task details unavailable to this Windows user.') }
    $Lines.Add('')
    $Lines.Add('TAILSCALE CONNECTION SERVICE')
    try {
        $TailscaleService = Get-CimInstance Win32_Service -Filter "Name='Tailscale'"
        if ($TailscaleService) {
            $Lines.Add('Tailscale state: ' + [string]$TailscaleService.State + '; start mode: ' + [string]$TailscaleService.StartMode)
            $Lines.Add('Service status alone does not verify the public HTTPS route. Existing routing was left unchanged.')
        } else { $Lines.Add('Tailscale service: not found') }
    } catch { $Lines.Add('Tailscale service details unavailable.') }
    $Lines.Add('')
    $Lines.Add('CHROME REMOTE DESKTOP')
    try {
        $Service = Get-CimInstance Win32_Service -Filter "Name='chromoting'"
        if ($Service) {
            $Lines.Add('chromoting state: ' + [string]$Service.State + '; start mode: ' + [string]$Service.StartMode + '; account: ' + [string]$Service.StartName)
            $Lines.Add('Service presence does not prove that a remote connection is reachable or that unattended access is configured.')
        } else { $Lines.Add('chromoting service: not found') }
    } catch { $Lines.Add('Chrome Remote Desktop service details unavailable.') }
    $Lines.Add('')
    $Lines.Add('PHYSICAL WIRED NETWORK / WAKE-ON-LAN')
    try {
        $Adapters = @(Get-WiredAdapters)
        if ($Adapters.Count -eq 0) { $Lines.Add('No physical wired Ethernet candidate was reported.') }
        foreach ($Adapter in $Adapters) {
            $Lines.Add('Adapter: ' + $Adapter.Name + '; hardware: ' + $Adapter.InterfaceDescription)
            $Lines.Add('  Status: ' + [string]$Adapter.Status + '; speed: ' + [string]$Adapter.LinkSpeed + '; MAC: ' + [string]$Adapter.MacAddress)
            $Management = Get-WolManagement $Adapter
            if ($Management) {
                $Lines.Add('  WakeOnMagicPacket: ' + [string]$Management.WakeOnMagicPacket + '; WakeOnPattern: ' + [string]$Management.WakeOnPattern)
            } else { $Lines.Add('  Driver wake settings: unavailable/unsupported') }
        }
    } catch { $Lines.Add('Network adapter details unavailable to this Windows user.') }
    $Lines.Add('Wake readiness cannot be certified from these settings alone. Test the exact intended sleep/shutdown state while someone can reach the PC.')
    $Lines.Add('Remote power-on needs Ethernet standby power, compatible BIOS/driver settings, and a wake sender that stays on in the same network (or a router with its own wake feature).')
    $Lines.Add('A powered-off PC cannot receive commands through its own Tailscale or Chrome Remote Desktop connection. Pulling mains power removes Wake-on-LAN availability.')
    $Lines.Add('After a mains outage, an optional BIOS Restore after AC Power Loss setting can power the PC back on; inspect it manually. This script does not change it.')
    $Lines.Add('')
    $Lines.Add('RECENT SYSTEM POWER / STARTUP EVENTS')
    try {
        $Events = @(Get-WinEvent -FilterHashtable @{ LogName='System'; Id=@(41,42,1074,6005,6006,6008); StartTime=(Get-Date).AddDays(-30) } -MaxEvents 20 -ErrorAction SilentlyContinue)
        $Events += @(Get-WinEvent -FilterHashtable @{ LogName='System'; ProviderName='Microsoft-Windows-Power-Troubleshooter'; Id=1; StartTime=(Get-Date).AddDays(-30) } -MaxEvents 5 -ErrorAction SilentlyContinue)
        if ($Events.Count -eq 0) { $Lines.Add('No matching readable events found in the past 30 days.') }
        foreach ($Event in @($Events | Sort-Object TimeCreated -Descending | Select-Object -First 25)) {
            $Message = (([string]$Event.Message -split '[\r\n]+')[0])
            if ($Message.Length -gt 400) { $Message = $Message.Substring(0,400) }
            $Lines.Add(('{0:yyyy-MM-dd HH:mm:ss} | {1} | ID {2} | {3}' -f $Event.TimeCreated,$Event.ProviderName,$Event.Id,$Message))
        }
        $Lines.Add('Event 41/6008 indicates an unexpected shutdown, not a proven diagnosis of a mains outage. Event 1074 can identify a requested restart/shutdown.')
    } catch { $Lines.Add('System event history could not be read by this Windows user.') }
    $Lines.Add('')
    $Lines.Add('Status changes no Windows configuration. Enable changes only the documented availability settings; Restore restores the saved original values.')
    $Lines.Add('Tailscale routing, Windows Firewall, BIOS/router settings, Windows Update, display timeout, and desktop sign-in were not changed.')
    $Lines.Add('The report contains computer/network identifiers needed for troubleshooting, but no stored connection tokens or API keys.')
    $ReportText = $Lines -join "`r`n"
    if ([string]::IsNullOrWhiteSpace($script:ReportDirectory) -or -not (Test-Path -LiteralPath $script:ReportDirectory -PathType Container)) {
        Write-Output $ReportText
        throw 'Desktop/report directory is unavailable. The report was printed above; specify an existing -ReportDirectory to save it.'
    }
    $ReportPath = Join-Path $script:ReportDirectory ('Vision-PC-Availability-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss') + '.txt')
    [IO.File]::WriteAllText($ReportPath, $ReportText, $script:Utf8)
    Write-Host "`nReport saved to: $ReportPath" -ForegroundColor Green
}

try {
    if ($EnableWakeOnLan -and $Action -ne 'Enable') { throw '-EnableWakeOnLan can only be used with -Action Enable.' }
    if ($Action -ne 'Status' -and -not (Test-Administrator)) { throw 'Open Windows PowerShell with Run as administrator for Enable or Restore. Status does not require elevation.' }
    switch ($Action) {
        'Enable' { Enable-Availability }
        'Restore' { Restore-Availability }
        'Status' { Add-Note 'Read-only status check. No Windows settings are being changed.' }
    }
} catch {
    $Failure = $_.Exception.Message
    Write-Host "`nAvailability action stopped: $Failure" -ForegroundColor Red
    Write-Host 'If changes were partially applied, the original backup remains available for -Action Restore.'
}
try { Write-AvailabilityReport } catch {
    Write-Host "Could not save the availability report: $($_.Exception.Message)" -ForegroundColor Red
    if (-not $Failure) { $Failure = 'The report could not be saved.' }
}
if ($Failure) { exit 1 }
