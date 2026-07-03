@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo ========================================
echo   Jarvis Brain v0.4.0
echo ========================================
echo.
echo Starting server...
echo Web UI: http://localhost:8000/
echo.
python server.py
pause
