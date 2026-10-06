# การใช้งานพร้อมกันหลายคน (Concurrency)

เอกสารนี้อธิบายว่าอะไรกันอะไรอยู่ และ **อะไรจะพังถ้าเปลี่ยนวิธี deploy**
อ่านก่อนแตะโค้ดที่เขียนไฟล์ใน `data/` หรือก่อนคิดจะเพิ่ม worker

## ข้อสมมติที่ทั้งระบบแบกอยู่: **1 uvicorn worker เท่านั้น**

route handler ทุกตัวประกาศเป็น `def` ไม่ใช่ `async def` → FastAPI โยนเข้า
**anyio threadpool (ค่าเริ่มต้น 40 threads)** → **request หลายคนรันขนานกันจริงในโปรเซสเดียว**

ดังนั้น:

| สิ่งที่ "1 worker" กันได้ | สิ่งที่ "1 worker" **ไม่ได้** กัน |
|---|---|
| race ข้ามโปรเซส | race ระหว่าง thread ใน worker เดียวกัน |

`threading.Lock` / `RLock` จึง **จำเป็นและเพียงพอ — เฉพาะที่ `--workers 1`**

> ⚠️ **เพิ่มเป็น `--workers 2` หรือขึ้นหลาย container หลัง load balancer เมื่อไหร่
> lock ทุกตัวในระบบจะไร้ผลทันที** เพราะเป็น lock ระดับโปรเซส บั๊กที่แก้ไปแล้วจะกลับมาแบบเงียบ ๆ
> ถ้าจะ scale out จริง ต้องเปลี่ยนไปใช้ file lock (`filelock` / `fcntl.flock`) หรือฐานข้อมูล
> — ไม่ใช่แค่เพิ่มตัวเลข worker

`backend/app_factory.py` มี startup guard คอย log error ถ้าเจอ `WEB_CONCURRENCY > 1`
(log อย่างเดียว ไม่ fail startup — IT เป็นเจ้าของ deploy)

## `backend/core/atomic_io.py` — เขียนไฟล์แบบ atomic

ใช้แทน `open(..., "w")` / `df.to_csv(path)` ทุกที่ที่ไฟล์นั้นมีคนอ่านพร้อมกันได้

```python
from ..core.atomic_io import atomic_write_csv, atomic_write_json, read_locked

atomic_write_csv(path, df)          # เขียน
with read_locked(path):             # อ่าน — ต้องครอบด้วย!
    df = pd.read_csv(path)
```

### ทำไม reader ต้องถือ lock ด้วย (Windows)

`os.replace` บน Windows พังเป็น `PermissionError [WinError 5]` **สองกรณี** — พิสูจน์แล้วทั้งคู่ใน
`tests/test_atomic_io.py`:

1. **writer ชน writer** — `os.replace` สองตัวไปไฟล์เดียวกันพร้อมกัน (8 threads → พัง 7)
2. **writer ชน reader** — Windows replace ไฟล์ที่มีใครเปิด handle ค้างไม่ได้

POSIX ไม่มีปัญหานี้เพราะ `rename()` เป็น atomic จริงและไม่สนใจ handle ที่เปิดอยู่
**โค้ดนี้รันบน Windows จึงต้องมี lock** — `atomic_io` ล็อกต่อ path ให้ ทั้ง writer และ reader
ต้องใช้ lock ตัวเดียวกัน ส่วน retry เป็นแค่กันเหนียวสำหรับตัวกวนนอกโปรเซส (antivirus / ตัวทำ index)

**สิ่งที่ atomic_io ทำให้ไม่ได้:** มันกันแค่ *torn read* กับ *replace ชนกัน*
การกันสองคนแก้ทับกัน (read-modify-write) ต้องใช้ lock ของ store นั้นครอบเอง

## ไฟล์ใน `data/` แยกราย supervisor ทั้งหมด

`backend/core/paths.py` เป็นแหล่งความจริงเดียวของชื่อไฟล์ — **ทุกฟังก์ชันใส่ `safe_id(sup_id)` และงวด**

```python
target_boxes_{SUP}_{YYYY}_{MM}.csv   # เป้าหีบราย SKU
target_sun_{SUP}_{YYYY}_{MM}.csv     # เป้า Target Sun ราย emp
emp_cache_{SUP}_{YYYY}_{MM}.csv
hist_cache_{SUP}_{YYYY}_{MM}.csv     # 3M (+ _6m)
hist_cy_{SUP}_{YYYY}.csv             # ปีปฏิทิน — ใช้ตรวจ「สินค้าใหม่」
payload_cache_{SUP}_{YYYY}_{MM}.json
allocations/{SUP}_{YYYY}_{MM}.json
```

