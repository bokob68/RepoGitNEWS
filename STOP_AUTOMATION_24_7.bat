@echo off
cd /d "%~dp0"

if not exist "repo_sync_scheduler.pid" (
    echo No running automation PID file found.
    pause
    exit /b 0
)

set /p SCHED_PID=<repo_sync_scheduler.pid
if "%SCHED_PID%"=="" (
    echo PID file is empty.
    pause
    exit /b 1
)

taskkill /PID %SCHED_PID% /T /F
del /q "repo_sync_scheduler.pid" >nul 2>nul
echo Automation stopped.
pause
