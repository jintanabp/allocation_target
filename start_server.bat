@echo off
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

if "%CONDA_PATH%"=="" (
    echo [ERROR] ไม่พบ Miniconda/Anaconda — กรุณารัน setup.bat ก่อน
    pause
    exit /b 1
)

call "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%"
call conda activate allocation_env

echo Server กำลังเริ่มต้น...
echo เปิดเบราว์เซอร์แล้วเปิดไฟล์ index.html
echo กด Ctrl+C เพื่อหยุด server
echo.
uvicorn main:app --host 127.0.0.1 --port 8000
pause
