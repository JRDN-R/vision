@echo off
setlocal
if not exist "%~dp0installer\Install-FirstUPCJax-Admins.ps1" (
  echo Right-click the downloaded ZIP and choose Extract All.
  echo Open the extracted folder and run this installer there.
  pause
  exit /b 1
)
set "FIRSTUPCJAX_INSTALLER=%~dp0installer\Install-FirstUPCJax-Admins.ps1"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "try { $p=Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList ('-NoLogo -NoProfile -ExecutionPolicy Bypass -File '+[char]34+$env:FIRSTUPCJAX_INSTALLER+[char]34); exit $p.ExitCode } catch { Write-Host 'Installation did not start. Approve the administrator prompt to continue.'; exit 1 }"
set "FIRSTUPCJAX_RESULT=%ERRORLEVEL%"
if not "%FIRSTUPCJAX_RESULT%"=="0" pause
exit /b %FIRSTUPCJAX_RESULT%
