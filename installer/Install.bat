@echo off
rem ===========================================================================
rem  Sets up the daily KSP price update. Double-click this file.
rem
rem  No admin rights needed: everything goes under the current user's own
rem  AppData folder, and the task runs as the logged-in user, so Windows never
rem  asks for a password.
rem
rem  Running it again upgrades an existing install in place.
rem ===========================================================================
setlocal
mode con: cols=78 lines=32 >nul 2>&1
title Set up the daily KSP price update
cls

set "DEST=%LOCALAPPDATA%\KSP-Price-Update"
set "SRC=%~dp0app"
set "TASKNAME=KSP Price Update"

echo.
echo   ===============================================================
echo      Setting up the daily KSP price update
echo   ===============================================================
echo.
echo   This takes about a minute. Please leave this window open.
echo.

if not exist "%SRC%\run-sync.bat" (
    echo   PROBLEM: this was run from the wrong place.
    echo.
    echo   Please extract the whole zip file first, then open the
    echo   extracted folder and double-click Install.bat there.
    echo.
    echo   Please send Mahnoor a screenshot of this window.
    echo.
    pause
    exit /b 1
)

echo   [1/3] Copying the program...
if exist "%DEST%" schtasks /End /TN "%TASKNAME%" >nul 2>&1
robocopy "%SRC%" "%DEST%" /E /NFL /NDL /NJH /NJS /NP /R:2 /W:2 >nul 2>&1
if errorlevel 8 (
    echo.
    echo   PROBLEM: the files could not be copied.
    echo   Please send Mahnoor a screenshot of this window.
    echo.
    pause
    exit /b 1
)

echo   [2/3] Setting the daily 6:00 alarm and the desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -File "%DEST%\setup.ps1" -InstallDir "%DEST%"
if errorlevel 1 (
    echo.
    echo   PROBLEM: Windows would not accept the daily schedule.
    echo   Please send Mahnoor a screenshot of this window.
    echo.
    pause
    exit /b 1
)

echo   [3/3] Running the first update now...
echo.

rem run-sync.bat draws the result screen and waits for a key press, so it has
rem the last word on screen. Nothing is printed after it on purpose.
call "%DEST%\run-sync.bat" firstrun
endlocal & exit /b %ERRORLEVEL%
