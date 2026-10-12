#requires -Version 5.1
<# Explicit local-model operations. No action runs implicitly during the ordinary updater. #>
[CmdletBinding()]
param(
    [ValidateSet('Inventory','UpdateCode','Download','Check','Benchmark','Enable','Disable','EnableDefault','Rollback')]
    [string]$Action = 'Inventory',
    [string]$InstallRoot = (Join-Path $env:ProgramData 'VisionPC'),
    [string]$SourceRef = '',
    [ValidateRange(0,99)][int]$GpuLayers = 0,
    [ValidateSet(4096,8192,16384)][int]$ContextTokens = 4096
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ConfigPath = Join-Path $InstallRoot 'config.json'
$Python = Join-Path $InstallRoot 'runtime\python.exe'
$ModelRoot = Join-Path $InstallRoot 'local-ai'
$Utf8 = New-Object Text.UTF8Encoding($false)
function Save-Json([string]$Path, $Value) {
    $Temporary = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    [IO.File]::WriteAllText($Temporary, ($Value | ConvertTo-Json -Depth 30), $Utf8)
    if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($Temporary, $Path, $null) }
    else { [IO.File]::Move($Temporary, $Path) }
}
function Verified-Download([string]$Url,[string]$Path,[long]$Size,[string]$Hash) {
    if (Test-Path -LiteralPath $Path) {
        if ((Get-Item -LiteralPath $Path).Length -eq $Size -and (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $Hash) { return }
        throw 'An existing model/runtime artifact differs from the pinned checksum. It was not replaced.'
    }
    $Part = $Path + '.part'
    $Curl = Get-Command curl.exe -ErrorAction Stop
    & $Curl.Source --fail --location --proto '=https' --proto-redir '=https' --retry 3 --continue-at - --output $Part $Url
    if ($LASTEXITCODE -ne 0) { throw 'Download interrupted; rerun Download to resume the staging file.' }
    if ((Get-Item -LiteralPath $Part).Length -ne $Size -or (Get-FileHash -LiteralPath $Part -Algorithm SHA256).Hash -ne $Hash) {
        throw 'Downloaded bytes do not match the pinned artifact. The partial file was retained for inspection, not installed.'
    }
    Move-Item -LiteralPath $Part -Destination $Path
}
function Verify-RuntimeFiles([string]$Archive,[string]$Directory) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    $Hashes = @{}
    try {
        foreach ($Entry in $Zip.Entries) {
            if (-not $Entry.Name) { continue }
            $Target = [IO.Path]::GetFullPath((Join-Path $Directory $Entry.FullName))
            if (-not $Target.StartsWith([IO.Path]::GetFullPath($Directory).TrimEnd('\') + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe runtime archive path.' }
            $Stream = $Entry.Open()
            $Digest = [Security.Cryptography.SHA256]::Create()
            try { $Expected = [BitConverter]::ToString($Digest.ComputeHash($Stream)).Replace('-','').ToLowerInvariant() }
            finally { $Stream.Dispose(); $Digest.Dispose() }
            if (-not (Test-Path -LiteralPath $Target -PathType Leaf) -or (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash -ne $Expected) {
                throw 'An installed runtime file differs from the pinned ZIP. It was not accepted as a new baseline.'
            }
            $Hashes[$Entry.FullName.Replace('\','/')] = $Expected
        }
        $Actual = @(Get-ChildItem -LiteralPath $Directory -File -Recurse)
        if ($Actual.Count -ne $Hashes.Count) { throw 'Unexpected files in the private runtime directory; inspect before enabling.' }
    } finally { $Zip.Dispose() }
    return $Hashes
}
if ($Action -eq 'UpdateCode') {
    if ($SourceRef -notmatch '^[a-fA-F0-9]{40}$') { throw 'Pass one reviewed application commit SHA with -SourceRef.' }
    if ([IO.Path]::GetFullPath($InstallRoot) -ne [IO.Path]::GetFullPath((Join-Path $env:ProgramData 'VisionPC'))) {
        throw 'The normal updater uses ProgramData\VisionPC. A custom installation needs its own reviewed maintenance procedure.'
    }
    # An older installed updater does not know the new processor-file inventory.
    # Fetch the updater from the SAME immutable revision before invoking it.
    $Updater = Join-Path ([IO.Path]::GetTempPath()) ('Vision-Updater-' + [Guid]::NewGuid().ToString('N') + '.ps1')
    try {
        Invoke-WebRequest -UseBasicParsing -Uri ('https://raw.githubusercontent.com/JRDN-R/vision/' + $SourceRef + '/vision-pc/Setup-Vision-PC.ps1') -OutFile $Updater -TimeoutSec 120
        if ((Get-Item -LiteralPath $Updater).Length -eq 0) { throw 'The pinned updater download is empty.' }
        & $Updater -Action Update -SourceRef $SourceRef
    } finally {
        if (Test-Path -LiteralPath $Updater) { Remove-Item -LiteralPath $Updater -Force }
    }
    exit
}
if (-not (Test-Path -LiteralPath $ConfigPath)) { throw 'Install Vision PC before configuring the optional local model.' }
$Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($Action -eq 'Inventory') {
    Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,TotalVisibleMemorySize,FreePhysicalMemory
    Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors
    Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion
    Get-PSDrive -PSProvider FileSystem | Select-Object Name,Used,Free
    Get-ScheduledTask -TaskName 'Vision Private PC' | Select-Object TaskName,State,Principal
    Write-Host 'CIM is not an accurate Vulkan allocation/VRAM budget measurement. Check captures runtime device inventory.'
    exit
}
if ($Action -eq 'EnableDefault') {
    throw 'All-user default activation is deliberately blocked in this release: tested photo input, workload/memory measurements, and the complete acceptance gates are still pending. Explicit text-only Qwen selection can be enabled separately.'
}
if ($Action -eq 'Rollback') {
    throw 'Use Disable to stop accepting local jobs, then the reviewed normal updater rollback under a maintenance boundary. Never restore a stale database over newer conversations. See docs/local-qwen-rag.md.'
}
if ($Action -eq 'Download') {
    $Manifest = Get-Content -LiteralPath (Join-Path $InstallRoot 'local-model-manifest.json') -Raw | ConvertFrom-Json
    New-Item -ItemType Directory -Force -Path $ModelRoot | Out-Null
    $Drive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($ModelRoot).TrimEnd('\').TrimEnd(':'))
    if ($Drive.Free -lt 22GB) { throw 'Keep at least 22 GiB free for model staging, runtime, and operating headroom. Permanent sources will not be removed.' }
    $Archive = Join-Path $ModelRoot ($Manifest.runtimeTag + '-windows-vulkan.zip')
    Verified-Download $Manifest.runtimeUrl $Archive $Manifest.runtimeArchiveBytes $Manifest.runtimeArchiveSha256
    $RuntimeDirectory = Join-Path $ModelRoot $Manifest.runtimeTag
    if (-not (Test-Path -LiteralPath $RuntimeDirectory)) {
        $Stage = $RuntimeDirectory + '.staging'
        if (Test-Path -LiteralPath $Stage) { throw 'An incomplete runtime staging directory exists. Inspect it before retrying.' }
        Expand-Archive -LiteralPath $Archive -DestinationPath $Stage
        Move-Item -LiteralPath $Stage -Destination $RuntimeDirectory
    }
    $Executable = @(Get-ChildItem -LiteralPath $RuntimeDirectory -Recurse -Filter llama-server.exe)
    if ($Executable.Count -ne 1) { throw 'The pinned package must contain exactly one llama-server executable.' }
    $RuntimeHashes = Verify-RuntimeFiles $Archive $RuntimeDirectory
    $ModelPath = Join-Path $ModelRoot $Manifest.modelFile
    $Base = 'https://huggingface.co/' + $Manifest.modelRepository + '/resolve/' + $Manifest.modelRevision + '/'
    Verified-Download ($Base + $Manifest.modelFile) $ModelPath $Manifest.modelBytes $Manifest.modelSha256
    Invoke-WebRequest -UseBasicParsing -Uri ($Base + 'README.md') -OutFile (Join-Path $ModelRoot 'MODEL-CARD.md')
    Invoke-WebRequest -UseBasicParsing -Uri ('https://raw.githubusercontent.com/ggml-org/llama.cpp/' + $Manifest.runtimeCommit + '/LICENSE') -OutFile (Join-Path $ModelRoot 'LLAMA-LICENSE.txt')
    Invoke-WebRequest -UseBasicParsing -Uri ('https://huggingface.co/Qwen/Qwen3.8-27B/resolve/' + $Manifest.baseModelRevision + '/LICENSE') -OutFile (Join-Path $ModelRoot 'MODEL-LICENSE.txt')
    $Manifest | Add-Member -NotePropertyName runtimeRoot -NotePropertyValue $RuntimeDirectory -Force
    $Manifest | Add-Member -NotePropertyName runtimeFiles -NotePropertyValue $RuntimeHashes -Force
    $Manifest | Add-Member -NotePropertyName runtimeSha256 -NotePropertyValue ((Get-FileHash -LiteralPath $Executable[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()) -Force
    $InstalledManifest = Join-Path $ModelRoot 'installed-manifest.json'
    Save-Json $InstalledManifest $Manifest
    if (-not $Config.localAI) { $Config | Add-Member -NotePropertyName localAI -NotePropertyValue ([PSCustomObject]@{}) }
    $Local = $Config.localAI
    foreach ($Pair in @{runtimePath=$Executable[0].FullName;modelPath=$ModelPath;manifestPath=$InstalledManifest;port=8101;contextTokens=4096;maxOutputTokens=2048;threads=8;gpuLayers=0;reserveRamGiB=10}.GetEnumerator()) {
        if (-not $Local.PSObject.Properties[$Pair.Key]) { $Local | Add-Member -NotePropertyName $Pair.Key -NotePropertyValue $Pair.Value }
    }
    if (-not $Local.PSObject.Properties['enabled']) { $Local | Add-Member -NotePropertyName enabled -NotePropertyValue $false }
    Save-Json $ConfigPath $Config
    Write-Host 'Pinned files installed. Qwen and account-default migration remain disabled. Run Check, then Benchmark.'
    exit
}
if ($Action -in @('Check','Benchmark')) {
    if (-not $Config.localAI) { throw 'Run Download first.' }
    $Report = Join-Path $ModelRoot ($Action.ToLowerInvariant() + '-' + $GpuLayers + '-' + $ContextTokens + '.json')
    $CheckAction = $Action.ToLowerInvariant()
    & $Python (Join-Path $InstallRoot 'setup_local_ai.py') --config $ConfigPath --action $CheckAction --gpu-layers $GpuLayers --context $ContextTokens --output $Report
    if ($LASTEXITCODE -ne 0) { throw 'Local check failed. Do not enable the default.' }
    Write-Host ('Report: ' + $Report)
    exit
}
if (-not $Config.localAI) { throw 'No local model configuration is installed.' }
if ($Action -eq 'Enable') {
    $Report = Join-Path $ModelRoot ('benchmark-' + $GpuLayers + '-' + $ContextTokens + '.json')
    $Passed = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
    if (-not $Passed.checks.integrity -or -not $Passed.checks.syntheticAnswer) { throw 'Run a successful Benchmark for this exact profile first.' }
    if ((Get-FileHash -LiteralPath $Config.localAI.manifestPath -Algorithm SHA256).Hash -ne $Passed.manifestSha256) { throw 'The installed manifest changed after the benchmark. Check and benchmark again.' }
    $Config.localAI.gpuLayers = $GpuLayers
    $Config.localAI.contextTokens = $ContextTokens
}
$Config.localAI.enabled = ($Action -eq 'Enable')
Save-Json $ConfigPath $Config
Write-Host 'Configuration saved. Restart Vision Private PC during an authorized maintenance window to apply it. No service was stopped by this action.'
