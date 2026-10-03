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
