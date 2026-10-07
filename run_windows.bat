@echo off
setlocal
set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%PROJECT_DIR%"

set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\pythonw.exe"
)
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=pythonw.exe"
)

start "" "%PYTHON_EXE%" "%PROJECT_DIR%\run.py"