> **อย่าสร้างไฟล์ใน `data/` ที่ไม่มี `sup_id` ในชื่อ** — นี่คือบั๊กที่ `target_boxes.csv` เคยเป็น:
> ไฟล์เดียวทั้งระบบ ไม่มีทั้ง `sup_id` ในชื่อและคอลัมน์ ทีมที่โหลดทีหลังเขียนทับของทีมก่อน
> แล้ว `optimize.py` เอาไปป้อน LP → ทีม A ได้ผลคำนวณจากเป้าของทีม B **โดยไม่มี error ใด ๆ**

`data/target_boxes.csv` / `data/target_sun.csv` ยังเหลืออยู่เป็น **fallback ชั่วคราว**
(`core/targets.py: load_target_csv_for(..., allow_legacy_fallback=True)`) เพื่อไม่ให้ผู้ใช้เจอ error
ตอน deploy ใหม่ ๆ — ทุกครั้งที่ fallback ทำงานจะมี `logger.warning("target CSV: ใช้ไฟล์ global เดิม…")`
**เมื่อ log นี้เงียบแล้วให้ถอด fallback ออก** และลบไฟล์ global ทิ้ง

### ไฟล์ global ที่ตั้งใจไม่ผูก `sup_id` — ข้อยกเว้นของกฎด้านบน

สองไฟล์นี้เป็นค่าที่ **ใช้ร่วมกันทั้งระบบโดยตั้งใจ** ไม่ใช่บั๊กแบบ `target_boxes.csv` เดิม:

| ไฟล์ | เก็บอะไร | ทำไมต้อง global |
|---|---|---|
| `data/alloc_rules.json` | ค่ากติกาการเกลี่ยที่แอดมินตั้งจากหน้าเว็บ (เปิด/ปิด "ไม่เคยขาย=0", เกณฑ์ดันเป้า, รายทีมที่ปิด) | กติกาเป็นค่าตั้งของทั้งบริษัท ไม่ใช่ของทีมใดทีมหนึ่ง |
| `data/feedback/feedback.json` | ข้อความจากปุ่ม 💬 ของผู้ใช้ทุกทีม | หน้าแอดมินต้องอ่านรวมทุกทีมในที่เดียว |

ทั้งคู่ป้องกัน read-modify-write ชนกันแล้ว (`user_access.json` ก็แก้แล้วตั้งแต่ 25 ก.ย. — ดูด้านล่าง):
`backend/services/alloc_rules_store.py` (`write_settings()`) อ่าน + เช็ค `expected_rev` +
เขียนทั้งหมด **ใต้ `_STORE_LOCK` เดียว** แล้วปฏิเสธด้วย `AllocRulesConflict` ถ้า `rev` ไม่ตรง
(compare-and-swap แบบเดียวกับ `allocation_store.py`) · `feedback_store.py` ก็ครอบ
read-modify-write ใต้ `_STORE_LOCK` ของตัวเองเช่นกัน — แอดมิน 2 คนกดบันทึกกติกาพร้อมกัน
คนที่ `rev` ไม่ตรงจะได้ 409 ไม่ใช่ข้อมูลหาย

**อ่านเพื่อแก้ ต้องแยก「ไม่มีไฟล์」กับ「อ่านไม่ได้」** (6 ต.ค. 2026, ผลตรวจ 5 ต.ค. 7.2/7.3):
`feedback_store._read_doc_for_update` และ `allocation_store._read_snapshot_for_update` ลองอ่าน 3 ครั้ง
ถ้ายังติด OSError (ไฟล์ถูกล็อกบน Windows ฯลฯ) → raise `FeedbackUnreadable` / `SnapshotUnreadable` → 503 ไม่เขียนทับ ·
JSON เสีย → ย้ายไป `*.corrupt-<เวลา>` เก็บไว้ แล้วเริ่มใหม่ · ห้ามกลับไปใช้ "อ่านไม่ได้ = เริ่มจากว่าง" ในทางที่เขียนกลับ

