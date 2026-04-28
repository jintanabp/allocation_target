@echo off
chcp 65001 >nul
setlocal
set "TARGET_ALLOC_UPDATE_URL=https://spcsahapat-my.sharepoint.com/:u:/g/personal/jintana_b_sahapat_co_th/IQANrmUJKYeMTbg00hI_R5g_AewsONRWVVcclbOwf0F9ElI?download=1"

set "EXE=%~dp0TargetAllocationLauncher.exe"
set "LOG=%~dp0launcher_last_run.log"

echo ================================================== > "%LOG%"
echo Run at: %DATE% %TIME%>> "%LOG%"
echo EXE: %EXE%>> "%LOG%"
echo URL: %TARGET_ALLOC_UPDATE_URL%>> "%LOG%"
echo ==================================================>> "%LOG%"

if "%TARGET_ALLOC_UPDATE_URL%"=="" (
  echo [ERROR] TARGET_ALLOC_UPDATE_URL ว่าง >> "%LOG%"
  echo [ERROR] TARGET_ALLOC_UPDATE_URL ว่าง
  echo เปิด log: "%LOG%"
  echo.
  pause
  exit /b 1
)

if not exist "%EXE%" (
  echo [ERROR] ไม่พบไฟล์: "%EXE%"
  echo กรุณาตรวจสอบว่าโฟลเดอร์ dist_launcher มีไฟล์ TargetAllocationLauncher.exe
  echo.
  echo [ERROR] ไม่พบไฟล์: "%EXE%" >> "%LOG%"
  echo เปิด log: "%LOG%"
  echo.
  pause
  exit /b 1
)

echo Launching: "%EXE%"
echo Update URL: %TARGET_ALLOC_UPDATE_URL%
echo.

REM เปิด launcher แบบไม่บล็อคหน้าต่างนี้ (ถ้า launcher เปิดไม่ขึ้น จะเห็น error ตรงนี้)
echo start "" "%EXE%" "%TARGET_ALLOC_UPDATE_URL%" >> "%LOG%"
start "" "%EXE%" "%TARGET_ALLOC_UPDATE_URL%"

echo (ถ้าหน้าต่างนี้เด้งหาย แปลว่า launcher เปิดแล้วตามปกติ)
echo ถ้า launcher ไม่ขึ้น ให้ดูข้อความ error ด้านบน
echo log จะถูกบันทึกไว้ที่: "%LOG%"
echo.
pause
