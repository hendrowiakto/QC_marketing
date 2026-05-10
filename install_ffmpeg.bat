@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo QC Marketing Bot - Install FFmpeg (Portable)
echo ============================================================
echo.
echo Akan download ffmpeg portable (~85 MB) dari github BtbN release.
echo Lokasi install: %~dp0tools\ffmpeg\
echo.
echo Bot akan otomatis pakai ffmpeg dari folder ini setelah selesai.
echo.

REM Check if already installed
if exist "tools\ffmpeg\bin\ffmpeg.exe" (
    echo [INFO] ffmpeg sudah terinstall di tools\ffmpeg\bin\
    "tools\ffmpeg\bin\ffmpeg.exe" -version 2^>^&1 ^| findstr /B "ffmpeg version"
    echo.
    echo Mau install ulang? Ketik Y untuk hapus + reinstall, lainnya untuk skip.
    set /p REINSTALL=
    if /I not "%REINSTALL%"=="Y" goto :END
    echo Hapus install lama...
    rmdir /s /q "tools\ffmpeg" 2>nul
)

mkdir tools 2>nul

REM Download
set ZIP_URL=https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip
set ZIP_FILE=tools\ffmpeg.zip

echo [1/3] Download ffmpeg...
powershell -NoProfile -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri '%ZIP_URL%' -OutFile '%ZIP_FILE%' -UseBasicParsing; exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }}"

if not exist "%ZIP_FILE%" (
    echo.
    echo [ERROR] Download gagal. Cek koneksi internet.
    echo Manual: download dari %ZIP_URL%
    echo Lalu extract ke tools\ffmpeg\ (struktur: tools\ffmpeg\bin\ffmpeg.exe)
    pause
    exit /b 1
)

echo [2/3] Extract...
powershell -NoProfile -Command "Expand-Archive -Path '%ZIP_FILE%' -DestinationPath 'tools\' -Force"

REM Folder yg di-extract namanya seperti "ffmpeg-master-latest-win64-gpl"
REM Rename ke "ffmpeg" supaya path konsisten
for /d %%D in (tools\ffmpeg-*) do (
    if exist "tools\ffmpeg" rmdir /s /q "tools\ffmpeg" 2>nul
    move "%%D" "tools\ffmpeg" >nul
)

del "%ZIP_FILE%" 2>nul

echo [3/3] Verify...
if not exist "tools\ffmpeg\bin\ffmpeg.exe" (
    echo.
    echo [ERROR] ffmpeg.exe tidak ditemukan setelah extract.
    pause
    exit /b 1
)

"tools\ffmpeg\bin\ffmpeg.exe" -version 2>&1 | findstr /B "ffmpeg version"

if errorlevel 1 (
    echo.
    echo [ERROR] ffmpeg test gagal.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo SUCCESS: ffmpeg terinstall.
echo Path: %~dp0tools\ffmpeg\bin\ffmpeg.exe
echo Bot akan otomatis pakai dari lokasi ini saat ada video.
echo ============================================================

:END
echo.
pause
endlocal
