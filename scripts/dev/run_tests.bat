@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\..\.."
set "PYTHONPATH=%CD%"

if exist "%CD%\runtime\python\python.exe" (
  set "PY=%CD%\runtime\python\python.exe"
  "%PY%" scripts\dev\install_repo_pythonpath.py >nul 2>&1
) else (
  set "PY=python"
)

if "%~1"=="" (
  "%PY%" run_tests.py
  goto :DONE
)

if /I "%~1"=="unittest" (
  shift
  "%PY%" -m unittest %*
  goto :DONE
)

"%PY%" run_tests.py %*

:DONE
exit /b %ERRORLEVEL%
