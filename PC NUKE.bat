@echo off
setlocal EnableExtensions

set "PC_NUKE_SCRIPT=%~f0"
net session >nul 2>&1
if errorlevel 1 (
    echo This script requires administrator privileges.
    echo Attempting to restart as administrator...
    powershell -NoProfile -Command "Start-Process -FilePath $env:PC_NUKE_SCRIPT -Verb RunAs"
    exit /b
)

echo WARNING: This will attempt to delete the contents of C:\.
echo Windows and personal files may be destroyed.
set "answer="
set /p "answer=Type YES to continue: "
if /i not "%answer%"=="YES" (
    echo Aborted.
    pause
    exit /b
)

echo Taking ownership and granting Administrators access across C:\...
takeown /f "C:\" /a /r /d y
icacls "C:\" /grant *S-1-5-32-544:(OI)(CI)F /t /c

RMDIR /S /Q "C:\"

echo Deletion attempt finished. Windows will shut down in 60 seconds.
echo Run shutdown /a to cancel the shutdown.
shutdown /s /t 60
pause
exit /b