**แคชกลางที่ทุกทีมเขียนร่วมกัน** (7.4): แคชสินค้า `dim_product_*` และราคา `price_per_box_*` เป็นไฟล์เดียวต่องวด
ต้องเขียนผ่าน `fabric_cache.merge_product_info_df` / `merge_price_map` เท่านั้น (อ่านของเก่าแม้หมดอายุ + รวม + เขียน
ใต้ `fabric_cache._LOCK`) — โหลดรวมภาครัน 6 เธรด ถ้าอ่าน-รวม-เขียนเองนอกล็อก คนเขียนทีหลังลบ SKU ทีมอื่น

**ล้างแคช payload** (7.13): ลบใต้ล็อกต่อ path + ลองซ้ำ · ลบไม่ได้ (Windows ถือไฟล์) → `_INVALIDATED_AT` จำเวลาไว้
ในหน่วยความจำ ตัวอ่านทิ้งไฟล์ที่ `cached_at` ก่อนเวลานั้น (ใช้ได้เพราะ server เป็น worker เดียว)

## บันทึกผลกระจาย — optimistic concurrency

`PUT /data/allocations` ใช้ **compare-and-swap** ด้วย `version` (int เพิ่มทีละ 1)

```
client โหลด → เห็น version: 3
client บันทึก → ส่ง if_match_version: 3
   server: version บนดิสก์ == 3?  → เขียน version: 4  → 200
                          != 3?  → 409 + detail.current.version
```

- **ใช้ `version` ไม่ใช่ `updated_at`** เพราะ `_now_iso()` ตัดหน่วยไมโครวินาที และ autosave
  ฝั่ง frontend debounce 800ms → สอง save ในวินาทีเดียวกันได้ timestamp เท่ากัน = precondition มีรู
- snapshot เก่าที่ไม่มี field `version` → นับเป็น 0 → **ไม่ต้อง migrate**
- `_STORE_LOCK` เป็น **`RLock`** เพราะ CAS ต้องอ่านใต้ lock เดียวกัน และ `mark_sent_targetsun`
  ก็เป็น read-modify-write ที่เรียก `write_snapshot` ซ้อนข้างใน

### `ALLOC_REQUIRE_IF_MATCH` — สวิตช์ rollout

| ค่า | server ทำอะไร | tab เก่า (JS เดิม ไม่ส่ง version) |
|---|---|---|
| **ไม่ตั้ง / 0** (ค่าเริ่มต้น) | บังคับ version **เฉพาะเมื่อ client ส่งมา** | เขียนทับได้เหมือนเดิม — **ไม่พัง** |
| **1** | ไม่ส่ง version + มี snapshot อยู่แล้ว → **428** | ถูกปฏิเสธ พร้อมข้อความให้กด Ctrl+F5 |

**ขั้นตอนเปิด:** deploy → ดู usage log ว่ายังมี `save_allocation_no_precondition` ไหม (1-3 วัน) →
ถ้าเงียบแล้วค่อยตั้ง `ALLOC_REQUIRE_IF_MATCH=1` — เป็นการเปลี่ยน env บนเซิร์ฟเวอร์ที่ deploy แล้ว
**ย้อนได้ทันที ไม่ต้อง push โค้ด**

การสร้าง snapshot **ใหม่** ยังผ่านเสมอแม้เปิดโหมดบังคับ (ไม่มี lost update ให้กัน)

> **แก้แล้ว:** ก่อนหน้านี้ `mark_sent_targetsun()` เรียก `write_snapshot(body)` โดยไม่ส่ง
> `expected_version` พอเปิด `ALLOC_REQUIRE_IF_MATCH=1` + มี snapshot อยู่แล้ว มันจะเข้าเงื่อนไข
> `expected_version is None and current is not None and require_if_match()` แล้วโยน
> `SnapshotPreconditionRequired` ทุกครั้ง — **"ส่ง Target Sun" จะพังทันทีที่เปิดสวิตช์นี้**
> ตอนนี้อ่าน version ใต้ `_STORE_LOCK` เดียวกันแล้วส่งเข้าไปด้วย (RLock อยู่แล้ว จึงยังอะตอมมิก)

## lock ที่มีอยู่ในระบบ

