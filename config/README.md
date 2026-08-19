# การตั้งค่า (บน server / dev)

## ไฟล์ `.env`

คัดลอก **`config/.env.example` → `config/.env`** แล้วกรอกค่า — backend โหลด `config/.env` ก่อน แล้วโหลด `.env` ที่รากถ้ามี

## สิทธิ์ผู้ใช้ — `user_access.json` + `access_hierarchy.json`

รายชื่อ **อีเมล + รหัส SL (USERPL)** เก็บที่ **`config/user_access.json`**

ลำดับชั้น Manager → Supervisor คำนวณจาก roster (Excel) แล้วเขียนลง **`config/access_hierarchy.json`** และ **`data/managers_cache.json`** — **ไม่ใช้** `trf_select_supervisor` / `ACC_USER_CONTROL` ใน runtime อีกต่อไป

### Workflow อัปเดตสิทธิ์

```bash
python scripts/access/import_user_access_from_division_xlsx.py
python scripts/access/rebuild_access_hierarchy.py
python scripts/access/validate_access_with_dim.py
python scripts/access/repair_user_access.py
# รีสตาร์ท server หลัง rebuild
```

- **นำเข้าจาก Excel:**  
  `python scripts/access/import_user_access_from_division_xlsx.py`  
  (ไฟล์ใน Downloads: `Email และ รหัส SL ผจก.และซุปฯ B,E.xlsx` + `รหัสSL-Mail ทีมขายDiv.S.xlsx`)
- **Deploy ครั้งแรก:** ไฟล์ config อยู่ใน Git — server ได้รายชื่อพร้อมใช้หลัง `git pull`
- **หลัง deploy:** แอดมินแก้ผ่านปุ่ม **「จัดการสิทธิ์」** (อีเมลใน `ALLOCATION_ADMIN_EMAILS`) — การแก้บน server ไม่ถูก commit อัตโนมัติ
- ฟิลด์ `can_import_targetsun` กำหนดใครกด **ส่งเข้า Target Sun** ได้
- ฟิลด์ `login_kind`: `marketing` = เข้าระบบแล้วเห็นแอดมินแท็บ **ทีมพนักงาน**, **ผูกรหัส SL** (ดู), **ผูกรหัส SKU** (ดู) เท่านั้น · `manager_acc` = บังคับบทบาท Manager (ใช้คู่กับ `manager_level` และ `acc_division`/`acc_region`/`acc_scope`)
- ฟิลด์ `manager_level` (เมื่อ `login_kind` = `manager_acc`): `division` = ดูซุปทั้ง division · `regional` = ดูซุปทั้งภาค — **ระบบอนุมาน `acc_scope` อัตโนมัติ** ไม่ต้องตั้งเอง
- ซุป (`supervisor_acc`): ดูซุปทั้งภาคใน division · ถ้ามี `acc_unit` = van/credit จะดูเฉพาะซุปหน่วยเดียวกันในภาค — **แก้เป้า/กระจาย/ส่ง Target Sun ได้ทุกทีมในกลุ่ม** (โหมด「ทั้งภาค」บันทึกแยก SL)
- ฟิลด์ `acc_scope`: `self` = ดูเฉพาะทีมตัวเอง · `region_peers` / `van` / `credit` = เห็นและเขียน Supervisor ใน **division+ภาคเดียวกัน** (กรองหน่วยเมื่อเป็น van/credit)
- ฟิลด์สำคัญ (ทุกแถวมีครบ — ค่าว่างใช้ `none`): `full_name`, `acc_division`, `acc_region`, `acc_unit`, `acc_position`, `login_kind`, `manager_level`, `acc_scope`, `acc_type`, `acc_joblevel`, `visible_supervisor_codes`
- นำเข้า/ซ่อมจาก Excel:

```bash
python scripts/access/import_user_access_from_division_xlsx.py
python scripts/access/repair_user_access.py
```

**กฎ Excel → สิทธิ**

| แหล่ง | เงื่อนไข | ผลลัพธ์ |
|--------|----------|---------|
| Div.S | คอล. E = `Div.S`, F = `All` | `manager_acc` + `manager_level: division` |
| Div.S | E = ภาค (BKK/Central/…), F = `All` | `manager_acc` + `manager_level: regional` |
| Div.S | E = ภาค, F = `Credit All` / `Van All` | `supervisor_acc` + หน่วย credit/van |
| Div.B/E | ตำแหน่งมี ผจก./ผช.ผจก. + ภาค | `manager_acc` + `manager_level: regional` |
| Div.E | ตำแหน่ง ผจก.แผนก Div.E (ไม่มีภาค) | `manager_acc` + `manager_level: division` |
| Div.B/E | ซุป + ภาค (+ เครดิต/รถ ถ้ามี) | `supervisor_acc` |

