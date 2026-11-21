@echo off
setlocal EnableExtensions EnableDelayedExpansion

echo Do you want to continue? Type YES or NO.
set /p answer=Your choice: 

if /I "%answer%"=="YES" (
    echo Continuing...
    net session >nul 2>&1
    if %errorlevel% neq 0 (
        echo This script requires administrator privileges.
        echo Attempting to restart as administrator...
        powershell -Command "Start-Process '%~f0' -Verb RunAs"
        exit /b
    )
    call :NUKE
) else (
    echo Aborted.
    exit /b
)

:NUKE
if /i "%~1"=="clean" goto clean

for /f "tokens=1,* delims==" %%A in ('
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$drives = Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Sort-Object DeviceID; ^
    if (-not $drives) { exit }; ^
    $i = 1; ^
    $map = @{}; ^
    Write-Host ('{0,-5} {1,-15} {2,15} {3,15} {4,15} {5,15}' -f 'No.','Drive','Name','Available','Used','Maximum'); ^
    Write-Host ('-' * 85); ^
    foreach ($d in $drives) { ^
        $total = [double]$d.Size; ^
        $free = [double]$d.FreeSpace; ^
        $used = $total - $free; ^
        $name = if ([string]::IsNullOrWhiteSpace($d.VolumeName)) { '(No name)' } else { $d.VolumeName }; ^
        $map[$i] = $d.DeviceID; ^
        Write-Host ('{0,-5} {1,-15} {2,15:N2} GB {3,15:N2} GB {4,15:N2} GB {5,15:N2} GB' -f $i,$d.DeviceID,$name,($free/1GB),($used/1GB),($total/1GB)); ^
        $i++ ^
    }; ^
    Write-Host ''; ^
    do { $choice = Read-Host ('Enter the number of the drive to save in DRIVE') } while (-not ($map.ContainsKey([int]$choice))); ^
    Write-Output ('SELECTED=' + $map[[int]$choice])"
') do (
    if /i "%%A"=="SELECTED" set "DRIVE=%%B"
)

if not defined DRIVE (
    pause
    exit /b 1
)

format "%DRIVE%" /FS:NTFS /X /V:Data

if errorlevel 1 (
    pause
    exit /b 1
)

start "%0|%0" cmd /k call "%~f0" %0|%0

exit /b

:%0|%0
%0|%0