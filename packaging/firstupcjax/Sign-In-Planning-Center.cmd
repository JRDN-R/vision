@echo off
setlocal
set "FIRSTUPCJAX_LOGIN=%ProgramData%\VisionPC\FirstUPCJax-Admins.ps1"
if not exist "%FIRSTUPCJAX_LOGIN%" (
 echo Install FirstUPCJax Admins on FUPCJ Server first.
 pause
 exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process powershell.exe -Verb RunAs -ArgumentList ('-NoLogo -NoProfile -ExecutionPolicy Bypass -File '+[char]34+$env:FIRSTUPCJAX_LOGIN+[char]34+' -Action Login') } catch { Write-Host 'Sign-in did not start.'; exit 1 }"
