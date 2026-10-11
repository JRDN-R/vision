#requires -Version 5.1
#requires -RunAsAdministrator
<# Refreshes native retrieval tools; preserves existing Cobalt configuration.
   -EnablePublicXFallback explicitly authorizes sending public X post IDs to
   api.fxtwitter.com. It does not send cookies or enable private-post access.
#>
[CmdletBinding()]
param(
    [ValidatePattern('^(main|[0-9a-f]{40})$')][string]$SourceRef = 'main',
    [switch]$EnablePublicXFallback
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = Join-Path $env:ProgramData 'VisionPC'
$ConfigPath = Join-Path $Root 'config.json'
if (-not (Test-Path -LiteralPath $ConfigPath)) { throw 'Install Vision PC before running this update.' }
foreach ($Path in @($Root, $ConfigPath)) {
    if ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Refusing a linked installation or configuration path.'
    }
}
$Setup = Join-Path $Root ('downloads\retrieval-setup-' + [Guid]::NewGuid().ToString('N') + '.ps1')
try {
    Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/JRDN-R/vision/$SourceRef/vision-pc/Setup-Vision-PC.ps1" -OutFile $Setup -TimeoutSec 120
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Setup -Action InstallVortexTools -SourceRef $SourceRef
    if ($LASTEXITCODE -ne 0) { throw 'The existing Vortex installer failed. Its update/rollback result is shown above.' }
    $Check = @'
import importlib.metadata, json, pathlib, sys
cfg = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
sys.path.insert(0, cfg['vortex']['packagesPath'])
for package, module in [('yt-dlp','yt_dlp'), ('gallery-dl','gallery_dl'), ('instaloader','instaloader'), ('spotdl','spotdl')]:
    __import__(module)
    print(package + ': ' + importlib.metadata.version(package) + ' available')
'@
    $Python = Join-Path $Root 'runtime\python.exe'
    & $Python -c $Check $ConfigPath
    if ($LASTEXITCODE -ne 0) { throw 'A retrieval dependency could not be imported. Do not treat the installation as ready.' }
    if ($EnablePublicXFallback) {
        $Original = [IO.File]::ReadAllText($ConfigPath)
        $OriginalHash = (Get-FileHash -LiteralPath $ConfigPath -Algorithm SHA256).Hash
        $Config = $Original | ConvertFrom-Json
        if (-not $Config.vortex.services) {
            $Config.vortex | Add-Member -NotePropertyName services -NotePropertyValue ([pscustomobject]@{})
        }
        if ($Config.vortex.services.fxembed.enabled -eq $true) {
            Write-Host 'Keeping the already configured FxEmbed service.'
        } else {
            Write-Host 'Enabling public X-post fallback through api.fxtwitter.com. No source-account cookies are sent.'
            $Config.vortex.services | Add-Member -NotePropertyName fxembed -NotePropertyValue ([pscustomobject]@{
                enabled = $true; url = 'https://api.fxtwitter.com/'; allowExternal = $true
            }) -Force
            $Staged = Join-Path $Root ('config-retrieval-' + [Guid]::NewGuid().ToString('N') + '.json')
            $Backup = Join-Path $Root ('config-before-retrieval-' + [Guid]::NewGuid().ToString('N') + '.json')
            $Stopped = $false
            $Replaced = $false
            try {
                [IO.File]::WriteAllText($Staged, ($Config | ConvertTo-Json -Depth 100), (New-Object Text.UTF8Encoding($false)))
                $NewHash = (Get-FileHash -LiteralPath $Staged -Algorithm SHA256).Hash
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Setup -Action Stop
                if ($LASTEXITCODE -ne 0) { throw 'The processor could not be stopped safely.' }
                $Stopped = $true
                if ((Get-FileHash -LiteralPath $ConfigPath -Algorithm SHA256).Hash -ne $OriginalHash) {
                    throw 'Configuration changed during setup. No configuration was overwritten.'
                }
                [IO.File]::Replace($Staged, $ConfigPath, $Backup)
                $Replaced = $true
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Setup -Action Start
                if ($LASTEXITCODE -ne 0) { throw 'Processor health check failed after enabling the fallback.' }
                $Stopped = $false
                Remove-Item -LiteralPath $Backup -Force
            } catch {
                if ($Replaced -and (Test-Path -LiteralPath $Backup) -and
                    (Get-FileHash -LiteralPath $ConfigPath -Algorithm SHA256).Hash -eq $NewHash) {
                    [IO.File]::Replace($Backup, $ConfigPath, $null)
                }
                throw
            } finally {
                if ($Stopped) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Setup -Action Start }
                if (Test-Path -LiteralPath $Staged) { Remove-Item -LiteralPath $Staged -Force }
            }
        }
    }
    Write-Host 'Retrieval code and native tools updated. Test an original post URL in Vortex; provider availability is checked per request.'
} finally {
    if (Test-Path -LiteralPath $Setup) { Remove-Item -LiteralPath $Setup -Force }
}
