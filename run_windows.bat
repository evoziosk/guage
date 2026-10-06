@echo off
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=%~dp0.venv\Scripts\pythonw.exe"
)
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=pythonw.exe"
)

start "" "%PYTHON_EXE%" "%~dp0run.py"