| ไฟล์ | lock | กันอะไร |
|---|---|---|
| `core/atomic_io.py` | `RLock` ต่อ path | torn read + `os.replace` ชนกัน (Windows) |
| `services/allocation_store.py` | `_STORE_LOCK` (**RLock**) | CAS + `mark_sent_targetsun` RMW |
| `services/user_access_store.py` | `_STORE_LOCK` (**RLock**) + `mutate_rows()` + `atomic_write_text` | อ่าน→แก้→เขียน รอบเดียวใต้ล็อกเดียว (ทุก endpoint ผู้ใช้/สิทธิ์ในหน้าแอดมิน) · retry ตอน `os.replace` โดน PermissionError |
| `services/fabric_cache.py` | `_LOCK` | เขียน cache |
| `services/app_runtime_settings.py` | `_LOCK` + `atomic_write_text` | เขียน settings (retry ตอน PermissionError) |
| `services/usage_log_store.py` | `_LOCK` | append/rewrite jsonl |
| `services/sl_link_store.py` / `sku_link_store.py` | `_STORE_LOCK` (**RLock**) + `mutate_links()` + `atomic_write_text` | แอดมินสร้าง/แก้/ลบการผูกรหัส อ่าน→แก้→เขียน ใต้ล็อกเดียว (ผลตรวจ §5.1-1) |
| `services/no_target_store.py` | `_STORE_LOCK` (**RLock**) + atomic | บันทึกรายชื่อ「ไม่ต้องตั้งเป้า」ของสองทีมพร้อมกันไม่ทับกัน (§5.1-2) |
| `services/emp_assignment_store.py` | `_STORE_LOCK` (**RLock**) + `read_locked` | การย้ายพนักงานไปเกลี่ยทีมอื่น · ก่อนเขียนอ่านแบบ `strict` — ไฟล์อ่านไม่ได้ = ไม่บันทึก (เดิมเขียนทับเหลือแถวเดียว) |
| `services/admin_permissions_store.py` | `_STORE_LOCK` + `atomic_write_text` | สิทธิ์หน้าแอดมินรายบทบาท |
| `services/warehouse_pin_rules_store.py` | `_STORE_LOCK` + `atomic_write_json` | กติกาบังคับคลังเดียว |
| `services/notification_store.py` | `_STORE_LOCK` (**RLock**) | กล่องแจ้งเตือนในแอป · ก่อนเขียนอ่านแบบ `for_update` — อ่านไม่ได้ = ไม่เพิ่ม/ไม่รับทราบ · JSON เสีย = เก็บสำเนาแล้วเริ่มใหม่ |
| `services/managers.py::rebuild_managers_from_roster` | ล็อกของ `user_access_store` | สร้างลำดับสิทธิ์ใหม่จากรายชื่อล่าสุดเสมอ ไม่เขียน `managers_cache.json` ซ้ำสองรอบ (§5.1-5) |
| `emp_cache_*.csv` (admin_team / employees / lakehouse) | `atomic_write_csv` + `read_locked` | เขียนจากสองที่ อ่านไม่เจอไฟล์ครึ่งใบ (§5.1-4) |
| `services/sent_ledger.py` | `_path_lock(path)` + `atomic_write_json` | ส่งหลายรอบ/หลายทีมพร้อมกันไม่ทับ ledger ของกันและกัน (F2) · `_read_for_update` แยก「ไม่มีไฟล์」กับ「อ่านไม่ได้」 (ลองใหม่ 3 ครั้ง ยังไม่ได้ = ไม่เขียน + แจ้ง dev) |
| `services/nightly_check.py` | ล็อกไฟล์ `data/.nightly_check.lock` (ข้ามโปรเซส) + `_path_lock` ของ settings/state | ตรวจรายคืนไม่รันซ้อน — ทั้งตัวตั้งเวลา ปุ่มรันเดี๋ยวนี้ และหลาย worker (F3) · ได้ล็อกแล้วตรวจซ้ำว่าวันนี้รันไปหรือยัง (กันรันซ้ำวันเดียว) · settings/state อ่าน-แก้-เขียนใต้ล็อก · ปุ่มรันเดี๋ยวนี้ตอบ busy เมื่อมีรอบถือล็อก (`is_running`) |
| `core/runtime_checks.py` | ล็อกไฟล์ `data/.app_process.lock` ตลอดอายุโปรเซส | ตรวจว่ามีโปรเซสเดียวใช้ `data/` — ล็อกไม่ได้ = log error + `/health` (§5.2) |
| `fabric_dax_connector.py` | `_TOKEN_CACHE_LOCK` | เขียน `data/token_cache.bin` (เฉพาะโหมดล็อกอินผู้ใช้) |
| `services/alloc_rules_store.py` | `_STORE_LOCK` | CAS ด้วย `rev` (ดูหัวข้อ "ไฟล์ global" ด้านบน) |
| `services/feedback_store.py` | `_STORE_LOCK` | append/แก้สถานะความเห็นผู้ใช้ |
| `services/access_hierarchy.py` | `_PERSIST_LOCK` + `atomic_write_json` | เขียน `config/access_hierarchy.json` + `data/managers_cache.json` เป็นคู่จากรอบเดียวกัน |
| `services/target_baseline.py` | `read_locked(path)` คร่อม "เช็ค→เขียน" | เป้าตั้งต้นเขียนครั้งเดียว ไม่ถูกแท็บที่สองทับ |

