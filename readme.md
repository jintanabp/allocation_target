# 📦 Target Allocation Dashboard

ระบบกระจายเป้ายอดขาย (หีบ) ให้พนักงานขายรายคน  
ดึงข้อมูลจาก Microsoft Fabric · คำนวณด้วย OR Engine · Export เป็น Excel

---

## โครงสร้างโฟลเดอร์ (v3)

```
allocation_target/
├── frontend/
│   ├── index.html          # หน้า Dashboard (เปิดใน browser)
│   ├── app.js              # Logic ฝั่ง frontend ทั้งหมด
│   └── style.css           # สไตล์ UI
│
├── backend/
│   ├── main.py             # FastAPI backend — API endpoints
│   ├── OR_engine.py        # เครื่องมือกระจายหีบ (L3M / L6M / EVEN / PUSH / LP)
│   ├── generate_excel.py   # สร้างไฟล์ Excel สรุปผล
│   └── fabric_dax_connector.py # เชื่อมต่อ Microsoft Fabric ผ่าน DAX
│
├── scripts/
│   ├── setup.bat                    # ติดตั้ง environment conda (รันครั้งแรกครั้งเดียว)
│   ├── start_server.bat             # เริ่ม server (แบบ conda)
│   ├── build_portable_runtime.bat   # สร้าง Python ใน runtime\ สำหรับแจกจ่าย (ไม่ต้องลง Python)
│   └── build_portable_runtime.ps1   # เรียกโดย build_portable_runtime.bat
│
├── data/                   # โฟลเดอร์ข้อมูล (สร้างอัตโนมัติ — ไม่ขึ้น Git)
│   ├── target_boxes.csv
│   ├── target_sun.csv
│   ├── app.log
│   └── ...                 # cache files (ลบอัตโนมัติทุก 7 วัน)
│
├── requirements.txt
├── requirements-dev.txt
├── Run_Local.bat           # รัน server + เปิดเบราว์เซอร์ (ใช้ runtime\ หรือ .venv)
└── readme.md
```

---

## การติดตั้ง (ครั้งแรก)

### สิ่งที่ต้องมีก่อน

| โปรแกรม | ดาวน์โหลด | หมายเหตุ |
|---------|-----------|---------|
| Miniconda | https://docs.conda.io/en/latest/miniconda.html | เลือก Windows 64-bit |
| Git | https://git-scm.com | สำหรับดึงโค้ดจาก GitHub |

> ⚠️ ตอนติดตั้ง Miniconda ต้องติ๊ก **"Add Miniconda3 to PATH environment variable"** ด้วย  
> (ค่า default ไม่ติ๊กให้ — ถ้าลืมติ๊ก `.bat` จะหา conda ไม่เจอ)

---

### ขั้นตอนติดตั้ง

**1. ดึงโค้ดจาก GitHub**

```bash
git clone https://github.com/<username>/<repo-name>.git
cd <repo-name>
```

**2. รัน setup (ครั้งเดียว)**

ดับเบิลคลิก **`scripts\setup.bat`**  
ระบบจะสร้าง conda environment `allocation_env` และติดตั้ง packages ให้อัตโนมัติ

> ถ้า `scripts\setup.bat` แจ้งว่าหา Miniconda ไม่เจอ → ให้รันด้วยมือใน Anaconda Prompt แทน:
> ```bash
> conda create -n allocation_env python=3.11 -y
> conda activate allocation_env
> pip install -r requirements.txt
> ```

---

## การใช้งาน

### เริ่ม Server

ดับเบิลคลิก **`scripts\start_server.bat`** — หรือรันด้วยมือ:

```bash
conda activate allocation_env
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### แบบ portable (แจกให้คนอื่นโดยไม่ต้องลง Python)

1. บนเครื่องผู้ดูแล (มีอินเทอร์เน็ต, Windows 64-bit): รัน **`scripts\build_portable_runtime.bat`** ครั้งหนึ่ง  
2. Zip ทั้งโฟลเดอร์โปรเจกต์ **รวมโฟลเดอร์ `runtime\`** แล้วส่งให้ผู้ใช้  
3. ผู้ใช้แตก zip แล้วดับเบิลคลิก **`Run_Local.bat`** — เปิด **http://127.0.0.1:8000/**

โฟลเดอร์ **`runtime\`** ไม่ขึ้น Git; ถ้า clone ใหม่ต้องสร้าง portable ใหม่หรือใช้ conda / `.venv` ตามด้านบน

### เปิด Dashboard

หลังรัน server แล้ว เปิด **http://127.0.0.1:8000/** ใน browser (แนะนำ Chrome)  
หรือใช้ `Run_Local.bat` จะเปิด URL นี้ให้อัตโนมัติ

> Server ต้องรันอยู่ก่อนเสมอ — ถ้าเปิด Dashboard แล้วขึ้น error ให้เช็คว่า `Run_Local.bat` หรือ `scripts\start_server.bat` ทำงานอยู่

---

## การอัปโหลดข้อมูลเป้าหมาย (Excel Upload)

**ก่อนเข้า Dashboard ทุกเดือน** supervisor ต้องอัปโหลดข้อมูลเป้าหมายผ่านหน้า Login

### ดาวน์โหลด Template

กดลิงก์ **"⬇ ดาวน์โหลด Template"** ในหน้า Login — จะได้ไฟล์ `Target_Upload_Template.xlsx`  
ที่มี **2 sheet** พร้อม header ถูกต้องและตัวอย่างข้อมูล

### รูปแบบข้อมูล

**Sheet `target_boxes`** — เป้าหีบรายแบรนด์ (คอลัมน์ที่ต้องมี: สีน้ำเงินเข้ม)

| คอลัมน์ | จำเป็น | คำอธิบาย |
|---------|--------|----------|
| `sku` | ✅ | รหัสสินค้า |
| `price_per_box` | ✅ | ราคาต่อหีบ (บาท) ต้อง > 0 |
| `supervisor_target_boxes` | ✅ | เป้าหีบรวมทั้งทีม |
| `brand_name_thai` | - | ชื่อแบรนด์ภาษาไทย |
| `brand_name_english` | - | ชื่อแบรนด์ภาษาอังกฤษ |
| `product_name_thai` | - | ชื่อสินค้าภาษาไทย |

**Sheet `target_sun`** — เป้าเงินรายพนักงาน

| คอลัมน์ | จำเป็น | คำอธิบาย |
|---------|--------|----------|
| `emp_id` | ✅ | รหัสพนักงาน |
| `target_sun` | ✅ | เป้าเงินตั้งต้น (บาท) |

> ⚠️ ยอดรวม `target_sun` ทุกคนควรเท่ากับ `price_per_box × supervisor_target_boxes` รวมทุก SKU

### วิธีอัปโหลด

| วิธี | ขั้นตอน |
|-----|---------|
| **ไฟล์เดียว 2 sheet** | อัปโหลดที่ช่อง "เป้าหีบ SKU" — ระบบอ่านทั้งคู่อัตโนมัติ |
| **แยก 2 ไฟล์** | อัปโหลดคนละช่อง |
| **Drag & Drop** | ลากไฟล์มาวางบนกล่องอัปโหลดได้เลย |

หลังอัปโหลดสำเร็จ ระบบจะแสดงสรุป เช่น `SKU 6 รายการ · มูลค่ารวม 1,250,000 บาท`

---

## ขั้นตอนการใช้งาน Dashboard

```
[Login] เลือก Supervisor + เดือน/ปี + อัปโหลด Excel เป้าหมาย
        ↓
[Step 1] ตรวจสอบข้อมูลพนักงาน + SKU ที่ดึงจาก Fabric
        ↓
[Step 2] ปรับเป้าเงินรายพนักงาน (ยอดรวมต้องตรงกับเป้ารวม)
        ↓
