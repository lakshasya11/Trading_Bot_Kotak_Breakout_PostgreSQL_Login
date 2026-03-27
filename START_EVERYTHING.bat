@echo off
title Trading Bot - Start Everything
color 0A

echo ============================================================================
echo           TRADING BOT - AUTOMATIC LOGIN SYSTEM
echo ============================================================================
echo.
echo Starting bot with auto-login enabled...
echo Kotak API will automatically:
echo   1. Generate TOTP code
echo   2. Validate MPIN
echo   3. Establish session
echo   4. Start trading
echo.
echo No manual login required!
echo.
echo ============================================================================
echo.

REM Start backend in its own window
echo [INFO] Starting backend server with Kotak auto-login...
if exist "backend\venv\Scripts\activate.bat" (
    start "Trading Bot - Backend" cmd /k "cd /d %~dp0backend && venv\Scripts\activate.bat && python main.py"
) else if exist ".venv\Scripts\activate.bat" (
    start "Trading Bot - Backend" cmd /k "cd /d %~dp0backend && ..\.venv\Scripts\activate.bat && python main.py"
) else (
    start "Trading Bot - Backend" cmd /k "cd /d %~dp0backend && python main.py"
)

REM Wait for backend to start and auto-login to complete
echo [INFO] Waiting for backend + Kotak login (5 seconds)...
timeout /t 5 /nobreak >nul

REM Start frontend in its own window
echo [INFO] Starting frontend server...
start "Trading Bot - Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

REM Wait for frontend to start
echo [INFO] Waiting for frontend to start (5 seconds)...
timeout /t 5 /nobreak >nul

REM Open browser
echo [INFO] Opening browser...
start http://localhost:5173

echo.
echo ============================================================================
echo           STARTUP COMPLETE!
echo ============================================================================
echo.
echo Backend:  http://localhost:8000  (Trading Engine + API)
echo Frontend: http://localhost:5173  (Web Interface)
echo.
echo [OK] Backend window opened  - Trading engine with auto-login
echo [OK] Frontend window opened - Web UI server
echo [OK] Browser opened         - http://localhost:5173
echo.
echo ============================================================================
echo.
echo TIPS:
echo - Browser will open automatically in 5 seconds
echo - Wait 5-10 seconds for everything to fully load
echo - Kotak auto-login takes 5-10 seconds
echo - Watch the backend window for login status
echo.
echo To stop everything:
echo - Close both terminal windows
echo - Or run STOP_EVERYTHING.bat
echo.
echo ============================================================================
echo.
echo You can close this window now. Both services are running.
echo.
pause
