# Exercise the actual Windows installer, directory recovery, English-data repair,
# and a real OCR run under Windows PowerShell 5.1, including paths with spaces.
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$InstallRoot=Join-Path $env:TEMP ('Vision Document Test '+[Guid]::NewGuid().ToString('N'))
$DownloadDir=Join-Path $InstallRoot 'downloads'
foreach ($Folder in @($InstallRoot,$DownloadDir,(Join-Path $InstallRoot 'data'))) { New-Item -ItemType Directory -Path $Folder -Force | Out-Null }
function Write-Utf8([string]$Path,[string]$Content) { [IO.File]::WriteAllText($Path,$Content,(New-Object Text.UTF8Encoding($false))) }
function Get-Download([string]$Url,[string]$Path) { Invoke-WebRequest -UseBasicParsing $Url -OutFile $Path -TimeoutSec 180 }
function Assert-PrivateSoundPath([string]$Path) {
    if (-not $Path.StartsWith($InstallRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Test path escaped its root.' }
}
. (Join-Path $PSScriptRoot '..\vision-pc\Document-Tools.ps1')
$OcrRoot=Join-Path $InstallRoot 'tools\tesseract'
$Ocr=Install-DocumentTesseract $OcrRoot
if ($Ocr -ne (Join-Path $OcrRoot 'tesseract.exe')) { throw 'OCR executable is not private.' }
# A completed first install must be reusable, and missing language data repaired.
Remove-Item -LiteralPath (Join-Path $OcrRoot 'tessdata\eng.traineddata')
$Again=Install-DocumentTesseract $OcrRoot
$Diagnostic=Get-Content -LiteralPath (Join-Path $InstallRoot 'data\document-ocr-install.json') -Raw | ConvertFrom-Json
if ($Diagnostic.installerLaunched -or $Again -ne $Ocr) { throw 'Retry reinstalled Tesseract instead of reusing it.' }
Add-Type -AssemblyName System.Drawing
$Bitmap=New-Object Drawing.Bitmap(800,140)
$Graphics=[Drawing.Graphics]::FromImage($Bitmap)
$Font=New-Object Drawing.Font('Arial',32)
try {
    $Graphics.Clear([Drawing.Color]::White)
    $Graphics.DrawString('Vision document test 42',$Font,[Drawing.Brushes]::Black,20,30)
    $Picture=Join-Path $InstallRoot 'test image.png'
    $Bitmap.Save($Picture,[Drawing.Imaging.ImageFormat]::Png)
} finally { $Font.Dispose();$Graphics.Dispose();$Bitmap.Dispose() }
$Output=Join-Path $InstallRoot 'result'
$Arguments='"'+$Picture+'" "'+$Output+'" --tessdata-dir "'+(Join-Path $OcrRoot 'tessdata')+'" -l eng --psm 6'
$Process=Start-Process $Ocr -ArgumentList $Arguments -Wait -PassThru -NoNewWindow
if ($null -ne $Process.ExitCode -and $Process.ExitCode -ne 0) { throw 'OCR process failed.' }
$Text=Get-Content -LiteralPath ($Output+'.txt') -Raw
if ($Text -notmatch 'Vision document test 42') { throw "OCR did not recover the expected text: $Text" }
Write-Host 'PASS: Windows installer, private copy, repeat setup, English-model repair, and actual OCR.'

# Match production's private Python, packages, and worker subprocess, not only
# the Tesseract CLI. This catches Windows-specific pytesseract argument bugs.
$DocumentRuntime=Join-Path $InstallRoot 'document runtime'
$Archive=Join-Path $DownloadDir 'python.zip'
Get-DocumentDownload 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip' $Archive '009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b'
Expand-Archive -LiteralPath $Archive -DestinationPath $DocumentRuntime -Force
Write-Utf8 (Join-Path $DocumentRuntime 'python311._pth') "python311.zip`r`n.`r`nLib\site-packages`r`nimport site`r`n"
$DocumentPython=Join-Path $DocumentRuntime 'python.exe'
& python -m pip --isolated --python $DocumentPython install --no-input --disable-pip-version-check --only-binary=:all: --index-url https://pypi.org/simple -r (Join-Path $PSScriptRoot '..\vision-pc\requirements-documents.txt')
if ($LASTEXITCODE -ne 0) { throw 'Document packages did not install.' }
$Tika=Join-Path $InstallRoot 'tika-app-3.3.2.jar'
Get-DocumentDownload 'https://downloads.apache.org/tika/3.3.2/tika-app-3.3.2.jar' $Tika '88c2032cba0d45feea361e6eebd2918bd04707614cdda5d89a1b167da5503c98e7b4cd368336f0402d559abcaf5006fcc7c825c32c749ae0417ea2f3b8423aba' 'SHA512'
$Java=Join-Path $env:JAVA_HOME_17_X64 'bin\java.exe'
$Options=Join-Path $InstallRoot 'options.json'
Write-Utf8 $Options (@{profile='Standard';tesseractPath=$Ocr;javaPath=$Java;tikaPath=$Tika;artifactsPath=$InstallRoot}|ConvertTo-Json)
# An unrelated machine-wide setting must not break Vision's private OCR copy.
$env:TESSDATA_PREFIX=Join-Path $InstallRoot 'deliberately missing language folder'
$Worker=Join-Path $PSScriptRoot '..\vision-pc\document_worker.py'
& $DocumentPython (Join-Path $PSScriptRoot '..\vision-pc\setup_documents.py') --worker $Worker --options $Options
if ($LASTEXITCODE -ne 0) { throw 'The production Standard extraction self-test failed.' }
Write-Host 'PASS: embedded Python 3.11.9, production OCR/PDF/HTML worker, Office imports, and Java/Tika, with spaces in paths and a conflicting TESSDATA_PREFIX.'
