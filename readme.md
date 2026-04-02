# Target Allocation System v3 — Setup & Guide

## การติดตั้ง

```bash
pip install fastapi uvicorn pandas msal requests openpyxl pulp
```

## โครงสร้างไฟล์

```
├── main.py                    ← FastAPI backend (v3)
├── fabric_dax_connector.py   ← Power BI DAX connector
├── OR_engine.py               ← 5 strategies: L3M / L6M / EVEN / PUSH / LP
├── generate_excel.py          ← Excel export (รองรับ filter แบรนด์)
├── generate_dummy_targets.py  ← สร้าง dummy CSV
├── index.html                 ← Frontend
├── app.js                     ← Frontend logic (v3)
├── style.css                  ← Styling (v3)
└── data/
    ├── target_boxes.csv       ← [ต้องสร้าง] SKU + เป้าหีบ + brand
    ├── target_sun.csv         ← [ต้องสร้าง] เป้าบาทรายพนักงาน
    ├── hist_cache_SL330_*.csv ← [auto] cache per supervisor per month
    ├── emp_cache_SL330.csv    ← [auto] cache รายชื่อพนักงาน
    ├── final_allocation_SL330.csv  ← [auto] ผลล่าสุด
    ├── Final_Dashboard_SL330.xlsx  ← [auto] Excel output per supervisor
    └── app.log                ← [auto] structured log
```

## วิธีรัน

### 1. สร้าง dummy targets (ครั้งแรก)

**Offline (ทดสอบ):**
```bash
python generate_dummy_targets.py --manager SL330 --month 4 --year 2026 --offline
python generate_dummy_targets.py --manager SL374 --month 4 --year 2026 --offline --seed 99
```

**Online (จาก Fabric):**
```bash
python generate_dummy_targets.py --manager SL330 --month 4 --year 2026
```

### 2. รัน backend
```bash
uvicorn main:app --reload
```

### 3. เปิด frontend
เปิด `index.html` ในเบราว์เซอร์ (หรือ Live Server ใน VS Code)

---

## Features v3

### วิธีกระจายหีบ (เลือกได้ใน UI)
| Strategy | คำอธิบาย | ความเร็ว |
|---|---|---|
| L3M | สัดส่วนตามยอดขาย 3 เดือนล่าสุด (default) | ⚡ เร็ว |
| L6M | สัดส่วนตามยอดขาย 6 เดือนล่าสุด | ⚡ เร็ว |
| EVEN | เกลี่ยเท่ากันทุกคน | ⚡ เร็ว |
| PUSH | ผลักดันคนขายน้อย (inverse ratio) | ⚡ เร็ว |
| LP | AI Revenue Balance ตาม yellow target | 🐌 20-90 วินาที |

### UI Features
- **Brand filter tabs** — scroll แนวนอน ไม่ wrap
- **Sticky totals** — คอลัมน์ "รวมหีบ" และ "มูลค่ารวม" ค้างอยู่ขวามือเสมอ (ALL brand)
- **Deviation highlight** — สีเขียว = ±1,000 บาทจากเป้าเหลือง / สีเหลือง = เกิน ±1,000 บาท
- **Export modal** — เลือกได้ว่าจะ export แบรนด์ไหน หรือทั้งหมด
- **Editable cells** — คลิกตัวเลขสีส้มในตารางผลลัพธ์แก้ไขได้ทันที (upsert safe)

### Security
- `sup_id` ถูก sanitize ก่อนใส่ใน filename (ป้องกัน path traversal)
- CORS configurable ผ่าน `CORS_ORIGINS` env variable
- Cache เก่ากว่า 7 วันถูก cleanup อัตโนมัติ

---

## API Endpoints

| Method | Path | คำอธิบาย |
|---|---|---|
| GET | `/health` | เช็คสถานะ |
| GET | `/managers` | list SuperCode ที่รองรับ |
| GET | `/data/employees?sup_id=SL330&target_month=4&target_year=2026` | ดึงข้อมูล dashboard |
| GET | `/data/employees?...&regen_target=true` | force regenerate targets |
| POST | `/optimize?sup_id=SL330&target_month=4&target_year=2026` | รัน OR optimization |
| POST | `/export/excel?sup_id=SL330` | สร้าง Excel (รับ brand_filter) |
| GET | `/download/excel?sup_id=SL330` | ดาวน์โหลด Excel |

### ตัวอย่าง POST /optimize payload
```json
{
  "yellowTargets": [
    {"emp_id": "S001", "yellow_target": 85000.00},
    {"emp_id": "S002", "yellow_target": 72000.00}
  ],
  "strategy": "L3M"
}
```

### ตัวอย่าง POST /export/excel payload
```json
{
  "allocations": [...],
  "brand_filter": "เบียร์ช้าง",
  "yellow_targets": [
    {"emp_id": "S001", "yellow_target": 85000.00}
  ]
}
```

---

## Production Deployment

```bash
# ตั้งค่า CORS สำหรับ production
export CORS_ORIGINS="https://your-domain.com,http://localhost:5500"

# รัน production server
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2

# หรือด้วย gunicorn
pip install gunicorn
gunicorn main:app -k uvicorn.workers.UvicornWorker --workers 2 --bind 0.0.0.0:8000
```

---

## ข้อมูลที่ดึงจาก Fabric

| ข้อมูล | Table | Filter |
|---|---|---|
| รายชื่อพนักงาน | `dim_salesman` | `SuperCode = "SL330"` |
| SKU ที่เคยขาย | `cross_sold_history_2y_qu` | emp + 6 เดือนล่าสุด |
| ข้อมูลสินค้า | `dim_product` | SKU list |
| ยอดขาย 3 เดือน | `cross_sold_history_2y_qu` | emp + sku + month/year |
| ยอดขายปีก่อน | `cross_sold_history_2y_qu` | emp + sku + same month LY |
| Warehouse | `cross_sold_history_2y_qu` | emp |