### กฎสิทธิ์ (Excel roster)

| Division | บทบาท | ดูได้ |
|----------|--------|--------|
| Div.B / Div.E | ผจก./ผช.ผจก. (`manager_acc`) | ซุปทุกคนใน **division + ภาค** เดียวกัน · โหมด「รวมทั้งหมด」ดูอย่างเดียว · กระจายหีบได้เฉพาะ「รายคน」/「รวมภาค」 |
| Div.S | ผจก. division (`manager_acc`, ไม่มีภาค) | ซุปทุกคนใน Div.S ทุกภาค · โหมด「รวมทั้งหมด」ดูอย่างเดียว · กระจายหีบได้เฉพาะ「รายคน」/「รวมภาค」 |
| Div.B / Div.E | ซุป (`supervisor_acc`) + `self` | **รหัส SL ตัวเอง** เท่านั้น |
| Div.B / Div.E | ซุป (`supervisor_acc`) + `region_peers` | ซุปทุกคนใน **division + ภาค** เดียวกัน (ดูรวมทั้งภาคได้ · กระจายหีบได้เฉพาะทีมตัวเอง) |
| Div.S | ขอบเขต `All` + ภูมิภาค `Div.S` | ซุปทุกคนใน Div.S ทุกภาค |
| Div.S | ขอบเขต `All` + ภาคเฉพาะ | ซุปใน Div.S ภาคนั้น |
| Div.S | `Credit All` / `Van All` | รหัส SL ตัวเอง + `acc_unit` |

**Dim_Salesman** ใช้เฉพาะดึงพนักงานใต้ `SuperCode` และ validate (`validate_access_with_dim.py`) — ไม่กำหนดสิทธิ login

ตัวอย่างรูปแบบ: ดู `config/user_access.example.json`

### สิทธิ์ดูแลระบบ — `role` + `admin_scope`

สองฟิลด์นี้**แยกจากตำแหน่งงาน** (`login_kind`) โดยสิ้นเชิง คนหนึ่งจึงเป็นทั้ง
Supervisor และแอดมินพร้อมกันได้ ตำแหน่งงานและการเห็นข้อมูลทีมไม่เปลี่ยนเลย

| ฟิลด์ | ค่า | ความหมาย |
|-------|-----|----------|
| `role` | ไม่มี | ผู้ใช้ทั่วไป |
| | `dev` | ทำได้ทุกอย่างทั้งระบบ (เดิมคือ "แอดมิน") — รวมตั้งค่าปลายทางที่ส่งข้อมูลจริง |
| | `admin` | ดูแลผู้ใช้/ผูกรหัส/ดูผลกระจาย **ตามขอบเขต** แต่แตะการตั้งค่าระบบไม่ได้ |
| `admin_scope` | `all` | ดูแลทุกคนในระบบ รวมคนที่ยังไม่มีภาค/ดิวิชัน |
| | `division` | ทุกคนในดิวิชันเดียวกับตัวเอง ทุกภาค |
| | `division_region` | ดิวิชัน + ภาคเดียวกับตัวเอง (**ค่าเริ่มต้นเมื่อไม่ระบุ** — แคบสุด) |

- ตั้งค่าที่หน้าแอดมิน → **สิทธิ์ → ผู้ดูแลระบบ** (dev เท่านั้น) ห้ามแก้ด้วยมือบน server
- `ALLOCATION_ADMIN_EMAILS` ใน `.env` ยังเป็น bootstrap ของ `dev` เสมอ (กันล็อกตัวเองออก) — คนในลิสต์นี้เป็น dev แม้ไฟล์จะเขียน `role: admin` ไว้
- ขอบเขต `division`/`division_region` คิดจาก `acc_division`/`acc_region` **ของแถวตัวเอง** ถ้าไม่มีค่า = ขอบเขตว่าง เข้าหน้าแอดมินไม่ได้ (fail closed) ให้ใช้ `all` แทน
- **บัญชีแอดมินอย่างเดียว**: แถวที่มี `role` แต่ไม่มี `userpl` ได้ — สร้างจากหน้า "ผู้ดูแลระบบ" โดยพิมพ์อีเมลที่ยังไม่มีในระบบ บัญชีแบบนี้ล็อกอินได้แต่ไม่เห็นข้อมูลทีมใดเลย (แถวที่ไม่มีทั้ง `userpl` และ `role` ยังถูกทิ้งตอนอ่านไฟล์เหมือนเดิม) ถอดสิทธิ์เมื่อไหร่ แถวจะถูกลบทิ้งไปด้วย

## ผูกรหัส SL — `sl_links.json`

