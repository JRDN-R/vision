#requires -Version 5.1
# Exercise the real updater guard with the argument supplied by the package.
# ExportConnection stops at an absent fixture config before touching services,
# credentials, networking, downloads, or the real ProgramData installation.
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Installer = Join-Path $Repo 'packaging\firstupcjax\installer\Install-FirstUPCJax-Admins.ps1'
$Updater = Join-Path $Repo 'vision-pc\Setup-Vision-PC.ps1'
$Tokens = $null; $ParseErrors = $null
$Ast = [System.Management.Automation.Language.Parser]::ParseFile($Installer,[ref]$Tokens,[ref]$ParseErrors)
if ($ParseErrors.Count) { throw 'The package installer must parse.' }
$Commands = @($Ast.FindAll({param($Node)
    $Node -is [System.Management.Automation.Language.CommandAst] -and
    @($Node.CommandElements | Where-Object {$_ -is [System.Management.Automation.Language.CommandParameterAst] -and $_.ParameterName -eq 'SourceRef'}).Count -eq 1
}, $true))
if ($Commands.Count -ne 1) { throw 'Expected one updater invocation in the package installer.' }
$Elements = $Commands[0].CommandElements
$Reference = $null
for ($Index=0; $Index -lt $Elements.Count; $Index++) {
    if ($Elements[$Index] -is [System.Management.Automation.Language.CommandParameterAst] -and $Elements[$Index].ParameterName -eq 'SourceRef') {
        $Value = $Elements[$Index+1]
        if ($Value -isnot [System.Management.Automation.Language.StringConstantExpressionAst]) { throw 'The release must pin a literal revision.' }
        $Reference = $Value.Value
    }
}
if ($Reference -notmatch '^[a-f0-9]{40}$') { throw 'Expected a pinned source commit.' }
$Fixture = Join-Path $env:TEMP ('FirstUPCJax Sound Room '+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Fixture | Out-Null
$Sentinel = Join-Path $Fixture 'existing-state-sentinel.txt'
[IO.File]::WriteAllText($Sentinel,'Preserve existing state')
$Before = (Get-FileHash -LiteralPath $Sentinel -Algorithm SHA256).Hash
function Invoke-RevisionCase([string]$SourceRef) {
    $Start = New-Object Diagnostics.ProcessStartInfo
    $Start.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $Start.Arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$Updater+'" -Action ExportConnection -SourceRef "'+$SourceRef+'" -OutputDirectory "'+$Fixture+'"'
    $Start.UseShellExecute=$false; $Start.CreateNoWindow=$true
    $Start.RedirectStandardOutput=$true; $Start.RedirectStandardError=$true
    $Start.EnvironmentVariables['ProgramData']=$Fixture
    $Process=[Diagnostics.Process]::Start($Start)
    $OutputTask=$Process.StandardOutput.ReadToEndAsync();$ErrorTask=$Process.StandardError.ReadToEndAsync()
    if (-not $Process.WaitForExit(30000)) { $Process.Kill();throw 'The isolated updater check timed out.' }
    $Output=$OutputTask.Result+$ErrorTask.Result
    $Code=$Process.ExitCode;$Process.Dispose()
    return [pscustomobject]@{Code=$Code;Output=$Output}
}
try {
    $Broken=Invoke-RevisionCase 'codex/firstupcjax-admins-worker'
    if ($Broken.Code -ne 1 -or $Broken.Output -notmatch 'Invalid source revision') { throw 'The original failure was not reproduced by the real updater.' }
    $Fixed=Invoke-RevisionCase $Reference
    if ($Fixed.Code -ne 1 -or $Fixed.Output -match 'Invalid source revision' -or $Fixed.Output -notmatch 'Run Setup first') {
        throw ('The packaged revision did not pass the real updater guard: '+$Fixed.Output)
    }
    if ((Get-FileHash -LiteralPath $Sentinel -Algorithm SHA256).Hash -ne $Before -or @(Get-ChildItem -LiteralPath $Fixture -Force).Count -ne 1) {
        throw 'The read-only regression check unexpectedly changed fixture state.'
    }
    Write-Host 'PASS: original revision error reproduced; corrected package argument accepted; fixture state unchanged.'
} finally {
    Remove-Item -LiteralPath $Fixture -Recurse -Force
}
