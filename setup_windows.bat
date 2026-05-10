@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  QC Marketing Bot - Setup Windows
echo ============================================================
echo.

REM === Check Python ===
echo [1/4] Check Python installation...
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python tidak ditemukan di PATH.
    echo.
    echo Install Python 3.10+ dari: https://www.python.org/downloads/
    echo Pastikan centang "Add Python to PATH" saat install.
    echo.
    pause
    exit /b 1
)
python --version
echo.

REM === Check git ===
echo [2/4] Check Git installation...
where git >nul 2>&1
if errorlevel 1 (
    echo [WARN] Git tidak ditemukan. Update via update.bat akan gagal.
    echo Install Git dari: https://git-scm.com/download/win
    echo.
    REM Tetap lanjut — git hanya untuk update, bukan untuk run bot
) else (
    git --version
)
echo.

REM === Install Python dependencies ===
echo [3/4] Install Python dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Gagal install dependencies.
    pause
    exit /b 1
)
echo.

REM === Install ffmpeg ===
echo [4/4] Install ffmpeg portable (untuk video review)...
if exist "tools\ffmpeg\bin\ffmpeg.exe" (
    echo        ffmpeg sudah ter-install di tools\ffmpeg\
) else (
    echo        Akan download ffmpeg ~85MB. Run install_ffmpeg.bat manual
    echo        atau press any key untuk skip ^(bisa install nanti^).
    pause
    REM Trigger installer (non-blocking — user bisa cancel)
    call install_ffmpeg.bat
)
echo.

echo ============================================================
echo  SETUP SELESAI.
echo ============================================================
echo.
echo NEXT STEPS (manual, dilakukan sekali):
echo.
echo   1. Buat 4 file kredensial dengan copy dari template .example:
echo      - "API Claude.txt"  ^(Anthropic API key^)
echo      - "API Gemini.txt"  ^(Google AI key^)
echo      - "Trello.txt"      ^(Trello API key + token + board IDs^)
echo      - "config.txt"      ^(settings, default OK^)
echo.
echo   2. Run bot: double-click main.py atau run "python main.py"
echo.
echo Untuk update: double-click update.bat
echo.
pause
endlocal
