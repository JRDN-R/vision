# Dot-sourced by the existing updater; never changes task identity or Tailscale.
function Invoke-ContextLogged([string[]]$Arguments,[string]$LogBase) {
    # PowerShell 5.1 can turn native progress written to stderr into terminating
    # errors. Use the existing installer's subprocess/log convention instead.
    $Quoted = @($Arguments | ForEach-Object { '"' + $_.Replace('"','\"') + '"' })
    $Process = Start-Process -FilePath $PythonExe -ArgumentList $Quoted -Wait -PassThru -NoNewWindow -RedirectStandardOutput "$LogBase.log" -RedirectStandardError "$LogBase-errors.log"
    if ($Process.ExitCode -ne 0) {
        Get-Content -LiteralPath "$LogBase.log" -Tail 8 | Out-Host
        Get-Content -LiteralPath "$LogBase-errors.log" -Tail 12 | Out-Host
        throw "Context installation check failed ($($Process.ExitCode)). Review $LogBase.log and $LogBase-errors.log. Existing configuration is retained unless an earlier activation completed."
    }
    Get-Content -LiteralPath "$LogBase.log" -Tail 24 | Out-Host
}
function New-ContextStage {
    $Stage = Join-Path $DownloadDir ('context-stage-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Stage | Out-Null
    Set-PrivateDirectory $Stage
    foreach ($File in $ProcessorFiles) {
        $Source = Join-Path $InstallRoot $File
        if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) { throw 'Run the reviewed application update before installing context tools.' }
        Copy-Item -LiteralPath $Source -Destination (Join-Path $Stage $File)
    }
    return $Stage
}
function Install-ContextEngine {
    $Configuration = Read-Configuration
    $StageDirectory = New-ContextStage
    $InstallId = [Guid]::NewGuid().ToString('N')
    $PackagesPath = Join-Path $InstallRoot ('plugins\context-' + $InstallId)
    $ModelPath = Join-Path $InstallRoot ('models\minilm-l6-v2-' + $InstallId)
    foreach ($Directory in @($PackagesPath,$ModelPath)) {
        New-Item -ItemType Directory -Path $Directory -Force | Out-Null
        Assert-PrivateSoundPath $Directory
        Set-PrivateDirectory $Directory
    }
    Write-Stage '1/4 Checking actual hardware and SQLite FTS5 support'
    Invoke-Checked $PythonExe @((Join-Path $StageDirectory 'setup_context.py'),'--report')
    Write-Stage '2/4 Installing private optional CPU embedding dependencies'
    Write-Host 'The existing processor stays online during downloads. No paid inference service is used.'
    # Explicit CPU wheel pins prevent installing a GPU runtime. Optional packages
    # live outside the web processor and transcription package directories.
    Invoke-ContextLogged @('-m','pip','--isolated','install','--index-url','https://pypi.org/simple','--extra-index-url','https://download.pytorch.org/whl/cpu','--disable-pip-version-check','--no-warn-script-location','--no-input','--only-binary=:all:','--no-cache-dir','--target',$PackagesPath,'-r',(Join-Path $StageDirectory 'requirements-context.txt')) (Join-Path $StageDirectory 'context-packages')
    $Resolved = & $PythonExe -m pip --isolated list --path $PackagesPath --format=json --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw 'Could not record the installed context dependency versions.' }
    Write-Utf8 (Join-Path $PackagesPath 'vision-context-packages.json') ($Resolved -join "`n")
    Write-Stage '3/4 Downloading the pinned 384-dimension MiniLM model and testing offline CPU inference'
    Invoke-ContextLogged @((Join-Path $StageDirectory 'setup_context.py'),'--download','--packages-path',$PackagesPath,'--model-path',$ModelPath,'--threads','2') (Join-Path $StageDirectory 'context-model')
    # Reload configuration after downloads; never replace intervening preferences.
    $Configuration = Read-Configuration
    if (-not $Configuration.intelligentContext) {
        $Configuration | Add-Member -NotePropertyName intelligentContext -NotePropertyValue ([pscustomobject]@{}) -Force
    }
    foreach ($Setting in @{'enabled'=$true;'embeddingModelPath'=$ModelPath;'embeddingPackagesPath'=$PackagesPath;'embeddingThreads'=2}.GetEnumerator()) {
        $Configuration.intelligentContext | Add-Member -NotePropertyName $Setting.Key -NotePropertyValue $Setting.Value -Force
    }
    if (-not $Configuration.intelligentContext.workers) { $Configuration.intelligentContext | Add-Member -NotePropertyName workers -NotePropertyValue 2 -Force }
    if (-not $Configuration.intelligentContext.debounceSeconds) { $Configuration.intelligentContext | Add-Member -NotePropertyName debounceSeconds -NotePropertyValue 2 -Force }
    Write-Stage '4/4 Backing up saved data and activating through the existing update transaction'
    Set-ProcessorUpdate $Configuration $StageDirectory $false $false $false $true
    Test-ContextEngine
    Write-Host 'Local semantic retrieval is installed. Original projects and conversations remain in their existing storage.' -ForegroundColor Green
}
function Test-ContextEngine {
    $Configuration = Read-Configuration
    $Script = Join-Path $InstallRoot 'setup_context.py'
    Invoke-Checked $PythonExe @($Script,'--self-test','--config',$ConfigPath)
    if ($Configuration.intelligentContext.embeddingModelPath) {
        Invoke-Checked $PythonExe @($Script,'--check-model','--packages-path',[string]$Configuration.intelligentContext.embeddingPackagesPath,'--model-path',[string]$Configuration.intelligentContext.embeddingModelPath,'--threads','2')
    }
    $Health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -Headers (Get-LocalDiagnosticHeaders $Configuration) -TimeoutSec 10
    if ($Configuration.intelligentContext.enabled -eq $false) {
        Write-Host 'Context engine is disabled. Its saved indexes and optional model are retained.'
    } elseif ($Health.capabilities.intelligentContextV1 -ne $true) {
        throw 'The running task does not advertise context engine readiness.'
    } else {
        Write-Host 'Context engine import, disposable indexing/retrieval, and authenticated processor health checks passed.' -ForegroundColor Green
        if (-not $Configuration.intelligentContext.embeddingModelPath) { Write-Host 'Semantic model is not installed. Exact, lexical and graph retrieval remain available; run InstallContextEngine for semantic retrieval.' }
    }
}
function Set-ContextEnabled([bool]$Enabled) {
    $Configuration = Read-Configuration
    if (-not $Configuration.intelligentContext) {
        $Configuration | Add-Member -NotePropertyName intelligentContext -NotePropertyValue ([pscustomobject]@{}) -Force
    }
    $Configuration.intelligentContext | Add-Member -NotePropertyName enabled -NotePropertyValue $Enabled -Force
    $StageDirectory = New-ContextStage
    Set-ProcessorUpdate $Configuration $StageDirectory $false $false $false $Enabled
    Write-Host 'Context setting updated. Original projects, conversations, indexes and downloaded models are retained.'
}
function Restore-ProcessorBackup([string]$Directory) {
    if (-not $Directory -or -not [IO.Path]::IsPathRooted($Directory)) { throw 'Supply the exact protected backup path printed by the update using -BackupDirectory.' }
    $Directory = [IO.Path]::GetFullPath($Directory).TrimEnd('\')
    $ExpectedParent = [IO.Path]::GetFullPath($DownloadDir).TrimEnd('\')
    if ([IO.Path]::GetDirectoryName($Directory) -ne $ExpectedParent -or [IO.Path]::GetFileName($Directory) -notmatch '^processor-update-backup-[0-9a-f]{32}$') {
        throw 'Rollback accepts only a retained processor-update-backup directory from this installation.'
    }
    Assert-PrivateSoundPath $Directory
    $ManifestPath = Join-Path $Directory 'processor-backup.json'
    Assert-PrivateSoundPath $ManifestPath
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if ($Manifest.schemaVersion -ne 1 -or $Manifest.complete -ne $true) { throw 'The processor backup is incomplete or unsupported.' }
    $Snapshot = Get-Content -LiteralPath (Join-Path $Directory 'snapshot\backup-manifest.json') -Raw | ConvertFrom-Json
    if ($Snapshot.complete -ne $true) { throw 'The original pre-update data snapshot is incomplete.' }
    $SavedConfig = Join-Path $Directory 'config.json'
    Assert-PrivateSoundPath $SavedConfig
    if ((Get-FileHash -LiteralPath $SavedConfig -Algorithm SHA256).Hash -ne $Manifest.configSha256) { throw 'Backup configuration checksum failed.' }
    $Entries = @($Manifest.files)
    $Seen = @{}
    foreach ($Entry in $Entries) {
        if ([string]$Entry.name -notin $ProcessorFiles -or $Seen.ContainsKey([string]$Entry.name)) { throw 'Backup contains unexpected or duplicate application files.' }
        $Seen[[string]$Entry.name] = $true
        if ($Entry.existed -eq $true) {
            $File = Join-Path $Directory $Entry.name
            Assert-PrivateSoundPath $File
            if ((Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash -ne $Entry.sha256) { throw 'Backup application checksum failed.' }
        }
    }
    if ($Entries.Count -ne $ProcessorFiles.Count) { throw 'This rollback requires the updater version that created the backup.' }
    $SavedConfiguration = Get-Content -LiteralPath $SavedConfig -Raw | ConvertFrom-Json
    if ([string]$SavedConfiguration.token -notmatch '^[A-Za-z0-9_-]{40,128}$' -or [int]$SavedConfiguration.port -ne 8765) { throw 'Backup server configuration is invalid.' }
    $Safety = Join-Path $DownloadDir ('rollback-safety-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Safety | Out-Null
    Set-PrivateDirectory $Safety
    Copy-Item -LiteralPath $ConfigPath -Destination (Join-Path $Safety 'config.json')
    $Current = @{}
    foreach ($Entry in $Entries) {
        $File = Join-Path $InstallRoot $Entry.name
        $Current[[string]$Entry.name] = Test-Path -LiteralPath $File
        if ($Current[[string]$Entry.name]) { Copy-Item -LiteralPath $File -Destination (Join-Path $Safety $Entry.name) }
    }
    $WasRunning = ((Get-ScheduledTask -TaskName $TaskName).State -eq 'Running')
    Stop-ProcessorForChange
    try {
        foreach ($Entry in $Entries) {
            $Destination = Join-Path $InstallRoot $Entry.name
            if ($Entry.existed -eq $true) { Copy-Item -LiteralPath (Join-Path $Directory $Entry.name) -Destination $Destination -Force }
            elseif (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Force }
        }
        Copy-Item -LiteralPath $SavedConfig -Destination $ConfigPath -Force
        Invoke-Checked $PythonExe @((Join-Path $InstallRoot 'server.py'),'--check')
        if ($WasRunning) {
            Start-ScheduledTask -TaskName $TaskName
            if (-not (Wait-Processor 'http://127.0.0.1:8765' $SavedConfiguration.token)) { throw 'Rolled-back application did not become healthy.' }
        }
    } catch {
        $Failure = $_.Exception.Message
        Stop-ProcessorForChange
        foreach ($Entry in $Entries) {
            $Destination = Join-Path $InstallRoot $Entry.name
            if ($Current[[string]$Entry.name]) { Copy-Item -LiteralPath (Join-Path $Safety $Entry.name) -Destination $Destination -Force }
            elseif (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Force }
        }
        Copy-Item -LiteralPath (Join-Path $Safety 'config.json') -Destination $ConfigPath -Force
        if ($WasRunning) { Start-ScheduledTask -TaskName $TaskName }
        throw "$Failure Current application restored. Safety copy: $Safety"
    }
    # Deliberately never restore data automatically: work saved after an update
    # must survive code rollback. Older code ignores the separate derived index.
    Write-Host 'Previous application/configuration restored. All current projects, conversations and source files were retained.' -ForegroundColor Green
    Write-Host "Pre-update data snapshot (manual disaster recovery only): $Directory\snapshot"
}
