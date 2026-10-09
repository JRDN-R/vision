# Portable checks for the Windows-side Cobalt readiness probe. No Docker
# daemon, live HTTP server, installer, or real sleep is used by these tests.
$ErrorActionPreference = 'Stop'
$ServicesPath = Join-Path $PSScriptRoot '..\vision-pc\Vortex-Services.ps1'
. $ServicesPath
$script:ServiceChecks = 0
$PinnedCommit = 'a636575b09de1fc55d9b8cd98cac88f5f2f16b42'

function Assert-ServiceEqual($Actual, $Expected, [string]$Label) {
    if ($Actual -cne $Expected) { throw "$Label expected [$Expected], got [$Actual]" }
    $script:ServiceChecks++
}
function Get-ServiceFailure([scriptblock]$Action) {
    $Caught = $null
    try { & $Action | Out-Null } catch { $Caught = $_.Exception }
    if (-not $Caught) { throw 'Expected the service health check to fail.' }
    $script:ServiceChecks++
    return $Caught
}

& {
    $script:Reads = 0
    $script:Sleeps = 0
    function Read-VortexServiceHealth {
        $script:Reads++
        if ($script:Reads -lt 3) { throw 'Simulated connection reset during host-port startup.' }
        return [pscustomobject]@{cobalt=[pscustomobject]@{version='11.7.1';services=@('youtube','twitter')};git=[pscustomobject]@{commit=$PinnedCommit}}
    }
    function Start-Sleep { param([int]$Seconds) $script:Sleeps++ }
    $Result = Get-VortexServiceHealth -Attempts 3 -DelaySeconds 0
    Assert-ServiceEqual $script:Reads 3 'Transient host readiness errors are retried'
    Assert-ServiceEqual $script:Sleeps 2 'Only failed attempts sleep'
    Assert-ServiceEqual $Result.git.commit $PinnedCommit 'Validated service response is returned'
    Assert-ServiceEqual ($Result.cobalt.services -join ',') 'youtube,twitter' 'Provider list is retained'
}

& {
    $script:Reads = 0
    $script:Sleeps = 0
    function Read-VortexServiceHealth {
        $script:Reads++
        throw 'PRIVATE_SIGNED_URL=https://example.invalid/?token=SECRET_COOKIE_VALUE'
    }
    function Start-Sleep { param([int]$Seconds) $script:Sleeps++ }
    $Failure = Get-ServiceFailure { Get-VortexServiceHealth -Attempts 3 -DelaySeconds 0 }
    Assert-ServiceEqual $script:Reads 3 'Unreachable host exhausts only the configured attempts'
    Assert-ServiceEqual $script:Sleeps 2 'No delay follows the final failed attempt'
    Assert-ServiceEqual ([bool]$Failure.Message) $true 'Final failure has a useful message'
    Assert-ServiceEqual ($Failure.Message -match 'PRIVATE_SIGNED_URL|example\.invalid|SECRET_COOKIE_VALUE') $false 'Raw transport exception is not exposed'
}

foreach ($Health in @(
    [pscustomobject]@{cobalt=[pscustomobject]@{services=@('youtube')};git=[pscustomobject]@{commit='untrusted-build'}},
    [pscustomobject]@{cobalt=[pscustomobject]@{services=@()};git=[pscustomobject]@{commit=$PinnedCommit}},
    [pscustomobject]@{cobalt=[pscustomobject]@{services=$null};git=[pscustomobject]@{commit=$PinnedCommit}},
    [pscustomobject]@{cobalt=[pscustomobject]@{services=@('youtube')}}
)) {
    & {
        $script:Reads = 0
        $script:Sleeps = 0
        function Read-VortexServiceHealth { $script:Reads++; return $Health }
        function Start-Sleep { param([int]$Seconds) $script:Sleeps++ }
        $Failure = Get-ServiceFailure { Get-VortexServiceHealth -Attempts 3 -DelaySeconds 0 }
        Assert-ServiceEqual $script:Reads 1 'Invalid pinned health is rejected immediately'
        Assert-ServiceEqual $script:Sleeps 0 'Invalid pinned health never enters retry delay'
        Assert-ServiceEqual ($Failure.Message -match '(?i)health|pinned|Cobalt') $true 'Invalid response reports concise health failure'
    }
}

& {
    $script:Reads = 0
    function Read-VortexServiceHealth { $script:Reads++; throw 'simulated unavailable service' }
    function Start-Sleep { throw 'A single attempt cannot sleep.' }
    $null = Get-ServiceFailure { Get-VortexServiceHealth -Attempts 1 -DelaySeconds 0 }
    Assert-ServiceEqual $script:Reads 1 'Single-attempt health check stays bounded'
}

# Inspect the host request implementation rather than relying on the current
# runner's proxy configuration. Windows PowerShell 5.1 has no -NoProxy switch
# on Invoke-RestMethod, so the request must explicitly disable its proxy.
$ParseErrors = $null
$Tokens = $null
$Ast = [Management.Automation.Language.Parser]::ParseFile((Resolve-Path $ServicesPath), [ref]$Tokens, [ref]$ParseErrors)
Assert-ServiceEqual @($ParseErrors).Count 0 'Service setup parses on this PowerShell version'
$Functions = @($Ast.FindAll({ param($Node) $Node -is [Management.Automation.Language.FunctionDefinitionAst] }, $true))
$ReadFunction = $Functions | Where-Object { $_.Name -eq 'Read-VortexServiceHealth' } | Select-Object -First 1
Assert-ServiceEqual ([bool]$ReadFunction) $true 'Dedicated host request helper exists'
$ReadSource = $ReadFunction.Body.Extent.Text
Assert-ServiceEqual ($ReadSource -match 'http://127\.0\.0\.1:9000/') $true 'Health probe targets the loopback API only'
Assert-ServiceEqual ($ReadSource -match '(?i)\.Proxy\s*=\s*\$null') $true 'System and user HTTP proxies are bypassed'
Assert-ServiceEqual ($ReadSource -match '(?i)\.AllowAutoRedirect\s*=\s*\$false') $true 'Health requests cannot follow redirects'
Assert-ServiceEqual ($ReadSource -match '(?i)\.Timeout\s*=\s*[1-9]\d{0,5}\b') $true 'Health request has a finite timeout'
Assert-ServiceEqual ($ReadSource -match '(?i)\.ReadWriteTimeout\s*=\s*[1-9]\d{0,5}\b') $true 'Response reads have a finite timeout'

foreach ($FunctionName in @('Install-VortexServices','Check-VortexServices')) {
    $FunctionAst = $Functions | Where-Object { $_.Name -eq $FunctionName } | Select-Object -First 1
    $Commands = @($FunctionAst.Body.FindAll({ param($Node) $Node -is [Management.Automation.Language.CommandAst] }, $true))
    $HealthCommand = $Commands | Where-Object { $_.GetCommandName() -eq 'Get-VortexServiceHealth' } | Select-Object -First 1
    Assert-ServiceEqual ([bool]$HealthCommand) $true "$FunctionName uses the shared validated host health check"
    if ($FunctionName -eq 'Install-VortexServices') {
        $Activation = $Commands | Where-Object { $_.GetCommandName() -eq 'Set-ProcessorUpdate' } | Select-Object -First 1
        Assert-ServiceEqual ([bool]$Activation) $true 'Existing processor activation path is retained'
        Assert-ServiceEqual ($HealthCommand.Extent.StartOffset -lt $Activation.Extent.StartOffset) $true 'Host readiness is checked before configuration activation'
    }
}

Write-Host "Passed $script:ServiceChecks Vortex host service health checks."
