@echo off
setlocal
REM Build a one-file exe from the project root.
REM Double-click keeps the window open after build.
REM Use this script with "/nopause" for automation.

set "SCRIPT_DIR=%~dp0"
set "APP_NAME="
set "EXE_NAME="
set "RELEASE_EXE_NAME=auto-mod-classifier-3.00.exe"
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
python -m PyInstaller --noconfirm --clean --noconsole --onefile --hidden-import=DrissionPage --exclude-module=cv2 --exclude-module=PIL --exclude-module=numpy --exclude-module=openpyxl --exclude-module=cloudscraper --exclude-module=curl_cffi --name "%APP_NAME%" --icon "%ICON_FILE%" --version-file "%SCRIPT_DIR%pyinstaller_version_info.txt" "%ENTRY_FILE%"
set "BUILD_ERROR=%ERRORLEVEL%"
popd

if not "%BUILD_ERROR%"=="0" (
    echo Build failed.
    if not defined NO_PAUSE pause
    exit /b %BUILD_ERROR%
)

if exist "%SCRIPT_DIR%dist\%EXE_NAME%" (
    copy /Y "%SCRIPT_DIR%dist\%EXE_NAME%" "%SCRIPT_DIR%dist\%RELEASE_EXE_NAME%" >nul
)

echo.
echo Build complete: dist\%EXE_NAME%
echo GitHub release copy: dist\%RELEASE_EXE_NAME%
if not defined NO_PAUSE pause
