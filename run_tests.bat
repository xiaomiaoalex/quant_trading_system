@echo off
REM pytest runner script for Windows
REM Handles paths with spaces correctly
setlocal enabledelayedexpansion

REM Load PostgreSQL environment variables if .env.postgres exists
if exist ".env.postgres" (
    echo Loading PostgreSQL configuration from .env.postgres
    for /f "usebackq tokens=*" %%a in (".env.postgres") do set %%a
)

set VENV_PYTHON=.venv\Scripts\python.exe
set TEST_PATH=%1
if "%TEST_PATH%"=="" set TEST_PATH=trader\tests

"%VENV_PYTHON%" -m pytest "%TEST_PATH%" -v
