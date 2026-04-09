@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\.."
echo ============================================
echo  Target Allocation - ติดตั้ง Environment
echo ============================================

REM ถ้าตั้ง CONDA_PATH ไว้ก่อนรัน (หรือใน Environment Variables) จะใช้ค่านั้น — ข้ามการค้นหาด้านล่าง
REM ตำแหน่งที่พบบ่อย (รวม LocalAppData — บางเครื่องติดตั้งที่นี่)
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

REM ถ้ายังไม่เจอ และมีคำสั่ง conda ใน PATH — ใช้ conda info --base
if "%CONDA_PATH%"=="" (
    where conda >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%i in ('conda info --base 2^>nul') do set "CONDA_PATH=%%i"
    )
)

if "%CONDA_PATH%"=="" (
    echo.
    echo [ERROR] ไม่พบ Miniconda/Anaconda ในเครื่องนี้
    echo.
    echo วิธีแก้:
    echo   1. เปิด File Explorer หาโฟลเดอร์ที่มีไฟล์ conda.exe ^(มักชื่อ miniconda3 หรือ miniforge3^)
    echo   2. ตั้งค่าตัวแปรแล้วรัน setup อีกครั้ง ใน cmd:
    echo      set CONDA_PATH=C:\เส้นทาง\ไป\miniconda3
    echo      scripts\setup.bat
    echo   หรือเพิ่ม Miniconda ใน PATH แล้วรัน scripts\setup.bat ใหม่
    echo.
    echo ดาวน์โหลด Miniconda: https://docs.conda.io/en/latest/miniconda.html
    echo.
    pause
    exit /b 1
)

echo พบ conda ที่: %CONDA_PATH%
call "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%"

REM ใช้ conda.bat แบบระบุ path ให้แน่นอน (ไม่พึ่ง PATH)
set "CONDA_BAT=%CONDA_PATH%\condabin\conda.bat"
if not exist "%CONDA_BAT%" set "CONDA_BAT=%CONDA_PATH%\Scripts\conda.bat"
if not exist "%CONDA_BAT%" (
    echo.
    echo [ERROR] ไม่พบ conda.bat ใน %CONDA_PATH%
    echo กรุณาติดตั้ง Miniconda/Anaconda ใหม่ หรือตรวจสอบโฟลเดอร์ให้ถูกต้อง
    echo.
    pause
    exit /b 1
)

REM Accept Conda Terms of Service (เครื่องใหม่บางเครื่องจะติดตรงนี้)
REM ถ้าคำสั่งนี้ไม่มีใน conda เวอร์ชันเก่า จะไม่เป็นไร (ดำเนินการต่อได้)
call "%CONDA_BAT%" tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main >nul 2>&1
call "%CONDA_BAT%" tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r >nul 2>&1
call "%CONDA_BAT%" tos accept --override-channels --channel https://repo.anaconda.com/pkgs/msys2 >nul 2>&1

echo.
echo กำลังตรวจสอบ environment allocation_env...
if exist "%CONDA_PATH%\envs\allocation_env\python.exe" (
    echo พบ allocation_env แล้ว — ข้ามการสร้างใหม่
) else (
    echo กำลังสร้าง environment allocation_env...
    call "%CONDA_BAT%" create -n allocation_env python=3.11 -y
    if errorlevel 1 (
        echo.
        echo [ERROR] สร้าง environment ไม่สำเร็จ
        pause
        exit /b 1
    )
)

call "%CONDA_BAT%" activate allocation_env
if errorlevel 1 (
    echo.
    echo [ERROR] activate environment ไม่สำเร็จ
    echo ลองปิดหน้าต่างแล้วเปิดใหม่ จากนั้นรัน scripts\setup.bat อีกครั้ง
    pause
    exit /b 1
)

echo.
echo กำลังติดตั้ง packages...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] ติดตั้ง packages ไม่สำเร็จ
    pause
    exit /b 1
)

echo.
echo ============================================
echo  ติดตั้งเสร็จแล้ว! รัน scripts\start_server.bat หรือ Run_Local.bat
echo ============================================
pause