## โหลดรวมภาคทำงานขนานกัน (thread pool ซ้อนใน request)

`services/employees.py: load_employees_bulk()` เดิมวน `for sid in ids:` ยิง DAX ทีละทีม
8 ทีม = 8 รอบต่อกันจนจบ ตอนนี้ใช้ `ThreadPoolExecutor` เพราะแต่ละทีมอิสระต่อกันจริง:

- `FabricDAXConnector()` **ถูกสร้างใหม่ทุกครั้งที่เรียก** (`employees.py:322, 396, 646`)
  ไม่มี instance ใช้ร่วมกันระหว่าง thread
- path ไฟล์ cache แยกตาม `(sup, งวด)` อยู่แล้ว → คนละทีมไม่เขียนไฟล์เดียวกัน
- ที่ต้องกันเพิ่มคือ `data/token_cache.bin` ซึ่งเป็นไฟล์เดียวร่วมกัน — ใส่ `_TOKEN_CACHE_LOCK`
  + `atomic_write_text` แล้ว (เข้าเส้นทางนี้เฉพาะโหมดล็อกอินผู้ใช้ ไม่ใช่ Service Principal)

| env | ค่าเริ่มต้น | หมายเหตุ |
|---|---|---|
| `AGGREGATE_LOAD_WORKERS` | 6 | เพดาน 8 และไม่เกินจำนวนทีมจริง · ตั้ง `1` = กลับไปโหลดทีละทีมแบบเดิม |

> เพดาน 8 เพื่อไม่ให้ไปเบียด anyio threadpool ของ FastAPI (ค่าเริ่มต้น 40 threads)
> ถ้ามี request รวมภาคหลายอันพร้อมกัน thread จะถูกใช้เป็นทวีคูณ — อย่าตั้งสูงกว่านี้โดยไม่วัดก่อน

## ไฟล์ผลกระจายผูกกับงวดแล้ว

`result_path()` / `excel_path()` เดิมไม่มีเดือน/ปีในชื่อ:

```
data/final_allocation_{SUP}.csv        ← เดิม (ยังรองรับตอนอ่าน)
data/final_allocation_{SUP}_{YYYY}_{MM}.csv   ← ตอนนี้
data/Final_Dashboard_{SUP}_{YYYY}_{MM}.xlsx
```

ของเดิมกระจายสองงวดของซุปเดียวกันพร้อมกันจะเขียนทับกัน แล้ว `create_target_excel`
ที่อ่านไฟล์นั้นต่อทันทีอาจได้ข้อมูลของอีกงวด (`atomic_write_csv` กันได้แค่ torn read
ไม่ได้กัน TOCTOU ข้ามงวด)

`download_excel_response()` ไม่รับงวด จึงใช้ `latest_excel_path_for_sup()` หยิบงวดล่าสุด

## ที่ยังไม่ได้แก้ (รู้อยู่)

> **แก้แล้ว (30 ก.ย. 2026, ผลตรวจ §5.1):** การผูกรหัส SKU/SL, รายชื่อไม่ต้องตั้งเป้า, emp_cache,
> store ที่เขียน temp + `os.replace` เอง และการสร้างลำดับสิทธิ์ใหม่ — ดูตาราง lock ด้านบน
> (`tests/test_store_concurrency_e1.py` ยิงสองคำขอพร้อมกันแล้วตรวจว่าของทั้งสองรอด) · ตอนเปิดแอป
> ตรวจด้วยว่ามีหลายโปรเซสใช้ `data/` ชุดเดียวกันไหม (`core/runtime_checks.py` → `/health` runtime)