ใช้เมื่อพนักงานได้ **รหัส SL ใหม่** แต่ทีม/สิทธิยังอิงรหัสเก่าใน roster หรือ `user_access.json`

| ฟิลด์ | ความหมาย |
|--------|----------|
| `canonical_sl` | รหัสหลัก/เก่าที่มีสิทธิและทีมครบ (เช่น SL508) |
| `alias_sls` | รหัสใหม่ที่ล็อกอินได้ (เช่น SL524) — สืบทอดสิทธิจาก canonical |

- แก้ผ่านแอดมินแท็บ **「ผูกรหัส SL」** หรือแก้ไฟล์โดยตรง
- มีผลทันทีหลังบันทึก — ผู้ใช้รหัส alias อาจต้อง logout/login ใหม่
- ไม่แทนการย้ายทีมใน Fabric — ยังต้องเลือก Supervisor ที่มีพนักงานจริงใต้ `SuperCode` (เช่น SL532)

ตัวอย่าง:

```json
{
  "links": [
    {
      "canonical_sl": "SL508",
      "alias_sls": ["SL508", "SL524"],
      "note": "รหัสใหม่ SL524"
    }
  ]
}
```

## ผูกรหัส SKU — `sku_links.json`

รวมประวัติขายข้ามรหัสเก่าเมื่อโหลด Dashboard (ขยาย DAX ตอนดึงประวัติ แล้วรวมกลับเป็น canonical)

| ฟิลด์ | ความหมาย |
|--------|----------|
| `canonical_sku` | รหัสที่ใช้ใน Dashboard งวดนี้ |
| `alias_skus` | รหัสเก่าใน `cross_sold_history` |
| `product_name` | ชื่อแสดงในแอดมิน (ไม่บังคับ) |

- แก้ผ่านแอดมินแท็บ **「ผูกรหัส SKU」** — เปิดแท็บจะแสดงรายการสินค้าในงวดจาก cache Dashboard อัตโนมัติ
- หลังบันทึก link ให้ **refresh** ข้อมูล Dashboard (`refresh=true`) เพื่อ rebuild hist cache

## ราคาสินค้า → เป้าบาท

เป้าบาทในระบบ**ไม่ได้ดึงมาจาก Target Sun** แต่คำนวณเอง:
`เป้าบาทรายคน = Σ(หีบเป้า × ราคา/หีบ)` (`backend/services/employees.py`)
ราคา/หีบ มาจาก `cfm_product_characteristic[CREDITUNITPRICE]` ที่ `PRODUCTSIZE = 0`

**ตารางราคาเก็บเป็นช่วงวันที่** (`FROMDATE`/`TODATE`) — การเลือกราคาจึงต้องอ้าง
**วันที่ 1 ของงวดเป้า** ไม่ใช่วันที่ดึงข้อมูล เพราะเราทำเป้าเดือนหน้าล่วงหน้าเสมอ
สินค้าที่ปรับราคาโดยเริ่มมีผลวันที่ 1 ของงวดจะถูกคิดด้วยราคาเก่าทันทีถ้าใช้ "วันนี้"

> เคสจริงที่เจอ (19 ส.ค. 2026 · SL346 งวด ก.ย.): เป้าหีบตรงเป๊ะ 12,142 หีบ แต่เป้าบาท
> ต่ำกว่าความจริง **10,477.00 บาท** จาก 5 SKU ที่ราคาใหม่เริ่ม 2026-09-01 พอดี
> (เช่น 734046 ราคา 312 หมดอายุ 2026-08-31 → ราคาใหม่ 352 เริ่ม 2026-09-01)
> อาการหลอกคือ **พอถึงวันที่ 1 ของงวดจะหายเอง แล้วกลับมาใหม่ในงวดถัดไป**

- แคชสินค้า `data/cache/dim_product_YYYY_MM.json` มีฟิลด์ `price_asof` กำกับว่าราคาคิด ณ วันไหน — ถ้าไม่ตรงงวด (รวมถึงแคชรุ่นเก่าที่ไม่มีฟิลด์นี้) ระบบจะทิ้งแล้วดึงใหม่เอง ไม่ต้องรอ TTL
- หีบที่ส่งเข้า Target Sun **ไม่เกี่ยวกับราคา** (ไฟล์ส่งมีแต่จำนวนหีบ) ราคาจึงกระทบแค่เป้าบาทที่แสดง การเกลี่ยเป้าเงิน และการถ่วงดุลรายได้ของตัวกระจาย

## สวิตช์ที่กระทบ "ตัวเลขที่ผู้ใช้เห็น"

ตัวแปรกลุ่มนี้อยู่ใน `.env` แต่แยกออกมาเพราะเปลี่ยนแล้ว **ผลกระจายหีบเปลี่ยนตาม**
หรือทำให้ระบบเริ่ม/เลิกปฏิเสธคำขอ — อย่าเปิดโดยไม่แจ้งผู้ใช้ก่อน

