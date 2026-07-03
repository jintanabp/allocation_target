# Target Sun Read API — Specification (Draft)

Read API อ่านเป้าหีบจากตาราง `TGA_TARGET_SALESMAN_NEXT`  

**อ้างอิง Write API ที่มีอยู่แล้ว**

| รายการ | ค่า |
|--------|-----|
| Endpoint | `POST /spc/targetsun/importTargetSalesmanNextFromExcel` |
| UAT | `https://spcuatws.sahapat.com/spc/targetsun/importTargetSalesmanNextFromExcel` |
| Prod | `https://spcws.sahapat.com/spc/targetsun/importTargetSalesmanNextFromExcel` |
| ตารางปลายทาง | `TGA_TARGET_SALESMAN_NEXT` |
| เอกสาร import | `targetsun-importTargetSalesmanNextFromExcel.md` |

**การตั้งค่า URL ในแอป allocation_target:** ไม่ใช้ `.env` — ดู `backend/services/targetsun_endpoints.py` และ runtime preset ใน `config/app_runtime.json` (แอดมิน → แท็บแหล่งข้อมูล)

ต้องการอ่านเป้าหีบงวดถัดไปจาก Target Sun (Oracle) โดยตรง แทน query ผ่าน Semantic Model เพื่อให้:

1. เป้าที่แสดงตอนกระจาย = เป้าใน Target Sun (ตรงกับที่ส่งกลับหลังกระจาย)
2. ลดปัญหา semantic model sync ช้า / ไม่ตรงกับ Oracle
3. ใช้ grain และ business key เดียวกับ Excel import ที่มีอยู่

### เรียก API เมื่อ

- ตอน user เปิด **Step 1** / เลือกงวด / กด refresh
- แอป cache ผล ~15 นาที ไม่ยิง API ทุกคลิก

---

## 2. Authentication

| รายการ | ค่า (IT UAT spec) |
|--------|-------------------|
| วิธี | **ไม่มี** — เรียกจาก backend เท่านั้น |
| Header | `TARGETSUN_READ_AUTH_HEADER` (optional เผื่อ production) |

**Environment (ฝั่ง web app allocation)**

| Variable | details |
|----------|----------|
| `TARGETSUN_READ_API_BASE` | Base URL เช่น `https://spcuatws.sahapat.com/spc/targetsun` |
| `TARGETSUN_READ_AUTH_HEADER` | ค่า Authorization header (optional) |

---

## 3. Endpoint 1 — งวดเป้าล่าสุด

ใช้ตรวจว่างวดที่ user เลืออยู่ในช่วงที่ระบบมีเป้าหรือยัง  
แทน `get_tga_max_effective_raw()` ในแอปปัจจุบัน

| รายการ | ค่า |
|--------|-----|
| **Method** | `GET` |
| **Path** | `/targetSalesmanNext/maxEffectiveDate` |
| **Full URL (UAT ตัวอย่าง)** | `https://spcuatws.sahapat.com/spc/targetsun/targetSalesmanNext/maxEffectiveDate` |

### Query parameters

| Parameter | Required | Type | คำอธิบาย |
|-----------|----------|------|----------|
| `divisionCode` | No | string (1 char) | กรองตาม division เช่น `B`, `E`, `S` |
| `salesType` | No | string (1 char) | `0` = credit, `1` = van |

### Response — สำเร็จ (HTTP 200)

```json
{
  "success": true,
  "result": {
    "maxEffectiveDate": "2026-06-01",
    "maxEffectiveDateTh": "1/6/2569",
    "maxUpdatedDate": "2026-05-28T14:30:00",
    "impliedTargetYear": 2026,
    "impliedTargetMonth": 6,
    "source": "EFFECTIVEDATE"
  },
  "resultMsg": "ok"
}
```

