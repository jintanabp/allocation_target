# การตั้งค่า (บน server / dev)

## ไฟล์ `.env`

บน **server บริษัท** (ไม่ commit): คัดลอก **`config/.env.example` → `config/.env`** แล้วกรอกค่า — backend โหลด `config/.env` ก่อน แล้วโหลด `.env` ที่รากโปรเจกต์ถ้ามี (ค่าที่รากจะทับค่าซ้ำ)  
โค้ด deploy จาก GitHub **ไม่ทับ** ไฟล์นี้

## `acc_local_test.json` (dev / UAT)

ไฟล์นี้ใช้ได้ **สองอย่าง** (ไม่ต้องตั้ง env แยกสำหรับสิทธิ์ส่ง Target Sun):

1. **จำลอง ACC_USER_CONTROL** — เมื่อเปิด `ALLOCATION_ALLOW_ACC_DEV_JSON=1` และชี้ `ACC_USER_CONTROL_DEV_JSON=config/acc_local_test.json`
2. **Allowlist ส่ง Target Sun** — อีเมลในไฟล์นี้ (และ admin ใน `ALLOCATION_ADMIN_EMAILS`) เท่านั้นที่กด **ส่งเข้า Target Sun** ได้

รูปแบบ:

```json
[
  { "email": "user@sahapat.co.th", "userpl": "SL330" }
]
```

เพิ่ม/ลดผู้ทดสอบ → แก้ JSON → รีสตาร์ท backend → รีเฟรชหน้าเว็บ

## ความปลอดภัย

ไฟล์ `.env` มี **client secret** — เก็บเฉพาะบน server; หมุน secret เมื่อมีการรั่วหรือย้าย environment

`acc_local_test.json` ไม่ควร commit อีเมล production จริงถ้า repo เปิดกว้าง — ใช้เฉพาะช่วงทดสอบ
