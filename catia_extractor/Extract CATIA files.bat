@echo off
title CATIA Data Extractor
rem Double-click to choose CATIA files, or drag files/folders onto this icon.
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3 from the Microsoft Store, then try again.
  pause
  exit /b 1
)
python "%~dp0catia_extract.py" --gui %*
