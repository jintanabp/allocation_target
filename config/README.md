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
- **ผู้จัดการรายภาค (`manager_level` = `regional`) ระบุ `acc_unit` ได้** (`van`/`credit`) → เห็นเฉพาะซุปหน่วยเดียวกันในภาค + รหัสตัวเอง · ไม่ระบุ = เห็นทุกทีมในภาคเหมือนเดิม · ระดับ `division` ระบุไม่ได้ (ค่าจะถูกตัดทิ้งตอนบันทึก เพราะขอบเขตคือทั้ง division อยู่แล้ว)
- **บัญชี "แอดมินอย่างเดียว"**: แถวที่มี `role` (`dev`/`admin`) แต่ `userpl` = `none` — ล็อกอินแล้วเข้าหน้าแอดมินโดยตรง ไม่มีหน้า dashboard ให้เลือกทีม (ต้องตั้ง `acc_region`/`acc_division` ให้ด้วย ไม่งั้นแอดมินรายภาคจะได้ขอบเขตว่างและถูกปฏิเสธแบบ fail-closed)
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
| | `dev` | ทำได้ทุกอย่างทั้งระบบ (เดิมคือ "แอดมิน") — รวมตั้งค่าปลายทางที่ส่งข้อมูลจริง และ**กู้คืนเป้าตั้งต้น** |
| | `head_admin` | หัวหน้าแอดมิน — เห็นทุกทีมทั้งระบบเหมือน dev และ **เพิ่ม/ลบแอดมินคนอื่นได้** แต่แตะการตั้งค่าระบบ/ปลายทางส่งข้อมูลไม่ได้ |
| | `admin` | ดูแลผู้ใช้/ผูกรหัส/ดูผลกระจาย **ตามขอบเขต** แต่แตะการตั้งค่าระบบไม่ได้ และเพิ่มแอดมินไม่ได้ |
| `admin_scope` | `all` | ดูแลทุกคนในระบบ รวมคนที่ยังไม่มีภาค/ดิวิชัน |
| | `division` | ทุกคนในดิวิชันเดียวกับตัวเอง ทุกภาค |
| | `division_region` | ดิวิชัน + ภาคเดียวกับตัวเอง (**ค่าเริ่มต้นเมื่อไม่ระบุ** — แคบสุด) |

- ตั้งค่าที่หน้าแอดมิน → **สิทธิ์ → ผู้ดูแลระบบ** (dev และ head_admin) ห้ามแก้ด้วยมือบน server ·
  head_admin ให้ได้แค่ระดับ `admin` เท่านั้น และแตะแถวของตัวเองไม่ได้ (กันยกระดับตัวเอง)
- **head_admin ได้ `admin_scope: all` เสมอ** — ระบบบังคับให้ทั้งตอนอ่านและตอนบันทึก
  (แถวเก่าที่เคยเป็น `division_region` จึงหายเองโดยไม่ต้องแก้ไฟล์) ·
  ถ้าไม่บังคับ บัญชีที่ไม่มีภาค/ดิวิชันจะได้ขอบเขตว่างแล้วโดนปฏิเสธทุกหน้า ทั้งที่เป็นหัวหน้าแอดมิน
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

## พนักงานที่ไม่ต้องตั้งเป้า — `no_target_employees.json`

พนักงานกรณีพิเศษที่ไม่ต้องมีเป้าและไม่รับหีบ — ตั้งจากหน้าแอดมินแท็บ **「ทีมพนักงาน」**
เลือกซุป แล้วติ๊กรายคน

| ฟิลด์ | ความหมาย |
|--------|----------|
| `super_code` | รหัสซุปเจ้าของทีม |
| `emp_id` | รหัสพนักงาน |
| `note` | เหตุผล (ไม่บังคับ) |
| `updated_by` / `updated_at` | ใครตั้งและเมื่อไหร่ (คงเวลาเดิมไว้เมื่อกดบันทึกซ้ำ) |

- คีย์เป็น **(ซุป, พนักงาน)** ไม่ใช่รหัสพนักงานเดี่ยว ๆ เพราะรหัสซ้ำข้ามทีมได้ —
  กัน SL509 ต้องไม่พลอยกัน SL397
- ผลที่หน้ากระจายหีบ: ขั้นที่ 1–2 ขึ้น**แถบเข้ม**พร้อมป้าย「ไม่ต้องตั้งเป้า」เป้าเงินเป็น **0**
  และแก้ไม่ได้ · ขั้นที่ 3 **ไม่มีแถวของเขา**
