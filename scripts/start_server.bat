@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\.."
echo ============================================
echo  Target Allocation - เริ่ม Server
echo ============================================

REM การหา conda — ให้ตรงกับ scripts\setup.bat (รองรับ LocalAppData, miniforge, conda ใน PATH)
if "%CONDA_PATH%"=="" if exist "%USERPROFILE%\miniconda3\Scripts\conda.exe" set CONDA_PATH=%USERPROFILE%\miniconda3
if "%CONDA_PATH%"=="" if exist "%USERPROFILE%\Miniconda3\Scripts\conda.exe" set CONDA_PATH=%USERPROFILE%\Miniconda3
if "%CONDA_PATH%"=="" if exist "%LOCALAPPDATA%\miniconda3\Scripts\conda.exe" set CONDA_PATH=%LOCALAPPDATA%\miniconda3
if "%CONDA_PATH%"=="" if exist "%LOCALAPPDATA%\Miniconda3\Scripts\conda.exe" set CONDA_PATH=%LOCALAPPDATA%\Miniconda3
if "%CONDA_PATH%"=="" if exist "%USERPROFILE%\anaconda3\Scripts\conda.exe" set CONDA_PATH=%USERPROFILE%\anaconda3
if "%CONDA_PATH%"=="" if exist "%USERPROFILE%\Anaconda3\Scripts\conda.exe" set CONDA_PATH=%USERPROFILE%\Anaconda3
if "%CONDA_PATH%"=="" if exist "C:\ProgramData\miniconda3\Scripts\conda.exe" set CONDA_PATH=C:\ProgramData\miniconda3
if "%CONDA_PATH%"=="" if exist "C:\ProgramData\Miniconda3\Scripts\conda.exe" set CONDA_PATH=C:\ProgramData\Miniconda3
if "%CONDA_PATH%"=="" if exist "C:\ProgramData\anaconda3\Scripts\conda.exe" set CONDA_PATH=C:\ProgramData\anaconda3
if "%CONDA_PATH%"=="" if exist "%USERPROFILE%\miniforge3\Scripts\conda.exe" set CONDA_PATH=%USERPROFILE%\miniforge3
if "%CONDA_PATH%"=="" if exist "%USERPROFILE%\mambaforge\Scripts\conda.exe" set CONDA_PATH=%USERPROFILE%\mambaforge

if "%CONDA_PATH%"=="" (
    where conda >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%i in ('conda info --base 2^>nul') do set "CONDA_PATH=%%i"
    )
)

if "%CONDA_PATH%"=="" goto :ERR_NO_CONDA

echo พบ conda ที่: %CONDA_PATH%
call "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%"

REM ใช้ conda.bat แบบระบุ path ให้แน่นอน (ไม่พึ่ง PATH)
set "CONDA_BAT=%CONDA_PATH%\condabin\conda.bat"
if not exist "%CONDA_BAT%" set "CONDA_BAT=%CONDA_PATH%\Scripts\conda.bat"
if not exist "%CONDA_BAT%" goto :ERR_NO_CONDA_BAT

REM บังคับใช้ allocation_env เท่านั้น (กันหลุดไป base)
if not exist "%CONDA_PATH%\envs\allocation_env\python.exe" goto :ERR_NO_ENV

call "%CONDA_BAT%" activate allocation_env
if errorlevel 1 goto :ERR_ACTIVATE

echo Server กำลังเริ่มต้น...
echo เปิดเบราว์เซอร์ที่: http://127.0.0.1:8000/
echo กด Ctrl+C เพื่อหยุด server
echo.
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:8000/"
uvicorn backend.main:app --host 127.0.0.1 --port 8000
pause
exit /b 0

:ERR_NO_CONDA
echo.
echo [ERROR] ไม่พบ Miniconda/Anaconda ใน path ที่สคริปต์รู้จัก
echo.
echo ถ้า scripts\setup.bat รันผ่านแล้ว ลองตั้งค่าแล้วรันใหม่:
echo   set CONDA_PATH=C:\เส้นทาง\ไป\โฟลเดอร์ miniconda3
echo   scripts\start_server.bat
echo.
echo หรือเปิด Miniconda Prompt แล้ว cd มาที่โฟลเดอร์โปรเจกต์ แล้วรัน scripts\start_server.bat
echo.
pause
exit /b 1

:ERR_NO_CONDA_BAT
echo.
echo [ERROR] ไม่พบ conda.bat ใน %CONDA_PATH%
echo กรุณารัน scripts\setup.bat ใหม่ หรือ reinstall Miniconda/Anaconda
echo.
pause
exit /b 1

:ERR_NO_ENV
echo.
echo [ERROR] ไม่พบ environment: allocation_env
echo กรุณารัน scripts\setup.bat (ครั้งแรก) ก่อน
echo.
pause
exit /b 1

:ERR_ACTIVATE
echo.
echo [ERROR] activate allocation_env ไม่สำเร็จ
echo ลองปิดหน้าต่างแล้วเปิดใหม่ จากนั้นรัน scripts\start_server.bat อีกครั้ง
echo.
pause
exit /b 1