| Field | คำอธิบาย |
|-------|----------|
| `maxEffectiveDate` | `MAX(EFFECTIVEDATE)` จาก `TGA_TARGET_SALESMAN_NEXT` (ISO date CE) |
| `maxEffectiveDateTh` | รูปแบบไทย `d/m/25xx` (optional) |
| `maxUpdatedDate` | fallback เมื่อ `EFFECTIVEDATE` ว่างทั้งตาราง → `MAX(UPDATEDATE)` |
| `impliedTargetYear` | ปี ค.ศ. ของงวดเป้าที่ UI ควรเลือกได้ |
| `impliedTargetMonth` | เดือน `1`–`12` |
| `source` | `"EFFECTIVEDATE"` หรือ `"UPDATEDATE"` |

### Response — ไม่มีข้อมูล (HTTP 200)

```json
{
  "success": true,
  "result": {
    "maxEffectiveDate": null,
    "maxUpdatedDate": null,
    "impliedTargetYear": null,
    "impliedTargetMonth": null,
    "source": null
  },
  "resultMsg": "no data"
}
```

---

## 4. Endpoint 2 — Query เป้า granular (หลัก)

ดึงเป้าหีบตาม **งวด + รายชื่อพนักงาน** — **ห้ามคืนข้อมูลทั้งตาราง**  
แทน `get_tga_target_salesman_granular()` ในแอปปัจจุบัน

| รายการ | ค่า |
|--------|-----|
| **Method** | `POST` (แนะนำ — salesman list อาจยาว) หรือ `GET` |
| **Path** | `/targetSalesmanNext/query` |
| **Full URL (UAT ตัวอย่าง)** | `https://spcuatws.sahapat.com/spc/targetsun/targetSalesmanNext/query` |
| **Content-Type** | `application/json` |

### Request body

```json
{
  "targetYear": 2026,
  "targetMonth": 6,
  "salesmanCodes": ["12345", "12346", "12347"],
  "includeZeroQuantity": true,
  "filterByEffectiveDate": true
}
```

| Field | Required | Type | Default | คำอธิบาย |
|-------|----------|------|---------|----------|
| `targetYear` | **Yes** | integer | — | ปี ค.ศ. (CE) เช่น `2026` |
| `targetMonth` | **Yes** | integer | — | เดือน `1`–`12` |
| `salesmanCodes` | **Yes** | string[] | — | รหัสพนักงาน 5 หลัก — **filter บังคับฝั่ง server** |
| `includeZeroQuantity` | No | boolean | `true` | รวมแถว `QUANTITYCASE = 0` (ใช้ตอนส่งกลับ Target Sun) |
| `filterByEffectiveDate` | No | boolean | `true` | กรอง `YEAR/MONTH(EFFECTIVEDATE)` ตามงวด |

### Request body — ทางเลือก (optional)

ถ้า Target Sun map Supervisor → Salesman ได้:

```json
{
  "supervisorCode": "SL330",
  "targetYear": 2026,
  "targetMonth": 6,
  "includeZeroQuantity": true,
  "filterByEffectiveDate": true
}
```

| Field | Required | คำอธิบาย |
|-------|----------|----------|
| `supervisorCode` | No | รหัส Supervisor เช่น `SL330` — ทางเลือกแทน `salesmanCodes` |

> **หมายเหตุ:** แอปเรามี `salesmanCodes` จาก `Dim_Salesman` อยู่แล้ว — `supervisorCode` เป็น convenience ไม่บังคับ

### Response — สำเร็จ (HTTP 200)

