# Run the production configuration function without downloads or service changes.
$ErrorActionPreference = 'Stop'
$Tokens = $null
$Errors = $null
$Script = Join-Path $PSScriptRoot '../vision-pc/Local-AI.ps1'
$Ast = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $Script), [ref]$Tokens, [ref]$Errors)
if ($Errors.Count) { throw ($Errors | Out-String) }
$Function = $Ast.Find({ param($Node) $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $Node.Name -eq 'Set-LocalModelConfiguration' }, $false)
if (-not $Function) { throw 'Missing production configuration function.' }
. ([scriptblock]::Create($Function.Extent.Text))

$Config = [PSCustomObject]@{dataDir='preserved-data'; localAI=[PSCustomObject]@{enabled=$false;modelPath='old-27B.gguf';runtimePath='old-runtime';manifestPath='old-manifest';port=8117;defaultEpoch='old';gpuLayers=32;contextTokens=16384;maxOutputTokens=8192;threads=12;reserveRamGiB=12}}
Set-LocalModelConfiguration $Config 'new-runtime' 'new-9B.gguf' 'new-manifest'
if ($Config.localAI.modelPath -ne 'new-9B.gguf' -or $Config.localAI.runtimePath -ne 'new-runtime' -or $Config.localAI.manifestPath -ne 'new-manifest') { throw 'Replacement retained stale artifact paths.' }
if ($Config.localAI.enabled -or $Config.localAI.defaultEpoch -or $Config.localAI.gpuLayers -ne 0 -or $Config.localAI.contextTokens -ne 4096 -or $Config.localAI.maxOutputTokens -ne 2048 -or $Config.localAI.threads -ne 8) { throw 'Replacement reused the previous model readiness/profile.' }
if ($Config.localAI.reserveRamGiB -ne 12) { throw 'Replacement lowered the existing memory reserve.' }
if ($Config.dataDir -ne 'preserved-data' -or $Config.localAI.port -ne 8117) { throw 'Unrelated configuration was changed.' }
$Before = $Config | ConvertTo-Json -Depth 10
Set-LocalModelConfiguration $Config 'new-runtime' 'new-9B.gguf' 'new-manifest'
if (($Config | ConvertTo-Json -Depth 10) -ne $Before) { throw 'Repeating Download changed the disabled profile.' }

$Config.localAI.enabled = $true
$Blocked = $false
try { Set-LocalModelConfiguration $Config 'other-runtime' 'other-model' 'other-manifest' } catch { $Blocked = $true }
if (-not $Blocked -or $Config.localAI.modelPath -ne 'new-9B.gguf') { throw 'Active model replacement was not blocked.' }

$Fresh = [PSCustomObject]@{dataDir='existing-data'}
Set-LocalModelConfiguration $Fresh 'runtime' 'model' 'manifest'
if ($Fresh.localAI.enabled -or $Fresh.localAI.port -ne 8101 -or $Fresh.dataDir -ne 'existing-data') { throw 'Fresh optional installation failed.' }
Write-Host 'Local model installation configuration tests passed.'

# Exercise the production atomic writer under Windows PowerShell 5.1. In
# particular, replacement must pass .NET a null backup name, not an empty one.
$Save = $Ast.Find({ param($Node) $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $Node.Name -eq 'Save-Json' }, $false)
if (-not $Save) { throw 'Missing production JSON writer.' }
. ([scriptblock]::Create($Save.Extent.Text))
$Utf8 = New-Object Text.UTF8Encoding($false)
$TestRoot = Join-Path ([IO.Path]::GetTempPath()) ('Qwen JSON with spaces ' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $TestRoot | Out-Null
try {
    $Target = Join-Path $TestRoot 'settings.json'
    foreach ($Stage in @(1,2,3)) {
        $Value = [PSCustomObject]@{stage=$Stage; nested=@{label=('caf' + [char]0x00e9); enabled=$false; items=@(1,2)}}
        Save-Json $Target $Value
        $Saved = [IO.File]::ReadAllText($Target,$Utf8) | ConvertFrom-Json
        if ($Saved.stage -ne $Stage -or $Saved.nested.label -cne $Value.nested.label -or $Saved.nested.enabled -ne $false -or $Saved.nested.items.Count -ne 2) { throw 'JSON create/replace lost data.' }
    }
    $Before = [IO.File]::ReadAllText($Target,$Utf8)
    $Handle = [IO.File]::Open($Target,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
    $Stopped = $false
    try { Save-Json $Target @{stage=99} } catch { $Stopped = $true } finally { $Handle.Dispose() }
    if (-not $Stopped -or [IO.File]::ReadAllText($Target,$Utf8) -cne $Before) { throw 'Failed replacement did not preserve the original and stop.' }
    if (@(Get-ChildItem -LiteralPath $TestRoot -Filter '*.tmp').Count) { throw 'JSON writer left staging files.' }
    Write-Host 'Production JSON writer passed create, repeat replacement, Unicode, nested data and locked-file failure checks.'
} finally { Remove-Item -LiteralPath $TestRoot -Recurse -Force }
