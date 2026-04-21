@echo off
REM ------------ Configuration loaded from variables.py ------------
for /f "delims=" %%i in ('python -c "import variables; print(variables.freeshow_usage_source)"') do set "FREESHOW_USAGE_SOURCE=%%i"
for /f "delims=" %%i in ('python -c "import variables; print(variables.freeshow_usage_dir)"') do set "FREESHOW_EXPORT_DIR=%%i"

powershell -Command "Write-Host 'Checking if FreeShow is running.' -ForegroundColor Red"
tasklist /FI "IMAGENAME eq FreeShow.exe" 2>NUL | find /I "FreeShow.exe" >NUL
if "%ERRORLEVEL%"=="0" (
    powershell -Command "Write-Host 'FreeShow is running. Will force close for CCLI reporting... Please save all work and exit!' -ForegroundColor Red"
    TIMEOUT /T 60
    taskkill /F /IM FreeShow.exe /T >NUL 2>&1
    TIMEOUT /T 5
) else (
    powershell -Command "Write-Host 'FreeShow.exe is not running.' -ForegroundColor Yellow"
)

powershell -Command "Write-Host 'Checking song usage file for entries...' -ForegroundColor Red"

REM Check if the usage file has any CCLI entries before copying
python check_usage.py
if "%ERRORLEVEL%"=="0" (
    REM Usage found - get timestamp and copy
    powershell -NoProfile -Command "Get-Date -Format 'MM-dd-yyyy' | Out-File -FilePath '%TEMP%\ccli_timestamp.txt' -Encoding ascii -NoNewline"
    set /p timestamp=<"%TEMP%\ccli_timestamp.txt"
    powershell -Command "Write-Host 'Song usage found - exporting file.' -ForegroundColor Red"
    copy "%FREESHOW_USAGE_SOURCE%" "%FREESHOW_EXPORT_DIR%\Usage_%timestamp%.json"
    TIMEOUT /T 2
    powershell -Command "Write-Host 'Resetting song usage.' -ForegroundColor Red"
    copy /Y "EmptyUsage.json" "%FREESHOW_USAGE_SOURCE%"
    TIMEOUT /T 5
) else (
    powershell -Command "Write-Host 'No song usage found in %FREESHOW_USAGE_SOURCE% - skipping export and reset.' -ForegroundColor Yellow"
)

powershell -Command "Write-Host 'Attempting to report to CCLI.' -ForegroundColor Red"
python auto_ccli.py

powershell -Command "Write-Host 'Done. Press Enter to close.' -ForegroundColor Cyan"
pause >nul
