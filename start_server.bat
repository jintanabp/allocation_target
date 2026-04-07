@echo off
chcp 65001 >nul
setlocal
echo ============================================
echo  Target Allocation - เริ่ม Server
echo ============================================

REM
set CONDA_PATH=
if exist "%USERPROFILE%\miniconda3\Scripts\conda.exe" set CONDA_PATH=%USERPROFILE%\miniconda3
if exist "%USERPROFILE%\Miniconda3\Scripts\conda.exe" set CONDA_PATH=%USERPROFILE%\Miniconda3
if exist "%USERPROFILE%\anaconda3\Scripts\conda.exe" set CONDA_PATH=%USERPROFILE%\anaconda3
if exist "%USERPROFILE%\Anaconda3\Scripts\conda.exe" set CONDA_PATH=%USERPROFILE%\Anaconda3
if exist "C:\ProgramData\miniconda3\Scripts\conda.exe" set CONDA_PATH=C:\ProgramData\miniconda3
if exist "C:\ProgramData\Miniconda3\Scripts\conda.exe" set CONDA_PATH=C:\ProgramData\Miniconda3
if exist "C:\ProgramData\anaconda3\Scripts\conda.exe" set CONDA_PATH=C:\ProgramData\anaconda3

if "%CONDA_PATH%"=="" goto :ERR_NO_CONDA

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
echo เปิดเบราว์เซอร์แล้วเปิดไฟล์ index.html
echo กด Ctrl+C เพื่อหยุด server
echo.
uvicorn main:app --host 127.0.0.1 --port 8000
pause
exit /b 0

:ERR_NO_CONDA
echo [ERROR] ไม่พบ Miniconda/Anaconda — กรุณารัน setup.bat ก่อน
pause
exit /b 1

:ERR_NO_CONDA_BAT
echo.
echo [ERROR] ไม่พบ conda.bat ใน %CONDA_PATH%
echo กรุณารัน setup.bat ใหม่ หรือ reinstall Miniconda/Anaconda
echo.
pause
exit /b 1

:ERR_NO_ENV
echo.
echo [ERROR] ไม่พบ environment: allocation_env
echo กรุณารัน setup.bat (ครั้งแรก) ก่อน
echo.
pause
exit /b 1

:ERR_ACTIVATE
echo.
echo [ERROR] activate allocation_env ไม่สำเร็จ
echo ลองปิดหน้าต่างแล้วเปิดใหม่ จากนั้นรัน start_server.bat อีกครั้ง
echo.
pause
exit /b 1
