# Dot-sourced by Setup-Vision-PC.ps1. Uses its private paths and transactional activation.
function Get-DocumentDownload([string]$Url,[string]$Path,[string]$Hash,[string]$Algorithm='SHA256') {
    if ((Test-Path -LiteralPath $Path) -and (Get-FileHash -LiteralPath $Path -Algorithm $Algorithm).Hash -eq $Hash) { return }
    Get-Download $Url $Path
    if ((Get-FileHash -LiteralPath $Path -Algorithm $Algorithm).Hash -ne $Hash) { Remove-Item -LiteralPath $Path -Force; throw 'Document tool download failed its checksum.' }
}
function Install-DocumentTesseract([string]$TesseractRoot) {
    New-Item -ItemType Directory -Path $TesseractRoot -Force | Out-Null
    Assert-PrivateSoundPath $TesseractRoot
    $Tesseract=Join-Path $TesseractRoot 'tesseract.exe'
    # The NSIS MultiUser initializer can replace /D with its registered/default
    # directory. Recover an existing machine installation without uninstalling it.
    $MachineRoots=@(@($env:ProgramW6432,$env:ProgramFiles) | Where-Object { $_ } | ForEach-Object { Join-Path $_ 'Tesseract-OCR' } | Select-Object -Unique)
    $Source=$MachineRoots | Where-Object { Test-Path -LiteralPath (Join-Path $_ 'tesseract.exe') } | Select-Object -First 1
    $ExitCode=$null
    $Launched=$false
    if (-not (Test-Path -LiteralPath $Tesseract) -and -not $Source) {
        # NSIS silently offers to uninstall registered copies. Do not run it
        # over an unrelated/custom installation that was not found above.
        $Registered=@('HKLM:\SOFTWARE\Tesseract-OCR','HKLM:\SOFTWARE\WOW6432Node\Tesseract-OCR','HKCU:\SOFTWARE\Tesseract-OCR','HKCU:\SOFTWARE\WOW6432Node\Tesseract-OCR' | ForEach-Object { Get-ItemProperty -LiteralPath $_ -ErrorAction SilentlyContinue } | Where-Object { $_.CurrentVersion })
        if ($Registered.Count) { throw 'Tesseract has an existing custom or incomplete Windows installation. Its files were not changed. Review its registered installation folder before retrying.' }
        $OcrInstaller=Join-Path $DownloadDir 'tesseract-5.5.0.exe'
        Get-DocumentDownload 'https://github.com/tesseract-ocr/tesseract/releases/download/5.5.0/tesseract-ocr-w64-setup-5.5.0.20241111.exe' $OcrInstaller 'F3FC4236425B690C8BE756F35793F77394EE004BE0A6460A440C754D892F68BC'
        # NSIS requires /D last and unquoted, even when the path contains spaces.
        $Ocr=Start-Process -FilePath $OcrInstaller -ArgumentList ('/S /AllUsers /D='+$TesseractRoot) -PassThru
        $Launched=$true
        # Retain the process handle before waiting: Windows PowerShell 5.1 can
        # otherwise return a null ExitCode for a short-lived installer.
        $Handle=$Ocr.Handle
        $Ocr.WaitForExit()
        $Ocr.Refresh()
        $ExitCode=$Ocr.ExitCode
        $Source=$MachineRoots | Where-Object { Test-Path -LiteralPath (Join-Path $_ 'tesseract.exe') } | Select-Object -First 1
    }
    if (-not (Test-Path -LiteralPath $Tesseract) -and $Source) {
        Write-Host 'Found Tesseract in its Windows installation folder; preparing a private Vision copy.'
        if ((Get-Item -LiteralPath $Source -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'The Tesseract source folder is a link.' }
        # Keep notices/configuration, but do not copy unrelated installed models.
        $Files=@(Get-ChildItem -LiteralPath $Source -File | Where-Object { $_.Name -eq 'tesseract.exe' -or $_.Extension -eq '.dll' -or $_.Name -match '^(LICENSE|COPYING|AUTHORS|README)' })
        foreach ($File in $Files) {
            if ($File.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'The Tesseract source contains a linked file.' }
            Copy-Item -LiteralPath $File.FullName -Destination $TesseractRoot -Force
        }
        foreach ($Name in @('doc','tessdata')) {
            $Folder=Join-Path $Source $Name
            if (-not (Test-Path -LiteralPath $Folder)) { continue }
            $Entries=@(Get-Item -LiteralPath $Folder -Force)+@(Get-ChildItem -LiteralPath $Folder -Recurse -Force)
            if (@($Entries | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) { throw 'The Tesseract source contains a linked folder or file.' }
            foreach ($File in @($Entries | Where-Object { -not $_.PSIsContainer -and $_.Extension -ne '.traineddata' })) {
                $Relative=$File.FullName.Substring($Source.Length).TrimStart('\')
                $Target=Join-Path $TesseractRoot $Relative
                New-Item -ItemType Directory -Path (Split-Path -Parent $Target) -Force | Out-Null
                Copy-Item -LiteralPath $File.FullName -Destination $Target -Force
            }
        }
    }
    $LogBase=Join-Path $InstallRoot 'data\document-ocr'
    Write-Utf8 ($LogBase+'-install.json') (@{installerLaunched=$Launched;exitCode=$ExitCode;machineSource=$Source;expectedExecutable=$Tesseract;executableFound=(Test-Path -LiteralPath $Tesseract)} | ConvertTo-Json)
    if (-not (Test-Path -LiteralPath $Tesseract)) {
        throw "Tesseract executable was not found after setup (exit code: $ExitCode). Checked $TesseractRoot and $($MachineRoots -join ', '). Details: $LogBase-install.json."
    }
    # The upstream installer downloads language data separately and may silently
    # omit it. Fetch a small, checksum-pinned English model directly over HTTPS.
    $Data=Join-Path $TesseractRoot 'tessdata'
    New-Item -ItemType Directory -Path $Data -Force | Out-Null
    Assert-PrivateSoundPath $Data
    Get-DocumentDownload 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/eng.traineddata' (Join-Path $Data 'eng.traineddata') '7D4322BD2A7749724879683FC3912CB542F19906C83BCC1A52132556427170B2'
    $Probe=Start-Process -FilePath $Tesseract -ArgumentList '--version' -Wait -PassThru -NoNewWindow -RedirectStandardOutput ($LogBase+'-version.log') -RedirectStandardError ($LogBase+'-errors.log')
    $Version=(Get-Content -LiteralPath ($LogBase+'-version.log') -Raw)+(Get-Content -LiteralPath ($LogBase+'-errors.log') -Raw)
    if (($null -ne $Probe.ExitCode -and $Probe.ExitCode -ne 0) -or $Version -notmatch '(?m)^tesseract\s+v?5\.') {
        throw "Tesseract did not pass its executable check. See $LogBase-version.log and $LogBase-errors.log."
    }
    return $Tesseract
}
function Install-DocumentTools([string]$InstallerPath) {
    Assert-InstalledProcessor
    $Configuration=Read-Configuration
    $InstallId=[Guid]::NewGuid().ToString('N')
    $StageDirectory=Join-Path $DownloadDir ('document-install-'+$InstallId)
    New-Item -ItemType Directory -Path $StageDirectory | Out-Null
    Assert-PrivateSoundPath $StageDirectory
    $SourceBase="https://raw.githubusercontent.com/JRDN-R/vision/$SourceRef/vision-pc"
    $RequiredFree=if ($DocumentProfile -eq 'Full') { 12GB } else { 4GB }
    $Disk=Get-PSDrive -Name ([IO.Path]::GetPathRoot($InstallRoot).Substring(0,1))
    if ($Disk.Free -lt $RequiredFree) { throw ('Document installation needs at least '+[math]::Round($RequiredFree/1GB)+' GB of free space for downloads and working files.') }
    Write-Stage '1/6 Staging the document processor'
    foreach ($File in $ProcessorFiles) { Get-Download "$SourceBase/$File" (Join-Path $StageDirectory $File) }
    Invoke-Checked $PythonExe (@('-m','py_compile')+@($ProcessorFiles|Where-Object {$_.EndsWith('.py')}|ForEach-Object {Join-Path $StageDirectory $_}))
    $RequirementText=[IO.File]::ReadAllText((Join-Path $StageDirectory 'requirements-documents.txt'))+$DocumentProfile
    if ($DocumentProfile -eq 'Full') { $RequirementText += [IO.File]::ReadAllText((Join-Path $StageDirectory 'requirements-documents-full.txt')) }
    $Hasher=[Security.Cryptography.SHA256]::Create()
    try { $RequirementHash=([BitConverter]::ToString($Hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($RequirementText)))).Replace('-','') } finally { $Hasher.Dispose() }
    $PluginRoot=Join-Path $InstallRoot 'plugins'
    New-Item -ItemType Directory -Path $PluginRoot -Force | Out-Null
    Assert-PrivateSoundPath $PluginRoot
    $DocumentRuntime=Join-Path $PluginRoot ('documents-'+$RequirementHash.Substring(0,16))
    $DocumentPython=Join-Path $DocumentRuntime 'python.exe'
    $Marker=Join-Path $DocumentRuntime 'vision-document-runtime.json'
    $Reuse=Test-Path -LiteralPath $Marker
    Write-Stage '2/6 Installing a separate document runtime'
    if (-not $Reuse) {
        New-Item -ItemType Directory -Path $DocumentRuntime -Force | Out-Null
        Assert-PrivateSoundPath $DocumentRuntime
        $Archive=Join-Path $DownloadDir 'python-3.11.9-documents.zip'
        Get-DocumentDownload 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip' $Archive '009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b'
        Expand-Archive -LiteralPath $Archive -DestinationPath $DocumentRuntime -Force
        $Signature=Get-AuthenticodeSignature -FilePath $DocumentPython
        if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw 'Document Python signature could not be verified.' }
        Write-Utf8 (Join-Path $DocumentRuntime 'python311._pth') "python311.zip`r`n.`r`nLib\site-packages`r`n$InstallRoot`r`nimport site`r`n"
        $Pip=@('-m','pip','--isolated','--python',$DocumentPython,'install','--disable-pip-version-check','--no-warn-script-location','--no-input','--only-binary=:all:','--no-cache-dir')
        Invoke-SoundLogged $PythonExe ($Pip+@('--index-url','https://pypi.org/simple','-r',(Join-Path $StageDirectory 'requirements-documents.txt'))) (Join-Path $InstallRoot 'data\document-packages')
        if ($DocumentProfile -eq 'Full') {
            Invoke-SoundLogged $PythonExe ($Pip+@('--index-url','https://download.pytorch.org/whl/cpu','torch==2.8.0+cpu','torchvision==0.23.0+cpu')) (Join-Path $InstallRoot 'data\document-cpu')
            Invoke-SoundLogged $PythonExe ($Pip+@('--index-url','https://pypi.org/simple','-r',(Join-Path $StageDirectory 'requirements-documents-full.txt'))) (Join-Path $InstallRoot 'data\document-docling')
        }
    }
    Write-Stage '3/6 Installing OCR and legacy-document tools'
    $TesseractRoot=Join-Path $ToolsDir 'tesseract'
    $Tesseract=Install-DocumentTesseract $TesseractRoot
    $JavaRoot=Join-Path $ToolsDir 'document-java-17.0.20.1'
    $Java=Get-ChildItem -LiteralPath $JavaRoot -Filter java.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
    if (-not $Java) {
        $JavaZip=Join-Path $DownloadDir 'document-java-17.0.20.1.zip'
        $JavaBase='https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.20.1%2B1/OpenJDK17U-jre_x64_windows_hotspot_17.0.20.1_1.zip'
        $CheckFile=Join-Path $StageDirectory 'java.sha256'
        Get-Download ($JavaBase+'.sha256.txt') $CheckFile
        $JavaHash=([IO.File]::ReadAllText($CheckFile) -split '\s+')[0]
        if ($JavaHash -notmatch '^[a-fA-F0-9]{64}$') { throw 'Java checksum could not be read.' }
        Get-DocumentDownload $JavaBase $JavaZip $JavaHash
        Expand-Archive -LiteralPath $JavaZip -DestinationPath $JavaRoot -Force
        $Java=Get-ChildItem -LiteralPath $JavaRoot -Filter java.exe -Recurse | Select-Object -First 1 -ExpandProperty FullName
    }
    Assert-PrivateSoundPath $JavaRoot
    $Tika=Join-Path $ToolsDir 'tika-app-3.3.2.jar'
    Get-DocumentDownload 'https://downloads.apache.org/tika/3.3.2/tika-app-3.3.2.jar' $Tika '88c2032cba0d45feea361e6eebd2918bd04707614cdda5d89a1b167da5503c98e7b4cd368336f0402d559abcaf5006fcc7c825c32c749ae0417ea2f3b8423aba' 'SHA512'
    $Assets=Join-Path $InstallRoot 'models\document-layout-2.133.0'
    New-Item -ItemType Directory -Path $Assets -Force | Out-Null
    Assert-PrivateSoundPath $Assets
    $Options=[ordered]@{profile=$DocumentProfile;tesseractPath=$Tesseract;javaPath=$Java;tikaPath=$Tika;artifactsPath=$Assets}
    $OptionsPath=Join-Path $StageDirectory 'document-options.json'
    Write-Utf8 $OptionsPath ($Options|ConvertTo-Json)
    Write-Stage '4/6 Downloading selected models and checking actual extraction'
    Write-Host 'Standard has no large neural model. Full adds PDF layout/table models only. No chat, image-generation, or extra speech model is downloaded.'
    # Downloads happen only during install. Completed runtimes stay offline.
    $RuleName='Vision Documents Offline '+$RequirementHash.Substring(0,16)
    $ExistingRule=Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
    $CheckArgs=@((Join-Path $StageDirectory 'setup_documents.py'),'--worker',(Join-Path $StageDirectory 'document_worker.py'),'--options',$OptionsPath)
    if (-not $Reuse) { $CheckArgs += '--download' }
    Invoke-SoundLogged $DocumentPython $CheckArgs (Join-Path $InstallRoot 'data\document-check')
    if (-not $ExistingRule) { New-NetFirewallRule -DisplayName $RuleName -Direction Outbound -Program $DocumentPython -Action Block -Profile Any | Out-Null }
    $JavaRule='Vision Document Java Offline'
    if (-not (Get-NetFirewallRule -DisplayName $JavaRule -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName $JavaRule -Direction Outbound -Program $Java -Action Block -Profile Any | Out-Null }
    # Repeat with egress blocked so installation verifies the production path.
    Invoke-SoundLogged $DocumentPython @((Join-Path $StageDirectory 'setup_documents.py'),'--worker',(Join-Path $StageDirectory 'document_worker.py'),'--options',$OptionsPath) (Join-Path $InstallRoot 'data\document-offline-check')
    Write-Utf8 $Marker (@{profile=$DocumentProfile;requirementsSha256=$RequirementHash}|ConvertTo-Json)
    Write-Stage '5/6 Activating background document processing'
    $Configuration=Read-Configuration
    $Configuration|Add-Member -NotePropertyName documentProcessing -NotePropertyValue ([pscustomobject]@{
        enabled=$true;profile=$DocumentProfile;pythonPath=$DocumentPython;tesseractPath=$Tesseract;javaPath=$Java;tikaPath=$Tika;artifactsPath=$Assets;
        tools=@('MarkItDown','Tesseract','Apache Tika','pdfplumber','PDFium','python-docx','python-pptx','openpyxl')+$(if($DocumentProfile -eq 'Full'){@('Docling')}else{@()})
    }) -Force
    Set-ProcessorUpdate $Configuration $StageDirectory ($Configuration.firebaseAuth.enabled -eq $true) $false $true
    if ([IO.Path]::GetFullPath($InstallerPath) -ne (Join-Path $InstallRoot 'Setup-Vision-PC.ps1')) { Copy-Item -LiteralPath $InstallerPath -Destination (Join-Path $InstallRoot 'Setup-Vision-PC.ps1') -Force }
    Write-Stage '6/6 Measuring installed storage'
    $Paths=@($DocumentRuntime,$TesseractRoot,$JavaRoot,$Tika,$Assets)
    $Bytes=0L
    foreach($Path in $Paths){$Bytes += [long]((Get-ChildItem -LiteralPath $Path -Recurse -File -ErrorAction SilentlyContinue|Measure-Object Length -Sum).Sum)}
    $Report=[ordered]@{profile=$DocumentProfile;installedBytes=$Bytes;installedGiB=[math]::Round($Bytes/1GB,2);sourceRevision=$SourceRef;documentCacheLimitGiB=4;maxUploadMiB=50;measuredAt=[DateTime]::UtcNow.ToString('o');paths=$Paths}
    Write-Utf8 (Join-Path $InstallRoot 'data\document-storage.json') ($Report|ConvertTo-Json -Depth 4)
    foreach($Name in @('python-3.11.9-documents.zip','tesseract-5.5.0.exe','document-java-17.0.20.1.zip')){Remove-Item -LiteralPath (Join-Path $DownloadDir $Name) -Force -ErrorAction SilentlyContinue}
    Remove-Item -LiteralPath $StageDirectory -Recurse -Force
    Write-Host ("`nDocument tools are ready. Installed size: "+$Report.installedGiB+' GiB, plus project files and a document cache capped at 4 GiB.') -ForegroundColor Green
    Write-Host 'Refresh Vision. Added files prepare automatically while you work. The existing Windows task handles startup; this PowerShell window can close.'
    Write-Host 'The PC must remain awake and online. Existing Whisper/video tools and saved projects were retained.'
}
