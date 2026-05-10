@echo off
cd /d "%~dp0"
echo ================================
echo QC Marketing Bot - Install Deps
echo ================================
echo.
python --version
if errorlevel 1 (
    echo.
    echo ERROR: Python tidak ditemukan. Install Python 3.10+ dulu dari https://python.org
    pause
    exit /b 1
)
echo.
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo ================================
echo SELESAI. Tutup window atau press any key.
echo ================================
pause
