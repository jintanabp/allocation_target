# 📦 Target Allocation Dashboard

ระบบกระจายเป้ายอดขาย (หีบ) ให้พนักงานขายรายคน  
ดึงข้อมูลจาก Microsoft Fabric · คำนวณด้วย OR Engine · Export เป็น Excel

---

## โครงสร้างไฟล์

```
allocation_target/
├── index.html              # หน้า Dashboard (เปิดใน browser)
├── app.js                  # Logic ฝั่ง frontend ทั้งหมด
├── style.css               # สไตล์ UI
├── main.py                 # FastAPI backend — API endpoints
├── OR_engine.py            # เครื่องมือกระจายหีบ (L3M / L6M / EVEN / PUSH / LP)
├── generate_excel.py       # สร้างไฟล์ Excel สรุปผล
├── fabric_dax_connector.py # เชื่อมต่อ Microsoft Fabric ผ่าน DAX
│
├── data/                   # โฟลเดอร์ข้อมูล (สร้างอัตโนมัติ)
│   ├── target_boxes.csv    # เป้าหีบรายแบรนด์ (แก้ได้เอง หรือ generate อัตโนมัติ)
│   ├── target_sun.csv      # เป้าเงินรายพนักงานตั้งต้น
│   ├── app.log             # log การทำงาน
│   └── ...                 # cache files (ลบอัตโนมัติทุก 7 วัน)
│
├── requirements.txt        # Python packages ที่ต้องใช้
├── setup.bat               # ติดตั้ง environment (รันครั้งแรกครั้งเดียว)
└── start_server.bat        # เริ่ม server (รันทุกครั้งที่จะใช้งาน)
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

เปิด Command Prompt หรือ Terminal แล้วรัน:

```bash
git clone https://github.com/<username>/<repo-name>.git
cd <repo-name>
```

**2. รัน setup.bat (ครั้งเดียว)**

ดับเบิลคลิกไฟล์ `setup.bat` — ระบบจะ:
- สร้าง conda environment ชื่อ `allocation_env`
- ติดตั้ง Python packages ทั้งหมดจาก `requirements.txt` อัตโนมัติ

> ถ้า setup.bat แจ้งว่าหา Miniconda ไม่เจอ → ให้รันด้วยมือใน Anaconda Prompt แทน:
> ```bash
> conda create -n allocation_env python=3.11 -y
> conda activate allocation_env
> pip install -r requirements.txt
> ```

---

## การใช้งาน

### เริ่ม Server

ดับเบิลคลิก `start_server.bat` ทุกครั้งที่จะใช้งาน

หรือรันด้วยมือ:
```bash
conda activate allocation_env
uvicorn main:app --host 127.0.0.1 --port 8000
```

### เปิด Dashboard

เปิดไฟล์ `index.html` ในเบราว์เซอร์โดยตรง (Chrome แนะนำ)

> Server ต้องรันอยู่ก่อนเสมอ ถ้าเปิด Dashboard แล้วขึ้น error ให้เช็คว่า `start_server.bat` ทำงานอยู่

---

## วิธีใช้งาน Dashboard

```
[1] เลือก Supervisor + เดือน/ปี → เข้าสู่ระบบ
        ↓
[2] ตั้งเป้าเงินรายพนักงาน (ปรับได้ — ยอดรวมต้องตรงกับเป้ารวม)
        ↓
[3] เลือก Strategy แล้วกด "กระจายหีบ"
        ↓
[4] ตรวจสอบผล / แก้หีบด้วยมือ (ระบบเกลี่ยส่วนต่างให้อัตโนมัติ)
        ↓
[5] Export Excel
```

### Strategy ที่มีให้เลือก

| Strategy | ใช้เมื่อ |
|----------|---------|
| **L3M** | กระจายตามยอดขายเฉลี่ย 3 เดือนล่าสุด (แนะนำ) |
| **L6M** | กระจายตามยอดขายเฉลี่ย 6 เดือน (เรียบกว่า) |
| **EVEN** | เกลี่ยเท่ากันทุกคน |
| **PUSH** | ผลักดันคนขายน้อย (ให้หีบมากกว่าปกติ) |
| **LP** | Linear Programming ตามเป้าเงิน (แม่นยำสุด แต่ช้า) |

---

## ไฟล์ข้อมูลที่ต้องเตรียม

### `data/target_boxes.csv` — เป้าหีบรายแบรนด์

```csv
sku,price_per_box,supervisor_target_boxes,brand_name_thai,brand_name_english,product_name_thai
624007,240.00,150,แบรนด์ A,Brand A,สินค้า A
624015,212.00,80,แบรนด์ B,Brand B,สินค้า B
```

> ถ้าไม่มีไฟล์นี้ ระบบจะ generate ค่าตัวอย่างจากประวัติ Fabric ให้อัตโนมัติ

### `data/target_sun.csv` — เป้าเงินตั้งต้นรายพนักงาน

```csv
emp_id,target_sun
EMP001,125000.00
EMP002,98000.00
```

> ยอดรวม `target_sun` ทุกคนต้องเท่ากับ `price_per_box × supervisor_target_boxes` รวมทุก SKU

---

## API Endpoints

| Method | Path | หน้าที่ |
|--------|------|---------|
| `GET` | `/data/employees` | ดึงพนักงาน + SKU + ประวัติจาก Fabric |
| `POST` | `/optimize` | คำนวณกระจายหีบตาม strategy |
| `POST` | `/export/excel` | สร้างไฟล์ Excel |
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

ไม่ต้องรัน `setup.bat` ใหม่ — รัน `start_server.bat` ได้เลย

---

## แก้ปัญหาเบื้องต้น

| อาการ | สาเหตุ | วิธีแก้ |
|-------|--------|---------|
| `conda is not recognized` | ยังไม่ได้ลง Miniconda หรือไม่ได้ติ๊ก Add to PATH | ติดตั้ง Miniconda ใหม่ ติ๊ก Add to PATH |
| Dashboard ขึ้น error เชื่อมต่อ | Server ไม่ได้รัน | เปิด `start_server.bat` ก่อน |
| หน้าเว็บแสดงผลไม่ครบ | ข้อมูลจาก Fabric ดึงไม่ได้ | เช็ค log ที่ `data/app.log` |
| ติดตั้งช้ามาก | conda กำลัง solve dependencies | รอได้เลย อาจใช้เวลา 5-10 นาที |

Log file อยู่ที่ `data/app.log` — เปิดดูได้ทุกเมื่อ

---

## เพิ่ม Supervisor ใหม่

เปิดไฟล์ `main.py` หา `KNOWN_MANAGERS` แล้วเพิ่มรหัสเข้าไป:

```python
KNOWN_MANAGERS = ["SL330", "SL374", "SL999", "SL001"]  # เพิ่มตรงนี้
```

แล้ว restart server ใหม่

---

*Target Allocation Dashboard v3 · Python 3.11 · FastAPI · Microsoft Fabric*