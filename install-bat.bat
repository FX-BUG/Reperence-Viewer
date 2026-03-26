@echo off
setlocal

set "DIR=%~dp0"
set "PYTHON_DIR=%DIR%python"
set "PYTHON_EXE=%PYTHON_DIR%\python.exe"

if exist "%PYTHON_EXE%" (
    echo Already installed. Run ReView.bat
    pause
    exit /b 0
)

echo ==========================================
echo   ReView Setup
echo ==========================================
echo.

mkdir "%PYTHON_DIR%" 2>nul

echo [1/4] Downloading Python...
powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip' -OutFile '%PYTHON_DIR%\python.zip'"
if errorlevel 1 (
    echo [ERROR] Download failed.
    pause
    exit /b 1
)

echo [2/4] Extracting...
powershell -Command "Expand-Archive -Path '%PYTHON_DIR%\python.zip' -DestinationPath '%PYTHON_DIR%' -Force"
del "%PYTHON_DIR%\python.zip"

echo [3/4] Installing pip...
echo import site> "%PYTHON_DIR%\python311._pth"
echo python311.zip>> "%PYTHON_DIR%\python311._pth"
echo .>> "%PYTHON_DIR%\python311._pth"
powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%PYTHON_DIR%\get-pip.py'"
"%PYTHON_EXE%" "%PYTHON_DIR%\get-pip.py" --no-warn-script-location
del "%PYTHON_DIR%\get-pip.py"

echo [4/4] Installing packages...
"%PYTHON_EXE%" -m pip install PyQt5 opencv-python numpy Pillow --no-warn-script-location -q

echo.
echo ==========================================
echo   Done! Run ReView.bat
echo ==========================================
pause
