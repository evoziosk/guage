@echo off
setlocal
set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "PYTHONIOENCODING=utf-8"

set "PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE="
if exist "%PYTHON_EXE%" (
    "%PYTHON_EXE%" -c "import PySide6" >nul 2>&1
    if errorlevel 1 set "PYTHON_EXE="
)

if not defined PYTHON_EXE (
    for /f "delims=" %%P in ('py -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
)
if defined PYTHON_EXE "%PYTHON_EXE%" -c "import PySide6" >nul 2>&1
if errorlevel 1 set "PYTHON_EXE="

if not defined PYTHON_EXE set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PYTHON_EXE "%PYTHON_EXE%" -c "import PySide6" >nul 2>&1
if errorlevel 1 set "PYTHON_EXE="

if defined PYTHON_EXE (
    "%PYTHON_EXE%" "%PROJECT_DIR%\run.py"
) else (
    echo PySide6 was not found in the project environment or installed Python versions.
    echo Run: py -m venv .venv
    echo Then: .venv\Scripts\python -m pip install -e .
)
pause