- **เป้าหีบของทีมยังเท่าเดิม** (I1) คนที่เหลือรับส่วนนั้นไป — รายชื่อนี้ตัดแค่ "ใครรับได้"
- มีผล**ถาวรจนกว่าจะเอาติ๊กออก** ไม่หายเมื่อเป้าต้นทางเปลี่ยนหรือรีเฟรชเป้าสด
- ซุปต้องโหลดขั้นที่ 1 ใหม่จึงจะเห็นผล (payload ถูกคำนวณ flag ใหม่ทุกครั้ง รวมตอนอ่านจาก cache)
- `/optimize` ตัดคนเหล่านี้ออกซ้ำอีกชั้นที่ฝั่ง server — กันหน้าเว็บรุ่นเก่าที่ค้างในเบราว์เซอร์
  ส่งเขามาให้กระจาย
- ไฟล์พัง = ระบบถือว่า**ไม่มีใครถูกกัน** พร้อมเขียน log ระดับ error (ไฟล์ตั้งค่าเสริมพัง
  ไม่ควรทำให้ซุปทั้งบริษัทเปิดหน้ากระจายหีบไม่ได้)

## เป้าตั้งต้นของงวด — `data/baselines/`

`data/baselines/{SL}_{ปี}_{เดือน}.json` คือสำเนาเป้า **ชุดแรก** ที่ระบบดึงมาตอนเปิดงวดนั้น
เก็บทั้งเป้าหีบราย SKU และเป้าเงินราย emp เพราะทั้งคู่หายได้พอกัน

ทำไมต้องมี: เป้าหีบของงวดมีอยู่ที่เดียวคือ `data/target_boxes_{SL}_{งวด}.csv` ซึ่ง**ถูกเขียนทับ**
ทุกครั้งที่โหลดขั้นที่ 1 ใหม่ เดิมไม่มีสำเนาเก่าเก็บไว้เลย เป้าเพี้ยนแล้วไม่มีอะไรให้เทียบหรือกู้

- **เขียนครั้งเดียวแล้วไม่แตะอีก** — ถ้าเขียนทับเรื่อย ๆ ก็เป็นแค่สำเนาของค่าล่าสุด = ไร้ประโยชน์
- อยู่ในโฟลเดอร์ย่อย จึงไม่ถูกตัวล้าง cache ตามอายุแตะ (ตัวล้างวนเฉพาะไฟล์ชั้นบนใน `data/`)
- รอบถัดไปที่เป้าต่างจากตั้งต้น ระบบเขียน log `target_baseline_drift` พร้อมค่าก่อน/หลังราย SKU
- ดูได้จากหน้าแอดมิน → **ผลการดำเนินงาน** (แอดมินทุกระดับ ตามขอบเขตตัวเอง) ·
  **ปุ่มกู้คืนมีเฉพาะ dev** เพราะเป็นการทับเป้าที่ทีมอื่นอาจกำลังใช้อยู่ · การกู้คืนถูก audit เสมอ
- การกู้คืน**ไม่แตะ snapshot ผลกระจาย** — คืนแค่เป้า ผู้ใช้ต้องกดกระจายใหม่เองถ้าต้องการผลที่ตรงกัน

## ความปลอดภัย

`.env` มี secret — อย่า commit

**ไฟล์ที่ต้องสำรองบน server** (แอปเขียนทับตอนใช้งานจริง หายแล้วสร้างใหม่จากโค้ดไม่ได้):

| ไฟล์ | เนื้อหา | git ติดตาม? |
|------|---------|-------------|
| `config/user_access.json` | ผู้ใช้ + สิทธิ์ที่แอดมินเพิ่มบนเว็บ | ❌ ไม่ (ถอดออกแล้ว 26 ส.ค. 2026) |
| `config/access_hierarchy.json` | ลำดับ Manager → Supervisor | ✅ ใช่ |
| `config/sl_links.json`, `config/sku_links.json` | การผูกรหัส | ✅ ใช่ |
| `config/no_target_employees.json` | พนักงานที่ไม่ต้องตั้งเป้า | ✅ ใช่ |
| `data/allocations/*.json` | ผลการกระจายราย SL × งวด | ❌ ไม่ |
| `data/baselines/*.json` | เป้าตั้งต้นของงวด | ❌ ไม่ |
| `data/logs/*.jsonl` | บันทึกการใช้งาน | ❌ ไม่ |