| ตัวแปร | ค่าเริ่มต้น | ผลเมื่อเปิด |
|--------|-------------|-------------|
| `ALLOC_ALLOW_MISMATCH` | ปิด | ปล่อยผลที่ผลรวมหีบต่อ SKU ไม่ตรงเป้าให้บันทึกได้ (ปกติตอบ 409 และไม่บันทึก) — **ทางออกฉุกเฉินเท่านั้น** |
| `ALLOC_ZERO_BASELINE_CAP` | ปิด | จำกัดหีบของคนที่ไม่มีประวัติขาย SKU นั้น — วัดกับข้อมูลจริงแล้ว **หีบย้ายที่ราว 2–3.5% ต่อทีม** |
| `ALLOC_REQUIRE_IF_MATCH` | ปิด | บังคับ client ส่ง version ตอนบันทึก กัน tab เก่าเขียนทับ |
| `AGGREGATE_LOAD_WORKERS` | 6 | จำนวน thread โหลดรวมภาคพร้อมกัน (ไม่กระทบตัวเลข แค่ความเร็ว) |

กฎทั้งหมดที่ระบบบังคับ: [`docs/ALLOCATION_INVARIANTS.md`](../docs/ALLOCATION_INVARIANTS.md)

## เอกสารแหล่งข้อมูล

รายละเอียดการดึง/ใช้/ส่งข้อมูล (Semantic Model, cache, API): [`docs/DATA_FLOW.md`](../docs/DATA_FLOW.md)

## ความปลอดภัย

`.env` มี secret — อย่า commit; backup `user_access.json`, `sl_links.json`, `sku_links.json` บน server เป็นระยะหลังแอดมินแก้ผ่านเว็บ

## Deploy checklist (server เครื่องเดียว)

### ก่อน push / merge

1. รัน `python run_tests.py` บนเครื่อง dev (ต้องใช้ตัวนี้ ไม่ใช่ `unittest` ตรง ๆ — มันติดตั้ง
   กันชนไม่ให้เทสต์เขียนทับ config จริงและไม่ให้ยิงเน็ตขึ้นระบบจริง)
2. ตรวจว่าไม่ commit `config/.env` และ `.cursor/`
3. GitHub Actions (`.github/workflows/test.yml`) ต้องผ่านบน PR

### เตรียม server

1. คัดลอก `config/.env.example` → `config/.env` แล้วกรอกค่า production
2. ตั้ง persistent volume และ env (แนะนำ):

```env
ALLOCATIONS_DATA_DIR=/var/lib/allocation_target/allocations
USAGE_LOGS_DIR=/var/lib/allocation_target/logs
FABRIC_CACHE_DIR=/var/lib/allocation_target/cache
AZURE_AUTH_DISABLED=0
ALLOCATION_ADMIN_EMAILS=admin1@...,admin2@...
```

3. Entra redirect URI ตรงกับ domain production
4. **Target Sun URL (Read / Send แยกกัน):** default ใน `backend/services/targetsun_endpoints.py` · สลับ preset จากแอดมิน → `config/app_runtime.json` (ดู `docs/ENV_CHECKLIST_IT.md`)
5. รัน uvicorn **1 worker** — snapshot JSON ใช้ last-write-wins
6. **หลัง deploy โค้ดใหม่** — รีสตาร์ท service เสมอ (โดยเฉพาะ endpoint แอดมิน)

### Checklist

- IT env: [`docs/ENV_CHECKLIST_IT.md`](../docs/ENV_CHECKLIST_IT.md)
- QA ก่อน go-live: [`docs/DEPLOY_QA_CHECKLIST.md`](../docs/DEPLOY_QA_CHECKLIST.md)

### หลัง deploy

```bash
python scripts/dev/smoke_deploy.py --base-url https://your-host/allocation_target
```

### ทดสอบ go-live (manual)

1. Supervisor กระจาย → `PUT /data/allocations` 200 → peer อ่านได้ / เขียน 403
2. แอดมินเห็น snapshot + ดาวน์โหลดสำรอง + ลบได้
3. ส่ง Target Sun แยกแบรนด์ — Excel มีหีบถูก
4. Restart server — snapshot ยังอยู่ใน `ALLOCATIONS_DATA_DIR`
5. Footer แสดง `build <hash>` ตรงกับ commit ที่ deploy

### Log rotation

ไฟล์ `usage_*.jsonl` ใน `USAGE_LOGS_DIR` โตตามการใช้งาน — ย้าย/ลบงวดเก่าเป็นระยะเพื่อกัน disk เต็ม
