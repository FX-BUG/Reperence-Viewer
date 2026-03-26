@echo off
set "DIR=%~dp0"
if not exist "%DIR%python\python.exe" (
    echo Run install.bat first!
    pause
    exit /b 1
)
set "QT_QPA_PLATFORM_PLUGIN_PATH=%DIR%python\Lib\site-packages\PyQt5\Qt5\plugins\platforms"
set "QT_PLUGIN_PATH=%DIR%python\Lib\site-packages\PyQt5\Qt5\plugins"
start "" "%DIR%python\pythonw.exe" "%DIR%gif_ref_viewer.py"
