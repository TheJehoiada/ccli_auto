@echo off
setlocal EnableDelayedExpansion
REM ------------ Configuration loaded from variables.py ------------
for /f "delims=" %%i in ('py -c "import variables; print(variables.freeshow_usage_source)"') do set "FREESHOW_USAGE_SOURCE=%%i"
for /f "delims=" %%i in ('py -c "import variables; print(variables.freeshow_usage_dir)"') do set "FREESHOW_EXPORT_DIR=%%i"

powershell -Command "Write-Host 'Checking if FreeShow is running.' -ForegroundColor Red"
tasklist /FI "IMAGENAME eq FreeShow.exe" 2>NUL | find /I "FreeShow.exe" >NUL
if "%ERRORLEVEL%"=="0" (
    powershell -Command "Write-Host 'FreeShow is running. Closing in 60 seconds to allow work to be saved...' -ForegroundColor Red"
    TIMEOUT /T 60
    powershell -Command "Write-Host 'Sending close signal to FreeShow...' -ForegroundColor Red"
    powershell -Command "$wsh = New-Object -ComObject WScript.Shell; $wsh.AppActivate('FreeShow'); Start-Sleep -Milliseconds 500; $wsh.SendKeys('%%{F4}')"
    :waitloop
    TIMEOUT /T 3 /NOBREAK >NUL 2>&1
    tasklist /FI "IMAGENAME eq FreeShow.exe" 2>NUL | find /I "FreeShow.exe" >NUL
    if "%ERRORLEVEL%"=="0" (
        powershell -Command "Write-Host 'Waiting for FreeShow to fully close...' -ForegroundColor Yellow"
        goto waitloop
    )
    powershell -Command "Write-Host 'FreeShow has closed.' -ForegroundColor Green"
) else (
    powershell -Command "Write-Host 'FreeShow.exe is not running.' -ForegroundColor Yellow"
)

powershell -Command "Write-Host 'Checking song usage file for entries...' -ForegroundColor Red"

REM Check if the usage file has any CCLI entries before copying
py check_usage.py
if "%ERRORLEVEL%"=="0" (
    REM Usage found - get timestamp and move the file
    powershell -NoProfile -Command "Get-Date -Format 'MM-dd-yyyy' | Out-File -FilePath '%TEMP%\ccli_timestamp.txt' -Encoding ascii -NoNewline"
    set /p timestamp=<"%TEMP%\ccli_timestamp.txt"
    powershell -Command "Write-Host 'Song usage found - exporting file.' -ForegroundColor Red"
    move "%FREESHOW_USAGE_SOURCE%" "%FREESHOW_EXPORT_DIR%\Usage_!timestamp!.json"
    TIMEOUT /T 2
) else (
    powershell -Command "Write-Host 'No song usage found in %FREESHOW_USAGE_SOURCE% - skipping export.' -ForegroundColor Yellow"
)

powershell -Command "Write-Host 'Attempting to report to CCLI.' -ForegroundColor Red"
py auto_ccli.py

powershell -Command "Write-Host 'Done. Press Enter to close.' -ForegroundColor Cyan"
pause >nul
