@echo off
rem =====================================================================
rem  Non-interactive auto-mount, called by Start-Web.bat / Start-Desktop.bat.
rem
rem  Re-creates the junction <program>\run\fastq -> <recorded folder> when
rem  mount_source.txt exists and the junction is missing. Silent no-op when
rem  nothing is configured, the folder is gone, or the junction already
rem  exists: startup must never block or fail because of this.
rem
rem  mount_source.txt is written by 挂载数据目录.bat (one line: the folder).
rem  KEEP THIS FILE ASCII-ONLY (cmd.exe mis-parses UTF-8 batch files).
rem =====================================================================
setlocal enabledelayedexpansion
set "HERE=%~dp0"
if not exist "%HERE%mount_source.txt" exit /b 0

set "SRC="
set /p SRC=<"%HERE%mount_source.txt"
if not defined SRC exit /b 0
if not exist "%SRC%" exit /b 0

rem ---- locate the program folder (same order as 挂载数据目录.bat) ----
set "PKG="
if exist "%HERE%VirusPlatform.exe" set "PKG=%HERE%."
if not defined PKG if exist "%HERE%VirusPlatform\VirusPlatform.exe" set "PKG=%HERE%VirusPlatform"
if not defined PKG if exist "%HERE%..\PVAP\VirusPlatform\VirusPlatform.exe" set "PKG=%HERE%..\PVAP\VirusPlatform"
if not defined PKG for %%D in (C D E F G) do (
  if not defined PKG if exist "%%D:\PVAP\PlantVirusPlatform\VirusPlatform\VirusPlatform.exe" set "PKG=%%D:\PVAP\PlantVirusPlatform\VirusPlatform"
)
if not defined PKG exit /b 0
if not exist "%PKG%\VirusPlatform.exe" exit /b 0

set "DST=%PKG%\run\fastq"
if not exist "%PKG%\run" mkdir "%PKG%\run" 2>nul
if exist "%DST%" (
  dir /al "%DST%" 2>nul | "%SystemRoot%\System32\find.exe" "<JUNCTION>" >nul
  if not errorlevel 1 exit /b 0
  exit /b 0
)
mklink /J "%DST%" "%SRC%" >nul 2>&1
if errorlevel 1 exit /b 1
echo   [mount] %DST% -^> %SRC%
exit /b 0
