# การตั้งค่า (บน server / dev)

## ไฟล์ `.env`

คัดลอก **`config/.env.example` → `config/.env`** แล้วกรอกค่า — backend โหลด `config/.env` ก่อน แล้วโหลด `.env` ที่รากถ้ามี

## สิทธิ์ผู้ใช้ — `user_access.json` + `region_teams.json`

รายชื่อ **อีเมล + รหัส SL (USERPL)** เก็บที่ **`config/user_access.json`**

- **ครั้งแรก / อัปเดตจาก ACC CSV:**  
  `python scripts/enrich_user_access_from_csv.py --csv path/to/ACC_USER_CONTROL.csv`  
  สคริปต์จะสร้าง `user_access.json` และ `region_teams.json` (จับคู่ภูมิภาคกับรหัส SL สำหรับผู้จัดการภูมิภาค)
- **Deploy ครั้งแรก:** ไฟล์ทั้งสองอยู่ใน Git — server ได้รายชื่อพร้อมใช้หลัง `git pull`
- **หลัง deploy:** แอดมินแก้ผ่านปุ่ม **「จัดการสิทธิ์」** (อีเมลใน `ALLOCATION_ADMIN_EMAILS`) — การแก้บน server ไม่ถูก commit อัตโนมัติ
- ฟิลด์ `can_import_targetsun` กำหนดใครกด **ส่งเข้า Target Sun** ได้

ตัวอย่างรูปแบบ: ดู `config/user_access.example.json`

## ไฟล์เก่า (เลิกใช้สำหรับสิทธิ์ login)

- `ALLOCATION_ALLOW_ACC_DEV_JSON` / `ACC_USER_CONTROL_DEV_JSON` — ไม่ใช้แล้ว
- `acc_local_test.json` — ใช้ seed สิทธิ์ Target Sun ครั้งแรกได้ (สคริปต์ enrich อ่านอัตโนมัติ)
- `acc_extra_user` บน Fabric — ปิดด้วย `EXTRA_USER_ACCESS_DISABLED=1`

## ความปลอดภัย

`.env` มี secret — อย่า commit; backup `user_access.json` บน server เป็นระยะหลังแอดมินแก้ผ่านเว็บ
