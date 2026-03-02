@echo off
REM Monitor Status Dashboard
REM Quick status check for the enhanced monitor

echo ================================================================================
echo   ENHANCED MONITOR STATUS DASHBOARD
echo ================================================================================
echo.

REM Check monitor process
echo [*] Checking Monitor Process...
tasklist /FI "WINDOWTITLE eq ENHANCED MONITOR*" >nul 2>&1
if errorlevel 1 (
    echo [STATUS] Monitor: NOT RUNNING
) else (
    echo [STATUS] Monitor: RUNNING
    echo.
    echo Process Details:
    tasklist /FI "WINDOWTITLE eq ENHANCED MONITOR*" /FO LIST
)
echo.

REM Check Python processes
echo [*] Python Processes:
tasklist | findstr python.exe
if errorlevel 1 (
    echo No Python processes found
)
echo.

REM Check treasury balance
echo [*] Treasury Balance Check...
python -c "from web3 import Web3; w3 = Web3(Web3.HTTPProvider('https://eth-mainnet.g.alchemy.com/v2/Rc0sle99H0nN5Sm3C3JyfeAmqYz4hmlu')); print(f'Balance: {w3.eth.get_balance(\"0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4\") / 1e18:.6f} ETH')"
echo.

REM Check current block
echo [*] Current Block:
python -c "from web3 import Web3; w3 = Web3(Web3.HTTPProvider('https://eth-mainnet.g.alchemy.com/v2/Rc0sle99H0nN5Sm3C3JyfeAmqYz4hmlu')); print(f'Block: {w3.eth.block_number}')"
echo.

echo ================================================================================
echo   STATUS CHECK COMPLETE
echo ================================================================================
echo.
echo Monitor Window: "ENHANCED MONITOR - 5 Profit Verification"
echo Watch that window for real-time progress updates
echo.
pause
