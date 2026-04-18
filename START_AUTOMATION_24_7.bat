@echo off
cd /d "%~dp0"
title RepoGitNEWS 24/7 Automation
python repo_sync_scheduler.py --config review_automation.ini
