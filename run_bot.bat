@echo off
echo ==========================================
echo       Starting ScalpBot Mk3 System
echo ==========================================

REM Delegate to the unified PowerShell script
powershell -ExecutionPolicy Bypass -File "%~dp0\run_system.ps1"

pause
