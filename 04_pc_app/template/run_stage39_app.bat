@echo off
setlocal
cd /d "%~dp0"
call conda run --no-capture-output -n nerf2 python software\stage39\verify_stage39_pc_package.py
if errorlevel 1 exit /b %errorlevel%
echo Stage39 App is starting at http://127.0.0.1:8310
echo Keep this window open. Press Ctrl+C to stop.
call conda run --no-capture-output -n nerf2 python pc_app\stage39_pc_server.py --port 8310
exit /b %errorlevel%