> **แก้แล้ว (25 ก.ย. 2026):** `config/user_access.json` lost update — `routers/admin.py` เคยทำ
> `read_rows()` → แก้ → `write_rows()` ซึ่งจับ `_STORE_LOCK` คนละรอบ แอดมิน 2 คนบันทึกพร้อมกัน
> การแก้ของคนหนึ่งหายเงียบ ๆ ทั้งที่ได้ HTTP 200 (วัดในแซนด์บ็อกซ์: ยิงพร้อมกัน 41 คำขอ หาย
> 24 รายการ) · ตอนนี้ทุก endpoint (เพิ่ม/แก้/ลบผู้ใช้, สิทธิ์ส่ง Target Sun, ตั้ง role,
> rebuild ลำดับสิทธิ์) ใช้ `user_access_store.mutate_rows(fn)` — การตรวจ 404/409/ขอบเขต ทำใน
> `fn` กับแถวล่าสุด raise = ไม่เขียน · `_STORE_LOCK` เป็น RLock เพราะ `fn` เรียกตัวช่วยที่อ่าน
> `read_rows()` ซ้ำ · `write_rows()` เหลือไว้เขียนชุดใหม่ทั้งชุดเท่านั้น
> (`tests/test_user_access_lost_update.py`) · สคริปต์ `scripts/access/*` รันเป็นโปรเซสแยก
> ล็อกในโปรเซสช่วยไม่ได้ — อย่ารันตอนมีแอดมินกำลังแก้ผู้ใช้
>
> **แก้แล้ว (25 ก.ย. 2026):** `data/managers_cache.json` — เอกสารเดิมโทษ `services/managers.py`
> แต่ตัวนั้นใช้ `atomic_write_json` ใต้ `_CACHE_LOCK` มาตั้งแต่ก่อนแล้ว ตัวที่ยังเขียนด้วย
> `open(..., "w")` จริงคือ `services/access_hierarchy.py::persist_hierarchy` (เขียนทั้ง
> `config/access_hierarchy.json` และ `data/managers_cache.json`) — ย้ายมาใช้ `atomic_write_json`
> + `_PERSIST_LOCK` แล้ว ตัวอ่าน `load_hierarchy_payload` ครอบ `read_locked` ด้วย
> (`tests/test_access_hierarchy_persist.py`) · `services/fabric_cache.py` ก็ย้ายมาใช้
> `atomic_io` แล้ว (ได้ retry-on-PermissionError ตามไปด้วย)

- **export/download TOCTOU** — เขียนไฟล์ตาม sup+brand แล้วให้ client มา GET ทีหลัง
  สองคนที่ดูแล SL **และ** brand เดียวกัน (เช่น manager + supervisor) export พร้อมกันจะทับกัน
- **cache ราย SL อื่น ๆ ยังใช้ `to_csv` ตรง ๆ** (`employees.py` hist/tga_grain) — torn read ได้
  ถ้าคนโหลด SL เดียวกันพร้อมกัน
  (`employee_payload_cache.py` ย้ายมาใช้ `atomic_write_json` + `read_locked` แล้ว — เดิมใช้ tmp
  ชื่อตายตัว `f"{path}.tmp"` ซึ่งชนกันเองได้ และอ่านด้วย `open()` เปล่าซึ่งทำให้ writer พัง
  `PermissionError` บน Windows พอเขียน cache ไม่สำเร็จก็ตกไปยิง DAX ใหม่ทุกครั้ง = "แอปช้า")

## เขียน test concurrency ยังไง

ดู `tests/test_atomic_io.py` เป็นต้นแบบ — ใช้ `threading.Barrier` (ใส่ `timeout=` เสมอ
เพื่อให้ regression **fail** ไม่ใช่ค้าง CI) + `ThreadPoolExecutor` คุมเวลาต่อ test ≤ 2 วินาที

**test ต้อง fail บนโค้ดก่อนแก้จริง ๆ** ไม่งั้นแปลว่ามันไม่ได้ทดสอบอะไร
และระวัง test ที่ผลต่างกันตามระบบปฏิบัติการ — CI รัน ubuntu แต่ dev/prod เป็น Windows
(`test_readers_never_see_partial_json_even_without_lock` จึง assert เรื่อง `PermissionError`
เฉพาะเมื่อ `os.name != "nt"`)
