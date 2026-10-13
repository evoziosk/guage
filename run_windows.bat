@echo off
setlocal
set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%PROJECT_DIR%"

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

if defined PYTHON_EXE for %%I in ("%PYTHON_EXE%") do set "PYTHONW_EXE=%%~dpIpythonw.exe"
if not defined PYTHON_EXE (
    call "%PROJECT_DIR%\run_windows_debug.bat"
    exit /b 1
)
if not exist "%PYTHONW_EXE%" (
    echo Could not find pythonw.exe beside the Python environment with PySide6.
    call "%PROJECT_DIR%\run_windows_debug.bat"
    exit /b 1
)

start "" "%PYTHONW_EXE%" "%PROJECT_DIR%\run.py"
