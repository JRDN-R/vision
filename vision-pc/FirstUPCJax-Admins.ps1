#requires -Version 5.1
#requires -RunAsAdministrator
[CmdletBinding()]
param(
    [ValidateSet('Setup','Login','Check')][string]$Action = 'Setup',
    [string]$PrivateConfiguration = '',
    [string]$NodeArchive = '',
    [string]$OwnerEmail = ''
)
$ErrorActionPreference = 'Stop'
$Root = Join-Path $env:ProgramData 'VisionPC'
$ConfigPath = Join-Path $Root 'config.json'
$Python = Join-Path $Root 'runtime\python.exe'
$Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$Utf8 = New-Object Text.UTF8Encoding($false)
function Stop-VisionForChurchChange {
    Stop-ScheduledTask -TaskName 'Vision Private PC'
    $Deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ((Get-ScheduledTask -TaskName 'Vision Private PC').State -eq 'Running') {
        if ([DateTime]::UtcNow -gt $Deadline) { throw 'Vision did not stop safely; no second worker was started.' }
        Start-Sleep -Milliseconds 300
    }
}
function Assert-PrivateFolder([string]$Directory) {
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    if ((Get-Item -LiteralPath $Directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'A linked installation folder is not allowed.' }
    & icacls.exe $Directory /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Private installation permissions could not be applied.' }
}
if (-not $Config.firebaseAuth.enabled) { throw 'Enable Vision Google sign-in before setting up FirstUPCJax Admins.' }
if (-not (Test-Path -LiteralPath (Join-Path $Root 'church_mcp.py'))) { throw 'Update FUPCJ Server before setting up FirstUPCJax Admins.' }
if ($Action -eq 'Setup') {
    if (-not (Test-Path -LiteralPath $PrivateConfiguration -PathType Leaf)) { throw 'Choose the private PC configuration supplied with the installer.' }
    $Private = Get-Content -LiteralPath $PrivateConfiguration -Raw | ConvertFrom-Json
    if (-not $OwnerEmail) { $OwnerEmail = [string]$Private.ownerEmail }
    if ($OwnerEmail -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$') { throw 'Enter the owner email.' }
    if (-not $Private.pco.client_id -or -not $Private.pco.secret) { throw 'Planning Center credentials are missing from the private setup configuration.' }
    if (-not (Test-Path -LiteralPath $NodeArchive -PathType Leaf)) { throw 'Extract the complete setup ZIP, including the Windows Node runtime.' }
    if ((Get-FileHash -LiteralPath $NodeArchive -Algorithm SHA256).Hash -ne '158f7685b44de51f6c0df1d153526cbcd3e1bc739a8dfc607721cef75de9e541') { throw 'The Windows scheduling runtime checksum did not match.' }
    if ($Config.backendUrl -ne $Private.backendUrl) { throw 'The private setup HTTPS address does not match this Vision server. No connection settings were changed.' }
    $Plugin = Join-Path $Root ('plugins\church-admin-' + [Guid]::NewGuid().ToString('N'))
    $Packages = Join-Path $Plugin 'packages'
    $Browsers = Join-Path $Plugin 'browsers'
    Assert-PrivateFolder $Root
    Assert-PrivateFolder $Plugin
    Expand-Archive -LiteralPath $NodeArchive -DestinationPath $Plugin
    $Node = Join-Path $Plugin 'node-v24.21.0-win-x64\node.exe'
    & $Node --version
    if ($LASTEXITCODE -ne 0) { throw 'The Windows scheduling runtime did not start.' }
    & $Python -m pip --isolated install --index-url https://pypi.org/simple --disable-pip-version-check --no-input --only-binary=:all: --target $Packages -r (Join-Path $Root 'requirements-church.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Notification browser dependency installation failed.' }
    & $Python (Join-Path $Root 'church_browser_install.py') --packages-path $Packages --browsers-path $Browsers
    if ($LASTEXITCODE -ne 0) { throw 'Chromium installation failed.' }
    # Reload after downloads. Preserve other features and existing owner identity.
    $Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    if ($Config.churchAdmins.ownerEmail -and $Config.churchAdmins.ownerEmail -ne $OwnerEmail) { throw 'An existing church owner is configured. This upgrade cannot transfer ownership.' }
    $Previous = [IO.File]::ReadAllBytes($ConfigPath)
    $Backup = "$ConfigPath.church-" + [DateTime]::UtcNow.ToString('yyyyMMddHHmmss') + '.bak'
    Copy-Item -LiteralPath $ConfigPath -Destination $Backup
    $Settings = if ($Config.churchAdmins) { $Config.churchAdmins } else { [pscustomobject]@{} }
    foreach ($Pair in @(@('enabled',$true),@('ownerEmail',$OwnerEmail.ToLowerInvariant()),@('packagesPath',$Packages),@('browsersPath',$Browsers),@('nodePath',$Node))) {
        $Settings | Add-Member -NotePropertyName $Pair[0] -NotePropertyValue $Pair[1] -Force
    }
    $Config | Add-Member -NotePropertyName churchAdmins -NotePropertyValue $Settings -Force
    $Data = [string]$Config.dataDir
    if (-not $Data) { $Data = Join-Path $Root 'data' }
    if (-not [IO.Path]::IsPathRooted($Data)) { $Data = Join-Path $Root $Data }
    $ChurchData = Join-Path $Data 'church-admin'
    Assert-PrivateFolder $ChurchData
    $Pending = Join-Path $ChurchData 'pco-credentials.pending.json'
    $CreatedPending = $false
    try {
        Stop-VisionForChurchChange
        if (-not (Test-Path -LiteralPath (Join-Path $ChurchData 'pco-credentials.dpapi'))) {
            [IO.File]::WriteAllText("$Pending.part",($Private.pco | ConvertTo-Json -Compress),$Utf8)
            Move-Item -LiteralPath "$Pending.part" -Destination $Pending -Force
            $CreatedPending = $true
        }
        [IO.File]::WriteAllText("$ConfigPath.church-part",($Config | ConvertTo-Json -Depth 30),$Utf8)
        Move-Item -LiteralPath "$ConfigPath.church-part" -Destination $ConfigPath -Force
        & $Python (Join-Path $Root 'church_login.py') --check
        if ($LASTEXITCODE -ne 0) { throw 'Notification browser check failed.' }
        & $Node (Join-Path $Root 'church_runner.mjs') --tools | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'The scheduling tool catalog did not load.' }
        Start-ScheduledTask -TaskName 'Vision Private PC'
        $Healthy = $false
        $Deadline = [DateTime]::UtcNow.AddSeconds(45)
        while ([DateTime]::UtcNow -lt $Deadline) {
            try {
                $Health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -Headers @{Authorization=('Bearer ' + $Config.token); 'X-Vision-Diagnostic-Token'=$Config.diagnosticToken} -TimeoutSec 5
                if ($Health.churchAdmins.enabled -and $Health.churchAdmins.nativeMcpReady -and $Health.churchAdmins.credentialsSaved) { $Healthy = $true; break }
            } catch { }
            Start-Sleep -Milliseconds 500
        }
        if (-not $Healthy) { throw 'The running server did not confirm the native MCP endpoint and protected credentials.' }
        $Metadata = Invoke-RestMethod -Uri ($Config.backendUrl + '/.well-known/oauth-protected-resource/firstupcjax/mcp') -TimeoutSec 15
        if ($Metadata.resource -ne ($Config.backendUrl + '/firstupcjax/mcp')) { throw 'The public MCP authorization metadata did not verify.' }
    } catch {
        $Failure = $_.Exception.Message
        Stop-VisionForChurchChange
        [IO.File]::WriteAllBytes($ConfigPath,$Previous)
        if ($CreatedPending -and (Test-Path -LiteralPath $Pending)) { Remove-Item -LiteralPath $Pending -Force }
        Start-ScheduledTask -TaskName 'Vision Private PC'
        throw "$Failure Previous configuration restored; saved user data retained. Backup: $Backup"
    }
    Write-Host 'Native FirstUPCJax Admins is configured and its HTTPS authorization metadata verified.' -ForegroundColor Green
    Write-Host 'Next: use Sign In Planning Center.cmd on this PC, then connect the private plugin in ChatGPT.'
}
elseif ($Action -eq 'Login') {
    & $Python (Join-Path $Root 'church_login.py')
    if ($LASTEXITCODE -ne 0) { throw 'Planning Center sign-in did not finish.' }
}
else {
    & $Python (Join-Path $Root 'church_login.py') --check
    if ($LASTEXITCODE -ne 0) { throw 'Notification browser check failed.' }
    & ([string]$Config.churchAdmins.nodePath) (Join-Path $Root 'church_runner.mjs') --tools | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Scheduling engine check failed.' }
    Write-Host 'Local browser/runtime checks passed. This does not send an email.'
}
