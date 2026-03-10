@echo off
setlocal

:: Get current directory
set "BASE_DIR=%~dp0"
set "BASE_DIR=%BASE_DIR:~0,-1%"

:: Python path
set "PYTHON_EXE=python.exe"

:: Check Python
%PYTHON_EXE% --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please ensure python.exe is in your PATH.
    pause
    exit /b 1
)

:: 1. Create caps command shortcut (caps.bat)
echo [INFO] Creating caps command shortcut...
(
    echo @echo off
    echo "%PYTHON_EXE%" "%BASE_DIR%\caps_ctl.py" %%*
) > "%BASE_DIR%\caps.bat"

:: 2. Add to PATH
echo [INFO] Checking PATH environment variable...
echo %PATH% | findstr /I /C:"%BASE_DIR%" >nul
if %errorlevel% neq 0 (
    echo [INFO] Adding %BASE_DIR% to user PATH...
    setx PATH "%BASE_DIR%;%PATH%"
    echo [SUCCESS] PATH updated. Please restart your terminal.
) else (
    echo [INFO] PATH already set. Skipping.
)

:: 3. Create startup shortcut
echo [INFO] Configuring startup shortcut...
set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT_PATH=%STARTUP_FOLDER%\CapsWriter-Monitor.lnk"
set "VBS_SCRIPT=%TEMP%\CreateShortcut.vbs"

set "TARGET_SCRIPT=%BASE_DIR%\caps_monitor.py"
set "TARGET_PYTHON=pythonw.exe"

(
    echo Set oWS = WScript.CreateObject^("WScript.Shell"^)
    echo sLinkFile = "%SHORTCUT_PATH%"
    echo Set oLink = oWS.CreateShortcut^(sLinkFile^)
    echo oLink.TargetPath = "%TARGET_PYTHON%"
    echo oLink.Arguments = """%TARGET_SCRIPT%"""
    echo oLink.WorkingDirectory = "%BASE_DIR%"
    echo oLink.Description = "CapsWriter Offline Monitor"
    echo oLink.Save
) > "%VBS_SCRIPT%"

cscript /nologo "%VBS_SCRIPT%"
del "%VBS_SCRIPT%"

echo [SUCCESS] Startup shortcut created: %SHORTCUT_PATH%

:: 4. Start service immediately
echo [INFO] Starting monitor service...
"%PYTHON_EXE%" "%BASE_DIR%\caps_ctl.py" start

echo.
echo ========================================================
echo   Installation Complete!
echo   You can now use 'caps' command in a new terminal:
echo.
echo     caps status            - Check service status
echo     caps mode saving       - Switch to Saving Mode (iGPU)
echo     caps mode performance  - Switch to Performance Mode (dGPU)
echo     caps restart           - Restart all services
echo     caps stop              - Stop all services
echo.
echo   Monitor service is running in background.
echo ========================================================
pause
