@echo off
cd /d "%~dp0"
title Metobot AI Quantitative Terminal - Launcher
color 0A

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py run.py
    if %ERRORLEVEL% NEQ 0 pause
    goto end
)

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    python run.py
    if %ERRORLEVEL% NEQ 0 pause
    goto end
)

color 0C
echo =========================================================
echo [HATA] Sistemde Python bulunamadi!
echo Lutfen python.org adresinden Python kurun.
echo =========================================================
pause

:end
