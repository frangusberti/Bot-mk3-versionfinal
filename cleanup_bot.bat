@echo off
echo ========================================================
echo Bot Mk3 Cleanup Script
echo ========================================================
echo.
echo Stopping Rust Backend Services...
taskkill /F /IM bot-server.exe /T 2>nul
taskkill /F /IM bot-data.exe /T 2>nul
taskkill /F /IM bot-core.exe /T 2>nul

echo.
echo Stopping Python Services (GUI, Policy Server, ML)...
taskkill /F /IM python.exe /T 2>nul
taskkill /F /IM pythonw.exe /T 2>nul

echo.
echo Cleaning up Rust Build Cache (Optional, frees up disk but slows next build)...
:: Uncomment the line below if you also want to clear the target folder
:: cargo clean --manifest-path "c:\Bot mk3\Cargo.toml"

echo.
echo Cleanup complete.
pause
