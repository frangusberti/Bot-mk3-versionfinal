@echo off
title BOT MK3
color 0B
echo.
echo  BOT MK3 - Sistema de Trading Algoritmico
echo  ==========================================
echo.
echo  Iniciando todos los componentes...
echo.

powershell -ExecutionPolicy Bypass -File "%~dp0run_system.ps1"

pause
