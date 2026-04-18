@echo off
setlocal
cd /d "%~dp0"
python repo_sync_scheduler.py --config review_automation.ini
