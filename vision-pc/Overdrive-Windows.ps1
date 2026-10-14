#requires -Version 5.1
#requires -RunAsAdministrator
<#
Configure optional Qwen Ultra Mac RPC acceleration on an existing Vision PC.
Does not start, stop or restart FUPCJ, open firewall ports, or change user settings.
Requires a reviewed llama.cpp b11559 build with Vulkan+RPC compiled in.
The Mac must be connected through TWO authenticated local SSH forwards.
#>
[CmdletBinding()]
param(
    [ValidateSet('Configure','Status','Disable')][string]$Action = 'Status',
    [string]$RuntimeDirectory = '',
    [string]$WorkerId = '',
    [ValidateSet(8,9,10)][int]$MacBudgetGiB = 8,
    [ValidateRange(1024,65535)][int]$RpcForwardPort = 51052,
    [ValidateRange(1024,65535)][int]$HealthForwardPort = 51053
)
$ErrorActionPreference = 'Stop'
$Root = Join-Path $env:ProgramData 'VisionPC'
$ConfigPath = Join-Path $Root 'config.json'
$Python = Join-Path $Root 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $ConfigPath)) { throw 'Vision PC is not installed.' }
if ($RpcForwardPort -eq $HealthForwardPort) { throw 'RPC and health ports must be different.' }
$Config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $Config.localAI -or -not $Config.localAI.enabled) { throw 'Install and enable local Qwen on FUPCJ first.' }

function Save-Config {
    $json = ConvertTo-Json -InputObject $Config -Depth 80
    $temp = Join-Path $Root ('.overdrive-config-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $backup = Join-Path $Root 'config-before-overdrive.bak'
    try {
        [IO.File]::WriteAllText($temp, $json + [Environment]::NewLine, (New-Object Text.UTF8Encoding($false)))
        # File.Replace is atomic on the same volume and saves the previous configuration.
        [IO.File]::Replace($temp, $ConfigPath, $backup)
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
    }
}

if ($Action -eq 'Status') {
    $value = $Config.localAI.overdrive
    if (-not $value -or $value.enabled -ne $true) {
        Write-Host 'Overdrive disabled or not configured. Qwen Ultra uses the FUPCJ processor.'
        exit 0
    }
    Write-Host ('Overdrive configured: Mac worker ' + $value.workerId)
    try {
        $health = Invoke-RestMethod -Uri ('http://127.0.0.1:' + [int]$value.healthPort + '/health') -TimeoutSec 2 -ErrorAction Stop
        if ($health.rpcReady -eq $true -and $health.workerId -eq $value.workerId) {
            Write-Host 'Mac RPC worker is reachable over the localhost health SSH forward.' -ForegroundColor Green
        } else {
            Write-Host 'Mac health response does not match the registered worker. FUPCJ remains available.' -ForegroundColor Yellow
        }
    } catch {
        Write-Host 'Mac offline or tunnel unavailable. FUPCJ remains available.' -ForegroundColor Yellow
    }
    if (Test-Path -LiteralPath $Python) {
        & $Python (Join-Path $Root 'setup_qwen_overdrive.py') --config $ConfigPath --action check
    }
    exit 0
}
if ($Action -eq 'Disable') {
    if ($Config.localAI.overdrive) {
        $Config.localAI.overdrive | Add-Member -MemberType NoteProperty -Name enabled -Value $false -Force
        Save-Config
    }
    Write-Host 'Overdrive disabled in config. FUPCJ models/receipts unchanged.'
    Write-Host 'Restart Vision PC once active jobs have completed to apply the change.'
    exit 0
}

if ($WorkerId -cnotmatch '^[a-f0-9]{32}$') { throw 'Enter the exact 32-character worker ID printed by the Mac helper.' }
if (-not $RuntimeDirectory -or -not (Test-Path -LiteralPath $RuntimeDirectory -PathType Container)) {
    throw 'Supply a local folder containing the reviewed RPC+Vulkan Windows llama-server.exe and DLLs.'
}
$source = (Resolve-Path -LiteralPath $RuntimeDirectory).ProviderPath
$binary = Join-Path $source 'llama-server.exe'
if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) { throw 'The supplied folder has no llama-server.exe.' }
if ((Get-Item -LiteralPath $binary -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Executable symlinks are not allowed.'
}
# Capture native process help without PowerShell 5.1 treating stderr as a terminating error.
$Stdout = Join-Path $env:TEMP ('overdrive-help-' + [Guid]::NewGuid().ToString('N') + '.out')
$Stderr = $Stdout + '.err'
try {
    $HelpProcess = Start-Process -FilePath $binary -ArgumentList '--help' -Wait -PassThru -NoNewWindow -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr
    $usage = (Get-Content -LiteralPath $Stdout -Raw -ErrorAction SilentlyContinue) + (Get-Content -LiteralPath $Stderr -Raw -ErrorAction SilentlyContinue)
    if ($usage -notmatch '\-\-rpc' -or $usage -notmatch '\-\-tensor-split') {
        throw 'This Windows llama-server was not compiled with RPC support.'
    }
} finally {
    Remove-Item -LiteralPath $Stdout,$Stderr -ErrorAction SilentlyContinue
}
$destination = Join-Path $Root 'rpc-b11559-overdrive'
if (Test-Path -LiteralPath $destination) {
    throw 'A pinned RPC runtime already exists. Review/backup it before installing a different build.'
}
New-Item -ItemType Directory -Path $destination | Out-Null
try {
    $files = @{}
    $list = @(Get-ChildItem -LiteralPath $source -File | Where-Object { $_.Name -eq 'llama-server.exe' -or $_.Extension -ieq '.dll' })
    if ($list.Count -lt 1 -or $list.Count -gt 200) { throw 'Unexpected RPC runtime file inventory.' }
    foreach ($item in $list) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Runtime symlinks are not allowed.' }
        $target = Join-Path $destination $item.Name
        Copy-Item -LiteralPath $item.FullName -Destination $target
        $files[$item.Name] = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $manifestPath = Join-Path $destination 'rpc-manifest.json'
    $manifest = [ordered]@{
        schema = 'vision-overdrive-rpc-runtime-v1'
        runtimeCommit = 'f2cc7282ce0042641df55e0e563b75e824e2b647'
        entrypoint = 'llama-server.exe'
        files = $files
    }
    [IO.File]::WriteAllText($manifestPath, (ConvertTo-Json -InputObject $manifest -Depth 10),
                            (New-Object Text.UTF8Encoding($false)))
    $option = [pscustomobject]@{
        enabled = $true
        workerId = $WorkerId
        rpcPort = $RpcForwardPort
        healthPort = $HealthForwardPort
        macBudgetGiB = $MacBudgetGiB
        runtimeManifestPath = $manifestPath
    }
    $Config.localAI | Add-Member -MemberType NoteProperty -Name overdrive -Value $option -Force
    Save-Config
} catch {
    if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Recurse -Force }
    throw
}
Write-Host 'Overdrive configured, but NOT enabled for prompts until on-device Tune passes.' -ForegroundColor Green
Write-Host 'Ensure both SSH forwards are active; then tune (outside OBS/Streamlabs):'
Write-Host ('  & "' + $Python + '" "' + (Join-Path $Root 'setup_qwen_overdrive.py') + '" --config "' + $ConfigPath + '" --action tune --context 8192')
Write-Host 'Restart Vision PC after tuning, when the existing work queue is idle.'
