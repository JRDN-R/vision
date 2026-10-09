# Run using Windows PowerShell 5.1 or PowerShell 7. These regressions never
# install WSL, download software, change Windows features, or stop Docker.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\vision-pc\Setup-Vortex-Windows.ps1')
$script:VortexWork = Join-Path ([IO.Path]::GetTempPath()) ('vortex-wsl-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $script:VortexWork | Out-Null
$script:VortexStatePath = Join-Path $script:VortexWork 'state.json'
$script:Checks = 0
$OriginalSystemRoot = $env:SystemRoot
$OriginalWslUtf8 = $env:WSL_UTF8
if (-not $env:SystemRoot) { $env:SystemRoot = $script:VortexWork }

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
    # The runtime label matters: a newer kernel or Windows version cannot make
    # an old WSL runtime satisfy Docker's requirement.
    Assert-Equal (ConvertFrom-VortexWslVersion 'WSL version: 2.6.3.0') ([version]'2.6.3.0') 'Modern WSL runtime'
    Assert-Equal (ConvertFrom-VortexWslVersion 'Version de WSL : 2.6.3.0') ([version]'2.6.3.0') 'Localized runtime label'
    Assert-Equal (ConvertFrom-VortexWslVersion ('WSL version' + [char]0xff1a + ' 2.6.3.0')) ([version]'2.6.3.0') 'Fullwidth colon'
    $Marked = [string][char]0xfeff + [char]0x200e + 'WSL version: ' + [char]0x2066 + '2.6.3.0' + [char]0x2069
    Assert-Equal (ConvertFrom-VortexWslVersion $Marked) ([version]'2.6.3.0') 'BOM and direction marks'
    $NullInterleaved = ([Text.Encoding]::UTF8.GetString([Text.Encoding]::Unicode.GetBytes('WSL version: 2.6.3.0')))
    Assert-Equal (ConvertFrom-VortexWslVersion $NullInterleaved) ([version]'2.6.3.0') 'Legacy NUL-interleaved capture'
    Assert-Equal (ConvertFrom-VortexWslVersion "WSL version: 1.2.0.0`nKernel version: 6.6.87.2`nWindows version: 10.0.19045.0") ([version]'1.2.0.0') 'Old runtime is not replaced by kernel version'
    foreach ($Unknown in @('', 'Kernel version: 6.6.87.2', 'WSLg version: 1.0.66', 'Windows version: 10.0.19045.0', 'Usage: wsl.exe [Argument] Windows version: 10.0.19045.0')) {
        Assert-Equal (ConvertFrom-VortexWslVersion $Unknown) $null 'Unidentified output is not a WSL version'
    }

    # The WSL output encoding override belongs only to the child query. It is
    # restored on success and failure, including a previously absent variable.
    foreach ($Prior in @($null, '0', 'custom')) {
        & {
            $env:WSL_UTF8 = $Prior
            function Invoke-VortexProgram {
                param($File, [string[]]$Arguments)
                Assert-Equal $env:WSL_UTF8 '1' 'UTF8 is requested only during WSL invocation'
                Assert-Equal ($Arguments -join ',') '--version' 'WSL arguments are retained'
                return [pscustomobject]@{ExitCode=0;Stdout='WSL version: 2.6.3.0';Stderr='';Output='WSL version: 2.6.3.0'}
            }
            $Result = Invoke-VortexWsl @('--version')
            Assert-Equal $Result.ExitCode 0 'WSL result is retained'
            Assert-Equal $env:WSL_UTF8 $Prior 'Previous encoding environment restored'
            function Invoke-VortexProgram { throw 'simulated query failure' }
            Assert-Throws { Invoke-VortexWsl @('--version') } 'simulated query failure'
            Assert-Equal $env:WSL_UTF8 $Prior 'Encoding restored after failure'
        }
    }

    # Fully working Docker is sufficient evidence to preserve its chosen
    # backend. Even a stale bootstrap restart marker must not reinstall WSL.
    foreach ($OnlyServices in @($false, $true)) {
        & {
            Write-VortexJson $script:VortexStatePath ([pscustomobject]@{restartBoot='same-boot';reason='old marker'})
            $script:Events = @()
            function Get-VortexBootId { return 'same-boot' }
            function Get-VortexRunningDocker { return [pscustomobject]@{Desktop='local-desktop';Executable='local-docker'} }
            function Enable-VortexWindowsFeatures { throw 'Unexpected feature change for working Docker' }
            function Install-VortexWsl { throw 'Unexpected WSL installation for working Docker' }
            function Request-VortexRestart { throw 'Unexpected restart for working Docker' }
            function Use-VortexDocker { param($Docker) $script:Events += 'use' }
            function Start-VortexDocker {
                param([switch]$PreserveBackend)
                Assert-Equal $PreserveBackend.IsPresent $true 'Existing backend is preserved'
                $script:Events += 'background'
            }
            Initialize-VortexPrerequisites -ServicesOnly:$OnlyServices
            $Expected = if ($OnlyServices) { 'use' } else { 'background' }
            Assert-Equal ($script:Events -join ',') $Expected 'Healthy Docker avoids prerequisite provisioning'
        }
    }
    Remove-Item -LiteralPath $script:VortexStatePath -Force

    & {
        function Get-VortexRunningDocker { return $null }
        function Enable-VortexWindowsFeatures { throw 'Unexpected feature install in services-only mode' }
        function Install-VortexWsl { throw 'Unexpected WSL install in services-only mode' }
        function Start-VortexDocker { throw 'Prohibited background engine restart' }
        function Use-VortexDocker { throw 'Unexpected unhealthy engine selection' }
        Assert-Throws { Initialize-VortexPrerequisites -ServicesOnly } '(?i)Docker'
    }
    & {
        $script:Events = @()
        function Get-VortexRunningDocker { return $null }
        function Get-CimInstance { return [pscustomobject]@{HypervisorPresent=$true} }
        function Enable-VortexWindowsFeatures { $script:Events += 'features' }
        function Install-VortexWsl { $script:Events += 'wsl' }
        function Start-VortexDocker {
            param([switch]$PreserveBackend)
            Assert-Equal $PreserveBackend.IsPresent $false 'Fresh setup may select WSL'
            $script:Events += 'docker'
        }
        Initialize-VortexPrerequisites
        Assert-Equal ($script:Events -join ',') 'features,wsl,docker' 'Missing prerequisites retain installation sequence'
    }
    & {
        Write-VortexJson $script:VortexStatePath ([pscustomobject]@{restartBoot='same-boot';reason='old marker'})
        function Get-VortexBootId { return 'same-boot' }
        function Get-VortexRunningDocker { return $null }
        function Enable-VortexWindowsFeatures { throw 'Unexpected feature change before pending restart' }
        function Install-VortexWsl { throw 'Unexpected WSL install before pending restart' }
        function Start-VortexDocker { throw 'Unexpected Docker startup before pending restart' }
        Assert-Throws { Initialize-VortexPrerequisites } '(?i)restart'
    }
    Remove-Item -LiteralPath $script:VortexStatePath -Force

    # The health shortcut must target this PC, even if the user's normal Docker
    # context or environment targets a remote host.
    foreach ($Endpoint in @('npipe:////./pipe/dockerDesktopLinuxEngine', 'npipe://./pipe/docker_engine')) {
        & {
            function Find-VortexDockerDesktop { return (Join-Path $script:VortexWork 'Docker Desktop.exe') }
            function Test-Path { return $true }
            function Invoke-VortexProgram {
                param($File, [string[]]$Arguments)
                if ($Arguments[0] -eq 'context') {
                    Assert-Equal ($Arguments -join ',') 'context,inspect,desktop-linux,--format,{{.Endpoints.docker.Host}}' 'Inspect explicit local context'
                    return [pscustomobject]@{ExitCode=0;Stdout=$Endpoint}
                }
                Assert-Equal ($Arguments -join ',') '--context,desktop-linux,info,--format,{{.OSType}}' 'Health query explicitly selects local context'
                return [pscustomobject]@{ExitCode=0;Stdout='linux'}
            }
            $Result = Get-VortexRunningDocker
            Assert-Equal ([bool]$Result.Executable) $true 'Local Linux engine is eligible for reuse'
        }
    }
    & {
        function Find-VortexDockerDesktop { return (Join-Path $script:VortexWork 'Docker Desktop.exe') }
        function Test-Path { return $true }
        $script:InfoCalled = $false
        function Invoke-VortexProgram {
            param($File, [string[]]$Arguments)
            if ($Arguments[0] -ne 'context') { $script:InfoCalled = $true }
            return [pscustomobject]@{ExitCode=0;Stdout='tcp://remote.example:2375'}
        }
        Assert-Throws { Get-VortexRunningDocker } '(?i)does not point to this PC'
        Assert-Equal $script:InfoCalled $false 'Remote context is rejected before contacting daemon'
    }
    & {
        function Find-VortexDockerDesktop { return (Join-Path $script:VortexWork 'Docker Desktop.exe') }
        function Test-Path { return $true }
        function Invoke-VortexProgram { throw (New-Object TimeoutException('simulated stalled engine')) }
        Assert-Equal (Get-VortexRunningDocker) $null 'Stalled health check is unavailable'
    }

    # Background startup settings must preserve Hyper-V or WSL backends when
    # the existing engine is already operational.
    foreach ($Backend in @($true, $false)) {
        $Settings = [pscustomobject]@{autoStart=$false;openUIOnStartupDisabled=$false;wslEngineEnabled=$Backend;useWindowsContainers=$true;unrelated=42}
        $Result = Set-VortexDockerSettings $Settings -PreserveBackend
        Assert-Equal $Result.wslEngineEnabled $Backend 'Existing WSL backend selection remains unchanged'
        Assert-Equal $Result.autoStart $true 'Background startup enabled'
        Assert-Equal $Result.openUIOnStartupDisabled $true 'Dashboard stays closed'
        Assert-Equal $Result.useWindowsContainers $false 'Linux containers selected'
        Assert-Equal $Result.unrelated 42 'Unrelated settings preserved'
        Assert-Equal @($Result.PSObject.Properties).Count 5 'No duplicate settings added'
    }

    # A healthy version never downloads. A failed/ambiguous query must stop
    # safely, rather than blindly launching a repair of the installed runtime.
    & {
        $script:WslCalls = @()
        function Invoke-VortexWsl {
            param([string[]]$Arguments)
            $script:WslCalls += ($Arguments -join ',')
            return [pscustomobject]@{ExitCode=0;Stdout='WSL version: 2.6.3.0';Stderr='';Output='WSL version: 2.6.3.0'}
        }
        function Get-VortexReleaseAsset { throw 'Unexpected WSL package download' }
        Install-VortexWsl
        Assert-Equal ($script:WslCalls -join ';') '--version;--set-default-version,2' 'Modern WSL skips installation'
    }
    foreach ($Query in @(
        [pscustomobject]@{ExitCode=0;Stdout='';Stderr='';Output=''},
        [pscustomobject]@{ExitCode=0;Stdout='Unrecognized output';Stderr='';Output='Unrecognized output'},
        [pscustomobject]@{ExitCode=1;Stdout='WSL version: 2.6.3.0';Stderr='Query failed';Output='WSL version: 2.6.3.0'},
        [pscustomobject]@{ExitCode=0;Stdout='';Stderr='WSL version: 2.6.3.0';Output='WSL version: 2.6.3.0'}
    )) {
        & {
            $script:DownloadCalled = $false
            function Invoke-VortexWsl { return $Query }
            function Get-VortexReleaseAsset { $script:DownloadCalled = $true; throw 'Unexpected download' }
            Assert-Throws { Install-VortexWsl } '(?i)WSL'
            Assert-Equal $script:DownloadCalled $false 'Unknown or failed query cannot launch MSI'
        }
    }
    foreach ($OldRuntime in @('WSL version: 1.2.0.0', 'Usage: wsl.exe [Argument]')) {
        & {
            function Invoke-VortexWsl { return [pscustomobject]@{ExitCode=0;Stdout=$OldRuntime;Stderr='';Output=$OldRuntime} }
            function Get-VortexReleaseAsset { throw 'provisioning sentinel' }
            Assert-Throws { Install-VortexWsl } 'provisioning sentinel'
        }
    }

    # MSI is never launched in these tests. Its completion codes and timeout
    # determine whether another setup run is safe, and whether a reboot is due.
    foreach ($Outcome in @('success', 'busy', 'restart', 'timeout')) {
        & {
            $Pending = Join-Path $script:VortexWork 'wsl-install-pending.json'
            $script:Downloads = 0
            $script:VersionQueries = 0
            function Get-VortexBootId { return 'msi-test-boot' }
            function Get-VortexReleaseAsset { return [pscustomobject]@{browser_download_url='https://example.invalid/wsl.msi';digest='unused'} }
            function Get-VortexDownload { $script:Downloads++ }
            function Assert-VortexDigest { }
            function Assert-VortexSignature { }
            function Invoke-VortexWsl {
                param([string[]]$Arguments)
                if ($Arguments[0] -eq '--version') { $script:VersionQueries++ }
                $VersionText = if ($Outcome -eq 'success' -and $script:VersionQueries -gt 1) { 'WSL version: 2.6.3.0' } else { 'WSL version: 1.2.0.0' }
                return [pscustomobject]@{ExitCode=0;Stdout=$VersionText;Stderr='';Output=$VersionText}
            }
            function Invoke-VortexProgram {
                param($File, [string[]]$Arguments, [int]$TimeoutSeconds, [switch]$ShowOutput, [switch]$KeepRunningOnTimeout)
                Assert-Equal ([IO.Path]::GetFileName($File)) 'msiexec.exe' 'Only the package client is invoked'
                Assert-Equal ($Arguments -contains '/norestart') $true 'MSI cannot reboot automatically'
                Assert-Equal ($Arguments -contains '/L*V') $true 'Verbose MSI log is requested'
                Assert-Equal $ShowOutput.IsPresent $true 'Waiting has progress notices'
                Assert-Equal $KeepRunningOnTimeout.IsPresent $true 'Timeout must not terminate MSI'
                Assert-Equal (Test-Path -LiteralPath $Pending) $true 'Pending state precedes MSI launch'
                if ($Outcome -eq 'timeout') { throw (New-Object TimeoutException('simulated MSI timeout')) }
                $Code = switch ($Outcome) { 'success' {0} 'busy' {1618} 'restart' {3010} }
                return [pscustomobject]@{ExitCode=$Code;Stdout='';Stderr='';Output=''}
            }
            switch ($Outcome) {
                'success' {
                    Install-VortexWsl
                    Assert-Equal $script:VersionQueries 2 'Successful install must verify runtime again'
                }
                'busy' { Assert-Throws { Install-VortexWsl } 'Windows Installer is busy' }
                'restart' {
                    Assert-Throws { Install-VortexWsl } 'Restart Windows'
                    $State = Get-Content -LiteralPath $script:VortexStatePath -Raw | ConvertFrom-Json
                    Assert-Equal $State.restartBoot 'msi-test-boot' 'MSI restart is recorded'
                }
                'timeout' {
                    Assert-Throws { Install-VortexWsl } 'may still be running'
                    $State = Get-Content -LiteralPath $Pending -Raw | ConvertFrom-Json
                    Assert-Equal $State.boot 'msi-test-boot' 'Unknown MSI completion retains boot marker'
                    Assert-Equal ([bool]$State.log) $true 'Unknown MSI completion retains diagnostic log path'
                    Assert-Throws { Install-VortexWsl } 'previous WSL installer did not report completion'
                    Assert-Equal $script:Downloads 1 'Same-boot retry cannot download or launch a second MSI'
                }
            }
            Assert-Equal (Test-Path -LiteralPath $Pending) ($Outcome -eq 'timeout') 'Pending marker reflects known MSI completion'
            Remove-Item -LiteralPath $Pending,$script:VortexStatePath -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Host "Passed $script:Checks Vortex WSL and Docker reuse checks."
} finally {
    $env:SystemRoot = $OriginalSystemRoot
    $env:WSL_UTF8 = $OriginalWslUtf8
    Remove-Item -LiteralPath $script:VortexWork -Recurse -Force
}
