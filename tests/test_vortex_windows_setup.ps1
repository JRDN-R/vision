# Run using Windows PowerShell 5.1. Tests never enable Windows features, download
# dependencies, install software, restart Windows, or start/stop a Docker engine.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\vision-pc\Setup-Vortex-Windows.ps1')
$script:VortexWork = Join-Path ([IO.Path]::GetTempPath()) ('vortex-setup-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $script:VortexWork | Out-Null
$script:VortexStatePath = Join-Path $script:VortexWork 'state.json'
$script:Checks = 0

function Assert-Equal($Actual, $Expected, [string]$Label) {
    if ($Actual -cne $Expected) { throw "$Label expected [$Expected], got [$Actual]" }
    $script:Checks++
}
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    $Caught = $null
    try { & $Action } catch { $Caught = $_ }
    if (-not $Caught -or $Caught.Exception.Message -notmatch $Pattern) { throw "Expected failure matching $Pattern" }
    $script:Checks++
}

try {
    # A reboot marker survives reruns and identifies the boot on which a feature
    # was installed; it does not claim that the feature is already operational.
    function Get-VortexBootId { return '639271440000000000' }
    Assert-Throws { Request-VortexRestart 'WSL needs activation.' } 'Restart Windows'
    $State = Get-Content -LiteralPath $script:VortexStatePath -Raw | ConvertFrom-Json
    Assert-Equal $State.restartBoot '639271440000000000' 'Saved boot identifier'
    Assert-Equal $State.reason 'WSL needs activation.' 'Saved restart reason'

    # The exact observed failure: Windows installation succeeded but the feature
    # is EnablePending. No second enable or subsequent Docker start is allowed.
    $script:FeatureStates = @{'Microsoft-Windows-Subsystem-Linux'='EnablePending';'VirtualMachinePlatform'='Enabled'}
    $script:Enabled = @()
    $script:RegistryRestart = $false
    function Get-WindowsOptionalFeature { param([switch]$Online, $FeatureName) return [pscustomobject]@{State=$script:FeatureStates[$FeatureName]} }
    function Enable-WindowsOptionalFeature {
        param([switch]$Online, $FeatureName, [switch]$All, [switch]$NoRestart)
        if (-not $All -or -not $NoRestart) { throw 'Windows features must be enabled without an automatic reboot.' }
        $script:Enabled += $FeatureName
    }
    function Test-VortexPendingRestart { return $script:RegistryRestart }
    $script:ReachedDocker = $false
    Assert-Throws { Enable-VortexWindowsFeatures; $script:ReachedDocker = $true } 'Windows features.*restart'
    Assert-Equal $script:ReachedDocker $false 'Pending reboot gates Docker'
    Assert-Equal $script:Enabled.Count 0 'Pending features are not installed again'

    $script:FeatureStates['Microsoft-Windows-Subsystem-Linux'] = 'Disabled'
    Assert-Throws { Enable-VortexWindowsFeatures } 'Windows features.*restart'
    Assert-Equal ($script:Enabled -join ',') 'Microsoft-Windows-Subsystem-Linux' 'Only the missing feature is installed'
    $script:FeatureStates['Microsoft-Windows-Subsystem-Linux'] = 'Enabled'
    $script:RegistryRestart = $true
    Assert-Throws { Enable-VortexWindowsFeatures } 'Windows features.*restart'
    $script:RegistryRestart = $false
    Enable-VortexWindowsFeatures
    $script:Checks++

    # Settings migrations preserve unrelated values, nested proxy configuration,
    # and older key casing without creating duplicate case-variant JSON keys.
    $Legacy = '{"autoStart":false,"openUIOnStartupDisabled":false,"wslEngineEnabled":false,"useWindowsContainers":true,"customSetting":{"keep":42}}' | ConvertFrom-Json
    $Settings = Set-VortexDockerSettings $Legacy
    Assert-Equal $Settings.autoStart $true 'Starts after sign-in'
    Assert-Equal $Settings.openUIOnStartupDisabled $true 'Dashboard stays closed'
    Assert-Equal $Settings.wslEngineEnabled $true 'WSL selected'
    Assert-Equal $Settings.useWindowsContainers $false 'Linux containers selected'
    Assert-Equal $Settings.customSetting.keep 42 'Unrelated settings preserved'
    Assert-Equal @($Settings.PSObject.Properties).Count 5 'No duplicate settings'
    $Settings = Set-VortexDockerSettings ([pscustomobject]@{})
    Assert-Equal $Settings.OpenUIOnStartupDisabled $true 'New settings file'
    $JsonPath = Join-Path $script:VortexWork 'settings.json'
    Write-VortexJson $JsonPath $Settings
    Assert-Equal (Get-Content $JsonPath -Raw | ConvertFrom-Json).AutoStart $true 'JSON round trip'

    # Binary provenance is checked before execution.
    $Artifact = Join-Path $script:VortexWork 'checksum-test.txt'
    [IO.File]::WriteAllText($Artifact, 'verified test content')
    $Hash = (Get-FileHash $Artifact -Algorithm SHA256).Hash
    Assert-VortexDigest $Artifact ('sha256:' + $Hash)
    $script:Checks++
    Assert-Throws { Assert-VortexDigest $Artifact ('sha256:' + ('0' * 64)) } 'checksum verification failed'
    Assert-Throws { Assert-VortexDigest $Artifact '' } 'did not provide'
    Assert-Throws { Get-VortexDownload 'http://example.com/tool.exe' $Artifact } 'require HTTPS'
    Assert-Throws { Get-VortexReleaseAsset 'untrusted/repository' '.*' } 'Unexpected dependency'

    # Real child-process exit handling and Windows argument quoting, using only
    # the existing PowerShell executable, including paths with spaces.
    if ($env:OS -eq 'Windows_NT') {
        $Child = Join-Path $script:VortexWork 'child with spaces.ps1'
        [IO.File]::WriteAllText($Child, 'param([string]$Value) [Console]::Out.Write($Value); exit 7')
        $Result = Invoke-VortexProgram (Join-Path $PSHOME 'powershell.exe') @('-NoProfile','-File',$Child,'a path with spaces\')
        Assert-Equal $Result.ExitCode 7 'Nonzero child exit is retained'
        Assert-Equal $Result.Output 'a path with spaces\' 'Spaces and trailing slash survive quoting'
        $Result = Invoke-VortexProgram (Join-Path $PSHOME 'powershell.exe') @('-NoProfile','-Command','[Console]::Out.Write("quoted text"); exit 0')
        Assert-Equal $Result.ExitCode 0 'Successful child exit'
        Assert-Equal $Result.Output 'quoted text' 'Embedded quotes survive quoting'
    }

    Write-Host "Passed $script:Checks Vortex Windows setup checks."
} finally {
    Remove-Item -LiteralPath $script:VortexWork -Recurse -Force
}
