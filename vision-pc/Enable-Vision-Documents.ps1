#requires -Version 5.1
<# Run in Administrator PowerShell on the existing Vision PC. #>
[CmdletBinding()]
param([ValidateSet('Full','Standard')][string]$Profile='Standard')
$ErrorActionPreference='Stop'
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$Principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Open Windows PowerShell as Administrator on the Vision PC.' }
# Resolve one immutable source revision for the whole installation.
$Revision=(Invoke-RestMethod -Uri 'https://api.github.com/repos/JRDN-R/vision/commits/main' -Headers @{'User-Agent'='Vision-Document-Setup'} -TimeoutSec 30).sha
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Could not resolve the Vision source revision.' }
$Setup=Join-Path $env:TEMP ('Setup-Vision-Documents-'+[Guid]::NewGuid().ToString('N')+'.ps1')
try {
    Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/JRDN-R/vision/$Revision/vision-pc/Setup-Vision-PC.ps1" -OutFile $Setup
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Setup -Action InstallDocumentTools -DocumentProfile $Profile -SourceRef $Revision
    if ($LASTEXITCODE -ne 0) { throw 'Document setup did not finish. Read the error above; the previous processor is retained.' }
} finally { Remove-Item -LiteralPath $Setup -ErrorAction SilentlyContinue }
