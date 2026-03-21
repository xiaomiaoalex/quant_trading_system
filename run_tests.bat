@echo off
REM pytest runner script for Windows
REM Handles paths with spaces correctly
setlocal enabledelayedexpansion

set VENV_PYTHON=.venv\Scripts\python.exe
set TEST_PATH=%1
if "%TEST_PATH%"=="" set TEST_PATH=trader\tests

"%VENV_PYTHON%" -m pytest "%TEST_PATH%" -v
