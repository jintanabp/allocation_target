@echo off
echo ============================================
echo  Target Allocation - ติดตั้ง Environment
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
    echo.
    echo [ERROR] ไม่พบ Miniconda/Anaconda ในเครื่องนี้
    echo กรุณาดาวน์โหลดและติดตั้งก่อน:
    echo https://docs.conda.io/en/latest/miniconda.html
    echo.
    pause
    exit /b 1
)

echo พบ conda ที่: %CONDA_PATH%
call "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%"

echo.
echo กำลังสร้าง environment allocation_env...
call conda create -n allocation_env python=3.11 -y

call conda activate allocation_env

echo.
echo กำลังติดตั้ง packages...
pip install -r requirements.txt

echo.
echo ============================================
echo  ติดตั้งเสร็จแล้ว! กด start_server.bat เพื่อเริ่มใช้งาน
echo ============================================
pause
