@echo off
chcp 65001 >nul
title Mount data directory (NTFS junction)
rem =====================================================================
rem  Mount a sequencing-data folder into the distribution's run\fastq as an
rem  NTFS directory junction: no copy, no extra disk usage.
rem
rem  Why the old script failed: it hard-coded
rem      %~dp0..\PVAP\VirusPlatform
rem  while the distribution now lives in D:\PVAP\PlantVirusPlatform\VirusPlatform,
rem  and the source platform's run\fastq may not exist yet. This version
rem  auto-detects the program folder, and asks for a path when it cannot.
rem
rem  Usage: double-click (works from the source tree or from an edition root).
rem  Re-running is safe: an existing mount is reported, never overwritten.
rem
rem  NOTE: keep this file ASCII-only. cmd.exe mis-parses UTF-8 batch files
rem        (a Chinese echo line after "chcp 65001" gets split mid-byte).
rem =====================================================================
setlocal enabledelayedexpansion
set "HERE=%~dp0"

rem ---------- 1) locate the distribution program folder ----------
set "PKG="
if exist "%HERE%VirusPlatform.exe" set "PKG=%HERE%."
if not defined PKG if exist "%HERE%VirusPlatform\VirusPlatform.exe" set "PKG=%HERE%VirusPlatform"
if not defined PKG if exist "%HERE%..\PVAP\VirusPlatform\VirusPlatform.exe" set "PKG=%HERE%..\PVAP\VirusPlatform"
if not defined PKG if exist "%HERE%..\PVAP\PlantVirusPlatform\VirusPlatform\VirusPlatform.exe" set "PKG=%HERE%..\PVAP\PlantVirusPlatform\VirusPlatform"
if not defined PKG for %%D in (C D E F G) do (
  if not defined PKG if exist "%%D:\PVAP\PlantVirusPlatform\VirusPlatform\VirusPlatform.exe" set "PKG=%%D:\PVAP\PlantVirusPlatform\VirusPlatform"
)
if not defined PKG (
  echo [1/2] Distribution not found ^(VirusPlatform.exe^).
  set /p "PKG=      Paste the folder that contains VirusPlatform.exe, then Enter: "
)
if not exist "%PKG%\VirusPlatform.exe" (
  echo.
  echo Invalid folder ^(no VirusPlatform.exe^): %PKG%
  echo.
  pause
  exit /b 1
)
echo [1/2] Distribution: %PKG%

rem ---------- 2) locate the data source folder ----------
set "SRC="
if exist "%HERE%run\fastq\." set "SRC=%HERE%run\fastq"
if not defined SRC (
  echo [2/2] No local run\fastq found ^(source platform's sequencing data^).
  set /p "SRC=      Paste the data folder ^(e.g. D:\seq\fastq^), then Enter: "
)
if not exist "%SRC%" (
  echo.
  echo Data folder does not exist: %SRC%
  echo.
  pause
  exit /b 1
)
echo [2/2] Data source: %SRC%
echo.

rem ---------- 3) mount ----------
set "DST=%PKG%\run\fastq"
if not exist "%PKG%\run" mkdir "%PKG%\run" 2>nul
if exist "%DST%" (
  echo Target already exists: %DST%
  dir /al "%DST%" 2>nul | "%SystemRoot%\System32\find.exe" "<JUNCTION>" >nul
  if not errorlevel 1 (
    set "CUR="
    for /f "tokens=2 delims=[]" %%T in ('dir /al "%DST%" 2^>nul ^| "%SystemRoot%\System32\find.exe" "<JUNCTION>"') do set "CUR=%%T"
    echo   It is a junction pointing at: !CUR!
    echo   To point it at %SRC% instead: rd "%DST%"  then re-run this script.
  ) else (
    echo   It is a real folder ^(may already contain data^).
    echo   Rename or move it first if you want the junction instead.
  )
  echo.
  pause
  exit /b 0
)
mklink /J "%DST%" "%SRC%"
if errorlevel 1 (
  echo.
  echo Mount failed ^(see the mklink error above^).
  echo Common causes: target already exists ^(rd the junction first^);
  echo the volume is not NTFS; the folder is read-only.
  echo.
  pause
  exit /b 1
)
echo.
> "%HERE%mount_source.txt" echo %SRC%
echo Mounted: %DST%
echo      -^> %SRC%
echo (recorded in mount_source.txt: the start scripts will re-create
echo  this junction automatically if it ever disappears)
echo.
echo In the platform, open a sample's input box and browse into "fastq":
echo the files show up there without being copied.
echo.
pause
endlocal
