@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  QC Marketing Bot - AUTO UPDATE
echo ============================================================
echo.

REM === 1. Stop bot kalau sedang jalan ===
echo [1/4] Stop bot kalau sedang jalan...
REM Match Python window dgn title "QC Marketing Bot" atau process menjalankan main.py
taskkill /FI "WINDOWTITLE eq QC Marketing Bot*" /F >nul 2>&1
REM Kill semua python.exe yang menjalankan main.py di folder ini (best-effort)
for /f "tokens=2" %%P in ('wmic process where "name='python.exe' and CommandLine like '%%QC Marketing\\main.py%%'" get ProcessId 2^>nul ^| findstr /R "[0-9]"') do (
    taskkill /F /PID %%P >nul 2>&1
)
timeout /t 2 /nobreak >nul

REM === 2. Git pull terbaru ===
echo.
echo [2/4] Pull update dari GitHub...

where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] git tidak ditemukan di PATH.
    echo Install Git dari: https://git-scm.com/download/win
    pause
    exit /b 1
)

REM Stash local changes (config.txt, secrets sudah .gitignore'd jadi aman)
git stash push -m "auto-stash before update" >nul 2>&1

git pull origin main
if errorlevel 1 (
    echo [ERROR] git pull gagal. Cek konflik atau koneksi.
    git stash pop >nul 2>&1
    pause
    exit /b 1
)

git stash pop >nul 2>&1

REM Tampil versi terbaru
if exist VERSION.txt (
    set /p VERSION=<VERSION.txt
    echo        Versi terbaru: !VERSION!
)
echo.

REM === 3. Upgrade Python deps ===
echo [3/4] Upgrade Python dependencies...
python -m pip install --upgrade --quiet -r requirements.txt
if errorlevel 1 (
    echo [WARN] Gagal upgrade beberapa package. Lanjut tetap launch bot.
)
echo.

REM === 4. Launch bot ===
echo [4/4] Launch bot...
start "" pythonw main.py 2>nul
if errorlevel 1 (
    REM Fallback: pakai python.exe biasa kalau pythonw tidak tersedia
    start "" python main.py
)

echo.
echo ============================================================
echo  UPDATE SELESAI. Bot sudah launch dengan versi terbaru.
echo ============================================================
echo.
timeout /t 3 /nobreak >nul
endlocal
