@echo off
cd /d "%~dp0"
echo stop>data\collector\stop.request
echo Collector will stop after the current read finishes.
pause
