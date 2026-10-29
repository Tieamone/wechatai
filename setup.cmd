@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt --cache-dir tools\pip-cache
if errorlevel 1 exit /b 1
cd frontend
call npm.cmd ci --cache ..\tools\npm-cache
if errorlevel 1 exit /b 1
call npm.cmd run build
if errorlevel 1 exit /b 1
echo Setup complete. Run run.cmd.
pause
