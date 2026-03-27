@echo off
title Trading Bot - Stop Everything
color 0C

echo ============================================================================
echo           TRADING BOT - STOPPING ALL SERVICES
echo ============================================================================
echo.

echo [1/2] Stopping backend (Python)...
taskkill /f /im python.exe 2>nul
if %errorlevel% == 0 (
    echo       [OK] Backend stopped.
) else (
    echo       [--] No backend process found.
)

echo [2/2] Stopping frontend (Node)...
taskkill /f /im node.exe 2>nul
if %errorlevel% == 0 (
    echo       [OK] Frontend stopped.
) else (
    echo       [--] No frontend process found.
)

echo.
echo ============================================================================
echo       [DONE] Everything stopped successfully.
echo ============================================================================
echo.
pause
