@echo off
chcp 65001 > nul
echo ================================
echo  Water Meter Simulator Install
echo ================================
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found
    echo Please install Python 3.x from https://www.python.org
    pause
    exit /b 1
)
echo Python OK
python -m pip install --upgrade pip
pip install pyserial
echo.
echo Install complete! Run: run.bat
pause
