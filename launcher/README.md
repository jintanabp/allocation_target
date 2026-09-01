# Launcher (.exe) + Auto-update — งานค้าง ยังไม่ได้ใช้จริง

> **สถานะ:** งานจากปลายเดือน เม.ย. 2026 ที่ยังทำไม่จบ **ยังไม่ถูก merge เข้า main**
> และ main ปัจจุบันยังใช้ `Run_Local.bat` ตามเดิม · เก็บไว้เผื่อกลับมาทำต่อ
>
> **ที่มา:** branch เดิม `feature/launcher-autoupdate` เผลอ commit `config/.env` ตัวจริง
> (มี `FABRIC_CLIENT_SECRET`) และไฟล์ build ~22 MB เข้ามาด้วย เพราะลบทั้งสองอย่าง
> ออกจาก `.gitignore` · branch นี้คือการยกมาเฉพาะโค้ด ไม่มีความลับและไม่มี binary
> secret ตัวที่หลุดถูกเปลี่ยนไปแล้ว (ยืนยันแล้วว่าคนละตัวกับที่ใช้อยู่)
>
> **ก่อนทำต่อ:** ห้ามแตะ `.gitignore` บรรทัด `config/.env` / `/build` / `dist*` เด็ดขาด
> ถ้าจำเป็นต้องมีไฟล์ตัวอย่าง ให้ใช้ `config/.env.example` ที่มีอยู่แล้ว

## แนวคิด

ผู้ใช้มีไฟล์ `TargetAllocationLauncher.exe` แค่ไฟล์เดียว กดแล้ว

- เช็คไฟล์ `latest.json` จาก URL ภายในบริษัท (HTTPS)
- ถ้ามีเวอร์ชันใหม่ → ดาวน์โหลด zip → ตรวจ `sha256` → ติดตั้งให้เองที่ `%LOCALAPPDATA%\TargetAllocation\app`
- หาพอร์ตว่างอัตโนมัติ เปิด server แล้วเปิดเว็บให้เอง

พอร์ตถูกส่งเข้า `Run_Local.bat` เป็น argument ตัวแรก (`Run_Local.bat 8123`)
ไม่ใส่ = 8000 เหมือนเดิม — เครื่องผู้ใช้บางเครื่องมีโปรแกรมอื่นจอง 8000 อยู่แล้ว

## สิ่งที่ IT ต้องเตรียม

โฮสต์ไฟล์ 2 อย่างไว้ใน internal HTTPS URL ที่ทุกคนเข้าถึงได้:

- `latest.json` — รูปแบบตาม [`latest.schema.json`](latest.schema.json) · ตัวอย่าง: [`latest.example.json`](latest.example.json)
- `TargetAllocation-<version>.zip`

> ลิงก์แชร์ของ SharePoint / Microsoft 365 มักต้องล็อกอินผ่านเบราว์เซอร์ก่อน
> จึงใช้เป็น URL อัตโนมัติไม่ได้ — ต้องเป็น URL ที่ดาวน์โหลดได้ตรง ๆ

## การสร้างไฟล์สำหรับแจก (ฝั่ง dev)

สร้าง zip release + `latest.json` (ได้ที่ `dist_release\`):

```powershell
scripts\build_release_zip.ps1 -Version 1.0.0
```

สร้าง `TargetAllocationLauncher.exe` (ได้ที่ `dist_launcher\`):

```powershell
scripts\build_launcher.ps1
```

สร้าง shortcut + `Start Target Allocation.cmd` ให้ผู้ใช้:

```powershell
scripts\make_launcher_shortcut.ps1
```

ตรวจว่าไฟล์ที่จะแจกครบและถูกต้อง:

```powershell
scripts\check_launcher_dist.ps1
```

**ผลลัพธ์ทั้งหมดอยู่ใน `dist_launcher/` `dist_release/` `build/` ซึ่ง git ไม่เก็บ** —
อย่า commit เข้ามา (นั่นคือสิ่งที่ทำให้ branch เดิมใช้ต่อไม่ได้)

## การใช้งานของผู้ใช้

ก๊อป `TargetAllocationLauncher.exe` ไปวางที่ไหนก็ได้ แล้วกดเปิด — ครั้งแรกจะโหลดตัวโปรแกรมมาติดตั้งให้เอง
ครั้งถัดไปจะเช็คเวอร์ชันใหม่ให้อัตโนมัติก่อนเปิด

## ไฟล์ในชุดนี้

| ไฟล์ | หน้าที่ |
|---|---|
| `launcher/launcher.py` | ตัวเปิดโปรแกรม — เช็คอัปเดต ติดตั้ง หาพอร์ตว่าง เปิด server |
| `launcher/latest.schema.json` | สเปกของ `latest.json` |
| `launcher/latest.example.json` | ตัวอย่าง `latest.json` |
| `TargetAllocationLauncher.spec` | สเปก PyInstaller |
| `scripts/build_launcher.ps1` | build `.exe` |
| `scripts/build_release_zip.ps1` | build zip + `latest.json` |
| `scripts/make_launcher_shortcut.ps1` | สร้าง shortcut / `.cmd` |
| `scripts/check_launcher_dist.ps1` | ตรวจไฟล์ที่จะแจกก่อนส่งมอบ |
