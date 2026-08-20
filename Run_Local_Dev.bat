@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

REM ============================================================
REM  โหมดนักพัฒนา — เหมือน Run_Local.bat แต่ "โหลดโค้ดใหม่ให้เอง"
REM
REM  ทำไมต้องมีไฟล์นี้: ไฟล์หน้าเว็บ (js/css/html) เสิร์ฟสดจากดิสก์ กด Ctrl+F5
REM  ก็เห็นของใหม่ทันที แต่โค้ด Python ถูกโหลดตอน "สตาร์ท server" ครั้งเดียว
REM  แก้ backend แล้วไม่รีสตาร์ท = ทดสอบกับโค้ดเก่าโดยไม่รู้ตัว (เคยหลงมาแล้ว)
REM
REM  ไฟล์นี้เฝ้าไฟล์ .py ให้ ถ้ามีการแก้จะรีสตาร์ทเองอัตโนมัติ
REM  ผู้ใช้ทั่วไปให้ใช้ Run_Local.bat ตามเดิม (เสถียรกว่า ไม่รีสตาร์ทเองระหว่างทำงาน)
REM ============================================================

set "ROOT=%~dp0"
set "PYTHONUTF8=1"
set "PORT_PY=%ROOT%runtime\python\python.exe"
set "VPY=%ROOT%.venv\Scripts\python.exe"

if exist "%PORT_PY%" (
  set "USE_PY=%PORT_PY%"
  set "PYTHONPATH=%ROOT%"
  goto :START
)
if exist "%VPY%" (
  set "USE_PY=%VPY%"
  goto :START
)

echo.
echo [ERROR] ไม่พบ runtime\python\ และไม่พบ .venv
echo         รัน Run_Local.bat หนึ่งครั้งเพื่อสร้าง .venv ก่อน
echo.
pause
exit /b 1

:START
echo ============================================
echo  Target Allocation — โหมดนักพัฒนา (auto-reload)
echo  http://localhost:8000/
echo  แก้ไฟล์ .py แล้ว server จะรีสตาร์ทให้เอง
echo  กด Ctrl+C เพื่อหยุด
echo ============================================
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8000/"

REM เฝ้าเฉพาะโค้ด — ไม่เฝ้า data/ กับ config/ ไม่งั้นจะรีสตาร์ททุกครั้งที่ระบบเขียนไฟล์
"%USE_PY%" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 ^
  --reload --reload-dir backend --reload-include "*.py"

pause
exit /b 0
