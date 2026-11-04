@echo off
setlocal
cd /d "%~dp0"
python stage39_continuous_rehearsal.py %*
exit /b %errorlevel%
