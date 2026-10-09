#requires -Version 5.1
<# Invoked from Setup-Vision-PC, with the same private directories and rollback helpers. #>
function Install-VortexServices {
    Assert-InstalledProcessor
    $Configuration = Read-Configuration
    Invoke-Checked $PythonExe @('-c','import psutil')
    # A real Linux Docker daemon is required; never silently install a VM,
    # enable Windows features, or forward URLs to somebody else's instance.
    $Docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $Docker) { throw 'Cobalt requires Docker with Linux containers running. Install/enable Docker first; native Vortex engines remain available.' }
    $EngineOS = & $Docker.Source info --format '{{.OSType}}'
    if ($LASTEXITCODE -ne 0 -or $EngineOS -ne 'linux') { throw 'Start the Docker Linux daemon before installing Cobalt. Native Vortex engines remain available.' }
    Invoke-Checked $Docker.Source @('compose','version')
    $Git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $Git) { throw 'Git is required to build the pinned Cobalt service source.' }
    $InstallId = [Guid]::NewGuid().ToString('N')
    $StageDirectory = Join-Path $DownloadDir ('vortex-services-' + $InstallId)
    $ServiceDirectory = Join-Path $ToolsDir ('vortex-services-' + $InstallId)
    New-Item -ItemType Directory -Path $StageDirectory,$ServiceDirectory | Out-Null
    Set-PrivateDirectory $StageDirectory
    Set-PrivateDirectory $ServiceDirectory
    $SourceBase = "https://raw.githubusercontent.com/JRDN-R/vision/$SourceRef/vision-pc"
    foreach ($File in $ProcessorFiles) { Get-Download "$SourceBase/$File" (Join-Path $StageDirectory $File) }
    foreach ($File in @('vortex-services.compose.yml','vortex_egress.py','vortex_network.py','vortex_urls.py','vortex-egress-policy.Dockerfile','vortex_egress_policy.sh')) {
        Copy-Item -LiteralPath (Join-Path $StageDirectory $File) -Destination (Join-Path $ServiceDirectory $File)
    }
    $CobaltSource = Join-Path $ServiceDirectory 'cobalt'
    Invoke-Checked $Git.Source @('clone','--no-checkout','https://github.com/imputnet/cobalt.git',$CobaltSource)
    Invoke-Checked $Git.Source @('-C',$CobaltSource,'checkout','--detach','a636575b09de1fc55d9b8cd98cac88f5f2f16b42')
    $Compose = Join-Path $ServiceDirectory 'vortex-services.compose.yml'
    $ComposeArgs = @('compose','--project-name','vision-vortex-services','--file',$Compose)
    $PreviousDirectory = if ($Configuration.vortex) { $Configuration.vortex.serviceDirectory } else { $null }
    try {
        Invoke-Checked $Docker.Source ($ComposeArgs + @('up','--detach','--build','--wait','--wait-timeout','120'))
        $Health = Invoke-RestMethod -Uri 'http://127.0.0.1:9000/' -TimeoutSec 10
        if (-not $Health.cobalt.services -or $Health.git.commit -ne 'a636575b09de1fc55d9b8cd98cac88f5f2f16b42') {
            throw 'The pinned Cobalt API health check failed.'
        }
        if (-not $Configuration.vortex) { $Configuration | Add-Member -NotePropertyName vortex -NotePropertyValue ([pscustomobject]@{}) }
        if (-not $Configuration.vortex.services) { $Configuration.vortex | Add-Member -NotePropertyName services -NotePropertyValue ([pscustomobject]@{}) }
        $Configuration.vortex.services | Add-Member -NotePropertyName cobalt -NotePropertyValue ([pscustomobject]@{
            enabled = $true; url = 'http://127.0.0.1:9000/'; publicEgressOnly = $true
        }) -Force
        $Configuration.vortex | Add-Member -NotePropertyName serviceDirectory -NotePropertyValue $ServiceDirectory -Force
        Set-ProcessorUpdate -Configuration $Configuration -StageDirectory $StageDirectory -CheckGoogleSignIn $true
        Write-Host 'Cobalt API is healthy. Per-source downloading still depends on anonymous provider access. FxEmbed and twitter-video-dl were not activated; see VORTEX.md.'
    } catch {
        & $Docker.Source @ComposeArgs down | Out-Null
        if ($PreviousDirectory -and (Test-Path (Join-Path $PreviousDirectory 'vortex-services.compose.yml'))) {
            & $Docker.Source compose --project-name vision-vortex-services --file (Join-Path $PreviousDirectory 'vortex-services.compose.yml') up --detach | Out-Null
        }
        throw
    }
}

function Check-VortexServices {
    $Configuration = Read-Configuration
    $Directory = $Configuration.vortex.serviceDirectory
    if (-not $Directory) { Write-Host 'No local extraction service is configured. Native engines remain independent.'; return }
    Invoke-Checked 'docker' @('compose','--project-name','vision-vortex-services','--file',(Join-Path $Directory 'vortex-services.compose.yml'),'ps')
    $Health = Invoke-RestMethod -Uri 'http://127.0.0.1:9000/' -TimeoutSec 10
    Write-Host ('Cobalt version: ' + $Health.cobalt.version)
    Write-Host ('Enabled providers: ' + ($Health.cobalt.services -join ', '))
}
