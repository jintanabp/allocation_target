@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "ROOT=%~dp0"
REM Force UTF-8 for Python stdout/stderr (avoid 'charmap' codec issues on Windows consoles)
set "PYTHONUTF8=1"
set "PORT_PY=%ROOT%runtime\python\python.exe"
set "VPY=%ROOT%.venv\Scripts\python.exe"
set "VPIP=%ROOT%.venv\Scripts\pip.exe"

REM --- แบบไม่ติดตั้ง Python: ใช้ runtime ที่สร้างจาก scripts\build_portable_runtime.bat ---
if exist "%PORT_PY%" (
  set "USE_PY=%PORT_PY%"
  REM Python embed ไม่ใส่ cwd ลง sys.path — บังคับให้เห็นแพ็กเกจ backend
  set "PYTHONPATH=%ROOT%"
  goto :START_SERVER
)

REM --- นักพัฒนา: venv จาก Python ในเครื่อง ---
if not exist "%VPY%" (
  echo ============================================
  echo  Target Allocation — โหมดนักพัฒนา ^(venv^)
  echo ============================================
  echo ไม่พบ runtime\python\ ^(แบบ portable^)
  echo กำลังสร้าง .venv — ต้องมี Python 3.11+ ใน PATH
  echo.
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3.11 -m venv "%ROOT%.venv" 2>nul
    if errorlevel 1 py -3 -m venv "%ROOT%.venv"
  )
  if not exist "%VPY%" (
    where python >nul 2>&1
    if errorlevel 1 goto :NO_RUNTIME
    python -m venv "%ROOT%.venv"
  )
  if not exist "%VPY%" goto :NO_VENV
  echo กำลัง pip install -r requirements.txt ...
  "%VPIP%" install -r "%ROOT%requirements.txt"
  if errorlevel 1 (
    echo.
    echo [ERROR] ติดตั้งแพ็กเกจไม่สำเร็จ
    pause
    exit /b 1
  )
  echo.
)

set "USE_PY=%VPY%"
goto :START_SERVER

:START_SERVER
echo ============================================
echo  Server: http://localhost:8000/  (แนะนำ — ล็อกอิน Microsoft / Entra)
echo            http://127.0.0.1:8000/  (ได้เหมือนกัน แต่ OAuth จะส่งกลับมาที่ localhost)
echo  ข้อมูล/cache อยู่ในโฟลเดอร์ data\
echo  กด Ctrl+C เพื่อหยุด
echo ============================================
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8000/"

"%USE_PY%" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
pause
exit /b 0

:NO_RUNTIME
echo.
echo [ERROR] ไม่พบ Python แบบ portable และไม่มี Python สำหรับสร้าง .venv
echo.
echo ผู้ใช้ทั่วไป: ให้ได้รับโฟลเดอร์ที่รวม runtime\python\ แล้ว ^(สร้างจาก build_portable_runtime.bat^)
echo ผู้ดูแล: รัน  scripts\build_portable_runtime.bat  บนเครื่อง Windows 64-bit ครั้งหนึ่ง
echo           แล้ว zip ทั้งโปรเจกต์รวมโฟลเดอร์ runtime\ แจกจ่าย
echo.
pause
exit /b 1

:NO_VENV
echo.
echo [ERROR] สร้าง .venv ไม่สำเร็จ
echo.
pause
exit /b 1
