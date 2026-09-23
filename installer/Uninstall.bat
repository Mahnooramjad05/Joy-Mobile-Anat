@echo off
rem ===========================================================================
rem  Removes the daily KSP price update completely: the 6:00 schedule, the
rem  desktop shortcut and the program folder. Double-click this file.
rem
rem  The Google Sheet is not touched. Prices already in it stay exactly as
rem  they are; they simply stop being updated.
rem ===========================================================================
setlocal
mode con: cols=78 lines=26 >nul 2>&1
title Remove the daily KSP price update
cls

set "DEST=%LOCALAPPDATA%\KSP-Price-Update"

echo.
echo   ===============================================================
echo      Removing the daily KSP price update
echo   ===============================================================
echo.
echo   The Google Sheet will not be changed. The prices already in it
echo   stay as they are, they just stop being updated each morning.
echo.
choice /C YN /N /M "   Remove it? Press Y for yes, N to cancel: "
if errorlevel 2 (
    echo.
    echo   Cancelled. Nothing was removed.
    echo.
    pause
    exit /b 0
)
echo.

echo   Removing the schedule and the shortcut...
if exist "%DEST%\setup.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%DEST%\setup.ps1" -InstallDir "%DEST%" -Remove
) else (
    schtasks /Delete /TN "KSP Price Update" /F >nul 2>&1
    del "%USERPROFILE%\Desktop\Update KSP prices now.lnk" >nul 2>&1
)

echo   Removing the program folder...
rem Step out of the folder first, or Windows will not let it be deleted.
cd /d "%LOCALAPPDATA%"
rmdir /S /Q "%DEST%" >nul 2>&1

if exist "%DEST%" (
    echo.
    echo   Most of it was removed, but some files are still in use.
    echo   Restarting the computer and running this again will finish it.
) else (
    echo.
    echo   ===============================================================
    echo      All removed.
    echo   ===============================================================
)
echo.
echo   Press any key to close this window.
pause >nul
endlocal