```json
{
  "success": true,
  "result": {
    "targetYear": 2026,
    "targetMonth": 6,
    "rowCount": 2,
    "totalQuantityCase": 50,
    "rows": [
      {
        "PRODUCTCODE": "123456",
        "SALESTYPE": "0",
        "DIVISIONCODE": "B",
        "SALESMANCODE": "12345",
        "AREACODE": "1",
        "PROVINCECODE": "10",
        "WAREHOUSECODE": "1001",
        "QUANTITYCASE": 50,
        "EFFECTIVEDATE": "1/6/2569",
        "UPDATEDATE": "28/5/2569"
      },
      {
        "PRODUCTCODE": "123457",
        "SALESTYPE": "0",
        "DIVISIONCODE": "B",
        "SALESMANCODE": "12345",
        "AREACODE": "1",
        "PROVINCECODE": "10",
        "WAREHOUSECODE": "1001",
        "QUANTITYCASE": 0,
        "EFFECTIVEDATE": "1/6/2569",
        "UPDATEDATE": "28/5/2569"
      }
    ]
  },
  "resultMsg": "ok"
}
```

### Response — ไม่มีเป้าในงวด (HTTP 200, rows ว่าง)

```json
{
  "success": true,
  "result": {
    "targetYear": 2026,
    "targetMonth": 6,
    "rowCount": 0,
    "totalQuantityCase": 0,
    "rows": []
  },
  "resultMsg": "ok"
}
```

แอปจะแสดงข้อความ "ยังไม่มีเป้างวดนี้" (HTTP 409 ฝั่งแอป)

---

## 5. โครงสร้างฟิลด์ (Row)

ตรงกับ Excel import (`importTargetSalesmanNextFromExcel`)

| Column | Field | Required | Length | หมายเหตุ |
|--------|-------|----------|--------|----------|
| A | `PRODUCTCODE` | Yes | 6 | รหัสสินค้า |
| B | `SALESTYPE` | Yes | 1 | `0` = credit, `1` = van |
| C | `DIVISIONCODE` | Yes | 1 | เช่น `B`, `E`, `S` |
| D | `SALESMANCODE` | Yes | 5 | รหัสพนักงาน |
| E | `AREACODE` | Yes | 1 | |
| F | `PROVINCECODE` | Yes | | |
| G | `WAREHOUSECODE` | No | 4 | อนุญาต null / ว่าง |
| H | `QUANTITYCASE` | Yes | numeric | จำนวนหีบ (integer) |
| I | `EFFECTIVEDATE` | Yes | date | รองรับ `d/m/Y`, พ.ศ. `25xx`, Excel serial |
| J | `UPDATEDATE` | No | datetime | optional |

### Business key (insert/update ตอน import)

```
PRODUCTCODE + SALESTYPE + DIVISIONCODE + SALESMANCODE + AREACODE + PROVINCECODE
```

`WAREHOUSECODE` แยกบรรทัดได้ แต่ไม่ใช่ส่วนหนึ่งของ duplicate key ตอน import

### Mapping ฝั่งแอป Target Allocation

| API field | แอปใช้ชื่อ |
|-----------|-----------|
| `SALESMANCODE` | `emp_id` |
| `PRODUCTCODE` | `sku` |
| `QUANTITYCASE` | `qty` |
| `SALESTYPE` | `salestype` |
| `DIVISIONCODE` | `divisioncode` |
| `AREACODE` | `areacode` |
| `PROVINCECODE` | `provincecode` |
| `WAREHOUSECODE` | `warehouse_code` |

*-----------------------------------------------
### การ aggregate ฝั่งแอป (ไม่ต้องทำใน API)

- **แสดงหน้าจอ Step 1:** `SUM(QUANTITYCASE)` ต่อ `SALESMANCODE × PRODUCTCODE`
- **ส่งกลับ Target Sun:** ใช้แถว granular เต็ม (รวม `QUANTITYCASE = 0`)

---

## 6. Filtering — สำคัญ

### ต้อง filter ฝั่ง server

แอปโหลด **ทีละทีม Supervisor** (~5–15 พนักงาน) ไม่ใช่ทั้งองค์กร

