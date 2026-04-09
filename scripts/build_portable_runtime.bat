@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo.
echo ============================================
echo  สร้าง Python แบบ portable ใน runtime\python\
echo  ต้องมีอินเทอร์เน็ตครั้งนี้ — ใช้ Windows 64-bit เท่านั้น
echo  เสร็จแล้วให้ zip ทั้งโปรเจกต์รวมโฟลเดอร์ runtime\ แจกผู้ใช้
echo  ผู้ใช้ไม่ต้องติดตั้ง Python แค่ดับเบิลคลิก Run_Local.bat
echo ============================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_portable_runtime.ps1"
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)
echo.
pause
