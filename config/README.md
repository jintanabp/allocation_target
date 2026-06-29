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
- ฟิลด์ `login_kind`: `marketing` = เข้าระบบแล้วเห็นแอดมินแท็บ **ทีมพนักงาน**, **ผูกรหัส SL** (ดู), **ผูกรหัส SKU** (ดู) เท่านั้น
- ฟิลด์ `acc_scope`: `self` = ดูเฉพาะทีมตัวเอง · `region_peers` = ดู Supervisor ใน **division+ภาคเดียวกัน** (กระจายหีบได้เฉพาะทีมตัวเอง — ทีมคนอื่นเป็น read-only)
- ฟิลด์สำคัญ: `full_name`, `acc_division`, `acc_region`, `acc_unit`, `acc_scope` (`all`/`credit`/`van`/`self`/`region_peers`), `login_kind`, `visible_supervisor_codes` (precompute)

### กฎสิทธิ์ (Excel roster)

| Division | บทบาท | ดูได้ |
|----------|--------|--------|
| Div.B / Div.E | ผจก./ผช.ผจก. (`manager_acc`) | ซุปทุกคนใน **division + ภาค** เดียวกัน · โหมด「รวมภาค」/「รวมทั้งหมด」กระจายหีบได้ |
| Div.B / Div.E | ซุป (`supervisor_acc`) + `self` | **รหัส SL ตัวเอง** เท่านั้น |
| Div.B / Div.E | ซุป (`supervisor_acc`) + `region_peers` | ซุปทุกคนใน **division + ภาค** เดียวกัน (ดูรวมทั้งภาคได้ · กระจายหีบได้เฉพาะทีมตัวเอง) |
| Div.S | ขอบเขต `All` + ภูมิภาค `Div.S` | ซุปทุกคนใน Div.S ทุกภาค |
| Div.S | ขอบเขต `All` + ภาคเฉพาะ | ซุปใน Div.S ภาคนั้น |
| Div.S | `Credit All` / `Van All` | รหัส SL ตัวเอง + `acc_unit` |

**Dim_Salesman** ใช้เฉพาะดึงพนักงานใต้ `SuperCode` และ validate (`validate_access_with_dim.py`) — ไม่กำหนดสิทธิ login

ตัวอย่างรูปแบบ: ดู `config/user_access.example.json`

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

## เอกสารแหล่งข้อมูล

รายละเอียดการดึง/ใช้/ส่งข้อมูล (Semantic Model, cache, API): [`docs/DATA_FLOW.md`](../docs/DATA_FLOW.md)

## ความปลอดภัย

`.env` มี secret — อย่า commit; backup `user_access.json`, `sl_links.json`, `sku_links.json` บน server เป็นระยะหลังแอดมินแก้ผ่านเว็บ
