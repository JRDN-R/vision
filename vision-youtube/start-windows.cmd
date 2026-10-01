@echo off
setlocal
cd /d "%~dp0\.."
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto failed
)
if not exist ".venv\vision-helper-ready" (
  ".venv\Scripts\python.exe" -m pip install -r "vision-youtube\requirements.txt"
  if errorlevel 1 goto failed
  type nul > ".venv\vision-helper-ready"
)
".venv\Scripts\python.exe" "vision-youtube\server.py" --open %*
if errorlevel 1 goto failed
exit /b
:failed
echo.
echo Vision could not start. See vision-youtube\README.md for setup instructions.
echo Install Python 3.12 and Deno first, then run this file again.
pause
