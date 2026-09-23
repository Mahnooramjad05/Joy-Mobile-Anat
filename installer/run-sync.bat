@echo off
rem ===========================================================================
rem  Runs the KSP price update.
rem
rem    run-sync.bat              from the desktop shortcut: shows a result
rem                              screen and waits for a key press
rem    run-sync.bat scheduled    from Task Scheduler: logs, no screen, and
rem                              passes the exit code back so a failed run is
rem                              retried per the task settings
rem ===========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=interactive"

rem Exit code the whole script reports. 10 means "never got as far as running
rem the sync because there was no internet".
set "CODE=0"

set "PY=%~dp0python\python.exe"
set "LOGDIR=%~dp0logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1

rem Date-stamped log name that does not depend on the machine's date format.
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set "STAMP=%%d"
set "LOG=%LOGDIR%\run-%STAMP%.log"

rem Load the pre-filled settings. tokens=1* keeps any = inside a value intact.
for /f "usebackq eol=# tokens=1* delims==" %%a in ("%~dp0settings.env") do (
    if not "%%a"=="" set "%%a=%%b"
)

if /i "%MODE%"=="interactive" (
    mode con: cols=78 lines=30 >nul 2>&1
    title Update KSP prices
    cls
    echo.
    echo   Updating KSP prices. This usually takes under a minute.
    echo   Please leave this window open.
    echo.
)

echo ============================================================ >> "%LOG%"
echo Run started %DATE% %TIME%  (mode: %MODE%)                    >> "%LOG%"
echo ============================================================ >> "%LOG%"

rem --- Wait for the internet ------------------------------------------------
rem A laptop waking from sleep has a scheduler before it has Wi-Fi.
"%PY%" helper.py wait-online 120 >> "%LOG%" 2>&1
if errorlevel 1 (
    set "RESULT=offline"
    set "CODE=10"
    echo No internet after waiting; the sync was not attempted. >> "%LOG%"
    goto :report
)

rem --- The sync itself -------------------------------------------------------
"%PY%" -m scraper.sync >> "%LOG%" 2>&1
set "CODE=%ERRORLEVEL%"
echo Exit code: %CODE% >> "%LOG%"

if "%CODE%"=="0" ( set "RESULT=ok" ) else ( set "RESULT=code%CODE%" )

:report
rem --- Housekeeping ----------------------------------------------------------
"%PY%" helper.py prune-logs 60 >> "%LOG%" 2>&1

if /i "%MODE%"=="scheduled" goto :finish

rem --- The result screen -----------------------------------------------------
cls
echo.
if "%RESULT%"=="ok" (
    echo   ===============================================================
    echo                            ALL DONE
    echo   ===============================================================
    echo.
    echo   Prices updated. You'll get an email in a few minutes.
    echo.
    echo   From now on this runs by itself every morning at 6:00.
    echo   You can close this window.
    goto :waitkey
)

echo   ===============================================================
echo                             PROBLEM
echo   ===============================================================
echo.
if "%RESULT%"=="offline" (
    echo   The computer is not connected to the internet.
    echo.
    echo   Please check your Wi-Fi and try again by double-clicking
    echo   "Update KSP prices now" on the desktop.
    goto :sendshot
)
if "%RESULT%"=="code1" (
    echo   Could not reach the KSP website.
    echo.
    echo   The prices in the Google Sheet have NOT been changed.
    echo   They are still yesterday's, so nothing is broken.
    goto :sendshot
)
if "%RESULT%"=="code2" (
    echo   KSP's website has changed and the prices could not be read.
    echo.
    echo   The prices in the Google Sheet have NOT been changed.
    echo   This one needs a small fix in the program.
    goto :sendshot
)
if "%RESULT%"=="code3" (
    echo   Could not reach the Google Sheet.
    echo.
    echo   Nothing was changed. This is usually a Google problem
    echo   that fixes itself, or a permissions change on the sheet.
    goto :sendshot
)
if "%RESULT%"=="code4" (
    echo   There is a setup problem with the program itself.
    echo.
    echo   Nothing was changed. This needs a new version.
    goto :sendshot
)
echo   The update stopped unexpectedly (code %CODE%).
echo.
echo   Nothing was changed in the Google Sheet.

:sendshot
echo.
echo   ---------------------------------------------------------------
echo   Please send Mahnoor a screenshot of this window.
echo   ---------------------------------------------------------------

:waitkey
echo.
echo   (log saved in the logs folder)
echo.
pause

:finish
endlocal & exit /b %CODE%
