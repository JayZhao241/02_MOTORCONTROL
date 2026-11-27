@echo off
cd /d "%~dp0.."
where py >nul 2>nul
if errorlevel 1 (
    python -m desktop.app
) else (
    py -3 -m desktop.app
)
if errorlevel 1 pause