ทุกอย่างใน `data/` **ไม่มีทางหายจาก `git pull`** เพราะไม่เคยอยู่ใน git เลย
`config/user_access.json` และ `config/app_runtime.json` ก็ถูกถอดออกจาก git แล้วเช่นกัน
(ทั้งคู่เคยโดน pull เขียนทับจนข้อมูลที่ตั้งบนเว็บหายมาแล้ว)

แต่อีก 3 ไฟล์ใน `config/` ยังถูกติดตามอยู่ → 🔴 **ห้าม `git reset --hard` / `git checkout .`
บน server เด็ดขาด** เพราะจะทับค่าที่ตั้งบนเว็บด้วยเวอร์ชันในโค้ดทันที
ถ้า `git pull` ฟ้อง "local changes would be overwritten" ให้ copy ไฟล์นั้นเก็บก่อนแล้วค่อยแก้ ไม่ใช่ reset

> **การ pull ครั้งแรกหลังถอด `config/user_access.json` ออกจาก git จะลบไฟล์นี้ทิ้ง**
> ต้องสำรองก่อน pull แล้ววางกลับทันที ไม่งั้นทุกคนที่ไม่ได้อยู่ใน `ALLOCATION_ADMIN_EMAILS`
> จะล็อกอินไม่ได้ (ดู `docs/DEPLOY_QA_CHECKLIST.md` หมวด 10)

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

### บันทึกการใช้งาน — `usage_YYYY-MM-DD.jsonl`

หนึ่งบรรทัดต่อเหตุการณ์ ใน `USAGE_LOGS_DIR` (ค่าเริ่มต้น `data/logs/`)

| ฟิลด์ | ความหมาย |
|--------|----------|
| `ts` | เวลาเกิดเหตุ **เป็น UTC** — หน้าจอและ Excel แปลงเป็นเวลาไทยให้ทั้งคู่ |
| `level` | `info` / `warn` / `error` |
| `email`, `role` | ใครทำ และตอนนั้นเป็นบทบาทอะไร |
| `sup_id` | ทีมที่ถูกกระทำ (ว่าง = เรื่องระดับระบบ เช่น แก้สิทธิ์) |
| `action` | รหัสเหตุการณ์ เช่น `save_allocation_ok`, `delete_allocation`, `target_baseline_restore` |
| `message` | ข้อความสำหรับคนอ่าน |
| `detail` | รายละเอียดเชิงเทคนิค (ข้อความ) |
| `target_month`, `target_year` | **งวดเป้าที่เหตุการณ์พูดถึง** — คนละเรื่องกับ `ts` |
| `context` | ค่าก่อน/หลังแบบมีโครงสร้าง (เช่น `boxes_before`, `version_after`) ไว้เทียบด้วยเครื่อง |
| `request_id`, `entry_id` | ไว้อ้างถึงบรรทัดนี้เวลาสอบถามข้ามทีม |

เหตุการณ์ที่ **ต้อง** มีเสมอเพราะย้อนกลับไม่ได้:
`save_allocation_ok` (ทับผลกระจาย · เก็บ version และยอดหีบก่อน/หลัง) ·
`delete_allocation` / `admin_delete_allocation` (ลบผลกระจาย · เก็บจำนวนแถว หีบ และใครบันทึกไว้) ·
`target_baseline_restore` (กู้คืนเป้า) · `no_target_employees_set` (กันพนักงานออกจากการตั้งเป้า)

> ตัวกรอง **ปีอย่างเดียว** เคยคืนแค่ log ของวันนี้เงียบ ๆ ทำให้แอดมินเข้าใจว่าไม่มีเหตุการณ์
> ทั้งที่มีเต็มไปหมด — ตอนนี้คัดจากชื่อไฟล์จริง ระบุปี/เดือนอย่างใดอย่างหนึ่งก็ได้

### Log rotation

ไฟล์ `usage_*.jsonl` ใน `USAGE_LOGS_DIR` โตตามการใช้งานและ**ไม่มีตัวล้างอัตโนมัติ** —
ย้าย/ลบงวดเก่าเป็นระยะเพื่อกัน disk เต็ม (ยังไม่ใช่ปัญหาที่ปริมาณปัจจุบัน แต่ควรรู้ไว้)
**อย่าลบก่อนสำรอง** — เป็นหลักฐานเดียวที่บอกได้ว่าใครแตะเป้างวดไหน
