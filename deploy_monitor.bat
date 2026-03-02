@echo off
REM Enhanced Monitor Deployment Script
REM Runs the liquidation profit monitor with comprehensive visibility

echo ================================================================================
echo   ENHANCED MONITOR DEPLOYMENT
echo   Starting 5-Transaction Verification System
echo ================================================================================
echo.

REM Check Python
echo [*] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Please install Python 3.8+
    pause
    exit /b 1
)
echo [OK]  Python found
echo.

REM Check web3
echo [*] Checking web3.py installation...
python -c "from web3 import Web3" >nul 2>&1
if errorlevel 1 (
    echo [WARN] web3.py not installed. Installing...
    pip install web3
    if errorlevel 1 (
        echo [ERROR] Failed to install web3.py!
        pause
        exit /b 1
    )
)
echo [OK]  web3.py ready
echo.

REM Check script exists
echo [*] Verifying monitor script...
if not exist "%~dp0liquidation_engine\enhanced_monitor.py" (
    echo [ERROR] enhanced_monitor.py not found!
    pause
    exit /b 1
)
echo [OK]  Monitor script found
echo.

REM Kill any existing monitor processes
echo [*] Cleaning up existing monitor processes...
taskkill /F /FI "WINDOWTITLE eq ENHANCED MONITOR*" >nul 2>&1
timeout /t 2 /nobreak >nul
echo [OK]  Cleanup complete
echo.

REM Set console title
echo [OK]  Starting monitor in new window...
echo.
echo ================================================================================
echo   DEPLOYMENT COMPLETE
echo   Monitor is starting in a separate window
echo ================================================================================
echo.
echo Monitor Window Title: "ENHANCED MONITOR - 5 Profit Verification"
echo.
echo What to watch for:
echo   - Connection to Ethereum mainnet
echo   - Contract verification (V1, V2, Treasury)
echo   - Block scanning progress
echo   - Profit detection events
echo   - Mission completion (5/5 profits) OR timeout/errors
echo.
echo The monitor will:
echo   - Scan every 10 seconds for LiquidationExecuted events
echo   - Display real-time progress and statistics
echo   - Auto-terminate after 5 profits OR 72 hours OR 10 errors
echo.
echo Check the monitor window for live updates!
echo ================================================================================
echo.

REM Start monitor in new window with UTF-8 encoding
start "ENHANCED MONITOR - 5 Profit Verification" cmd /k "chcp 65001 >nul && cd /d %~dp0liquidation_engine && python enhanced_monitor.py"

echo [*] Monitor launched!
echo.
echo To check status:
echo   - Watch the monitor window for live output
echo   - Run: tasklist /FI "WINDOWTITLE eq ENHANCED MONITOR*"
echo.
echo To stop monitor:
echo   - Press Ctrl+C in the monitor window
echo   - Or close the window
echo.
pause