| สถานการณ์ | วิธีเรียก |
|-----------|----------|
| Supervisor เลือก SL330 | `salesmanCodes` = พนักงาน 8 คนใต้ SL330 |
| Manager ดู 3 ทีม | 3 request (ทีมละ SL) หรือ 1 request รวม `salesmanCodes` ~30 คน |

### ห้าม

- คืนข้อมูลทั้งตาราง / ทั้ง division โดยไม่มี filter
- ให้ client filter เองหลังดึงทั้งก้อน

### ขนาดโดยประมาณ

| ขอบเขต | แถวโดยประมาณ |
|--------|-------------|
| ทีม SL หนึ่ง (~10 คน × ~300 SKU × grain) | หลักพัน–หมื่น |
| ทั้งบริษัท | หลักแสน–ล้าน — **ไม่รับ** |

---

## 7. Pagination & Limits (แนะนำ)

| รายการ | คำแนะนำ |
|--------|---------|
| Max `salesmanCodes` ต่อ request | 200 codes |
| Max rows ต่อ response | 50,000 แถว |
| Pagination | optional — ถ้าเกิน limit |

### Response pagination (optional)

```json
{
  "result": {
    "rows": [],
    "page": 1,
    "pageSize": 5000,
    "totalRows": 12000,
    "hasMore": true
  }
}
```

---

## 8. Error responses

รูปแบบเดียวกับ import API (`success: false`)

| HTTP | `resultMsg` (ตัวอย่าง) | สาเหตุ |
|------|----------------------|--------|
| 400 | `targetYear is required` | ขาดพารามิเตอร์บังคับ |
| 400 | `salesmanCodes is required` | ไม่ส่งรายชื่อพนักงาน |
| 400 | `salesmanCodes exceeds limit (200)` | เกิน max ต่อ request |
| 401 | `Unauthorized` | Auth ไม่ผ่าน |
| 403 | `Forbidden` | ไม่มีสิทธิ |
| 500 | `Internal server error` | Server error |

```json
{
  "success": false,
  "result": null,
  "resultMsg": "salesmanCodes is required"
}
```

---

## 9. Performance

| รายการ | เป้า |
|--------|------|
| Latency ต่อทีม (UAT) | < 5 วินาที |
| Cache ฝั่งแอป | TTL ~900 วินาที (15 นาที) |
| ความถี่เรียก | ตอน cache miss / refresh เท่านั้น |

---

## 10. Acceptance criteria

1. Query งวด มิ.ย. 2569 + `salesmanCodes` ของทีม SL330 → ผลตรงกับ  
   `SELECT * FROM TGA_TARGET_SALESMAN_NEXT WHERE SALESMANCODE IN (...) AND YEAR(EFFECTIVEDATE)=2026 AND MONTH(EFFECTIVEDATE)=6`
2. Grain ตรงกับ Excel import (คอลัมน์ A–I)
3. `includeZeroQuantity=true` คืนแถว `QUANTITYCASE=0`
4. `maxEffectiveDate` ตรงกับ `MAX(EFFECTIVEDATE)` ในตาราง
5. รองรับวันที่แบบ พ.ศ. (`1/6/2569`) เหมือน import API
6. **ไม่คืนข้อมูลทั้งตาราง** เมื่อส่ง `salesmanCodes` หรือ `supervisorCode`
7. มี UAT + Prod endpoint + เอกสาร error
8. Auth เดียวกับ import API

---

## 11. cURL ตัวอย่าง

### maxEffectiveDate

```bash
curl -s -X GET \
  "https://spcuatws.sahapat.com/spc/targetsun/targetSalesmanNext/maxEffectiveDate" \
  -H "Authorization: Bearer <token>"
```

### query

```bash
curl -s -X POST \
  "https://spcuatws.sahapat.com/spc/targetsun/targetSalesmanNext/query" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "targetYear": 2026,
    "targetMonth": 6,
    "salesmanCodes": ["12345", "12346"],
    "includeZeroQuantity": true,
    "filterByEffectiveDate": true
  }'
```