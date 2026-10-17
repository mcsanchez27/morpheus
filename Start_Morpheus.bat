@echo off
title Morpheus - Autonomous Sparring Partner (Mountain Man Studios)
color 0A
cd /d "%~dp0"
echo ======================================================================
echo    MORPHEUS - Autonomous Discord Sparring Partner & Testing Agent
echo    Mountain Man Studios (Server ID: 1560796428161388595)
echo ======================================================================
echo.
echo Starting Morpheus bot gateway...
"M:\Projects\Matt\Fight Bot\.venv\Scripts\python.exe" morpheus.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Morpheus stopped with an exit code.
    pause
)