[Step 3] เลือก Strategy แล้วกด "กระจายหีบ"
        ↓
[Step 4] ตรวจสอบผล / แก้หีบด้วยมือ (ระบบเกลี่ยส่วนต่างให้อัตโนมัติ)
        ↓
[Export] ดาวน์โหลด Excel สรุปผล
```

### Strategy ที่มีให้เลือก

| Strategy | ใช้เมื่อ |
|----------|---------|
| **L3M** | กระจายตามยอดขายเฉลี่ย 3 เดือนล่าสุด (แนะนำ) |
| **L6M** | กระจายตามยอดขายเฉลี่ย 6 เดือน (เรียบกว่า) |
| **EVEN** | เกลี่ยเท่ากันทุกคน |
| **PUSH** | ผลักดันคนขายน้อย (ให้หีบมากกว่าปกติ) |
| **LP** | Linear Programming ตามเป้าเงิน (แม่นยำสุด แต่ช้ากว่า) |

---

## API Endpoints

| Method | Path | หน้าที่ |
|--------|------|---------|
| `GET` | `/data/employees` | ดึงพนักงาน + SKU + ประวัติจาก Fabric |
| `POST` | `/optimize` | คำนวณกระจายหีบตาม strategy |
| `POST` | `/upload/targets` | อัปโหลด Excel เป้าหมาย (boxes/sun/both) |
| `GET` | `/upload/template` | ดาวน์โหลด Excel template |
| `POST` | `/export/excel` | สร้างไฟล์ Excel สรุปผล |
| `GET` | `/download/excel` | ดาวน์โหลด Excel ที่สร้างแล้ว |
| `GET` | `/managers` | ดึงรายชื่อ Supervisor ทั้งหมด |
| `GET` | `/health` | ตรวจสอบสถานะ server |
| `GET` | `/debug/fabric` | debug การเชื่อมต่อ Fabric |

Swagger UI: http://localhost:8000/docs

---

## การอัปเดตโค้ด

```bash
git pull
```

ไม่ต้องรัน `scripts\setup.bat` ใหม่ — รัน `Run_Local.bat` หรือ `scripts\start_server.bat` ได้เลย

---

## แก้ปัญหาเบื้องต้น

| อาการ | สาเหตุ | วิธีแก้ |
|-------|--------|---------|
| `conda is not recognized` | ยังไม่ได้ลง Miniconda หรือไม่ได้ติ๊ก Add to PATH | ติดตั้ง Miniconda ใหม่ ติ๊ก Add to PATH |
| Dashboard ขึ้น error เชื่อมต่อ | Server ไม่ได้รัน | เปิด `Run_Local.bat` หรือ `scripts\start_server.bat` ก่อน |
| อัปโหลด Excel แล้วขึ้น error คอลัมน์ | ชื่อ header ไม่ตรง | ดาวน์โหลด Template แล้วกรอกข้อมูลตาม |
| หน้าเว็บแสดงผลไม่ครบ | ข้อมูลจาก Fabric ดึงไม่ได้ | เช็ค log ที่ `data/app.log` |
| ติดตั้งช้ามาก | conda กำลัง solve dependencies | รอได้เลย อาจใช้เวลา 5-10 นาที |

Log file อยู่ที่ `data/app.log` — เปิดดูได้ทุกเมื่อ

---

## เพิ่ม Supervisor ใหม่

เปิดไฟล์ `backend/main.py` หา `KNOWN_MANAGERS` (หรือตั้ง env `KNOWN_MANAGERS`) แล้วเพิ่มรหัสเข้าไป:

```python
KNOWN_MANAGERS = ["SL330", "SL374", "SL999", "SL001"]  # เพิ่มตรงนี้
```

แล้ว restart server ใหม่

---

*Target Allocation Dashboard v3 · Python 3.11 · FastAPI · Microsoft Fabric*