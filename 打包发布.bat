@echo off
setlocal
REM Build a one-file exe from the project root.
REM Double-click keeps the window open after build.
REM Use this script with "/nopause" for automation.

set "SCRIPT_DIR=%~dp0"
set "APP_NAME="
set "EXE_NAME="
set "ICON_FILE="
set "ENTRY_FILE="
set "NO_PAUSE="

if /I "%~1"=="/nopause" set "NO_PAUSE=1"

REM Check that Python 3 is available.
where python >nul 2>nul
if errorlevel 1 (
    echo Python 3 not found.
    if not defined NO_PAUSE pause
    exit /b 1
)

REM Find the first .pyw entry file and reuse its name for the exe.
for %%I in ("%SCRIPT_DIR%*.pyw") do if not defined ENTRY_FILE (
    set "ENTRY_FILE=%%~nxI"
    set "APP_NAME=%%~nI"
    set "EXE_NAME=%%~nI.exe"
)

REM Find the first .ico file in the current directory.
for %%I in ("%SCRIPT_DIR%*.ico") do if not defined ICON_FILE set "ICON_FILE=%%~nxI"

if not exist "%SCRIPT_DIR%%ENTRY_FILE%" (
    echo Entry script not found.
    if not defined NO_PAUSE pause
    exit /b 1
)

if not exist "%SCRIPT_DIR%%ICON_FILE%" (
    echo Icon file not found.
    if not defined NO_PAUSE pause
    exit /b 1
)

REM Run PyInstaller inside the project directory.
pushd "%SCRIPT_DIR%"
python -m PyInstaller --noconfirm --clean --noconsole --onefile --name "%APP_NAME%" --icon "%ICON_FILE%" "%ENTRY_FILE%"
set "BUILD_ERROR=%ERRORLEVEL%"
popd

if not "%BUILD_ERROR%"=="0" (
    echo Build failed.
    if not defined NO_PAUSE pause
    exit /b %BUILD_ERROR%
)

echo.
echo Build complete: dist\%EXE_NAME%
if not defined NO_PAUSE pause
