@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m tools.notification_demo --serverchan-test
pause
