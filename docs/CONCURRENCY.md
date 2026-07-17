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

## lock ที่มีอยู่ในระบบ

| ไฟล์ | lock | กันอะไร |
|---|---|---|
| `core/atomic_io.py` | `RLock` ต่อ path | torn read + `os.replace` ชนกัน (Windows) |
| `services/allocation_store.py` | `_STORE_LOCK` (**RLock**) | CAS + `mark_sent_targetsun` RMW |
| `services/user_access_store.py` | `_STORE_LOCK` | ⚠️ จับแยกใน read/write — **ยังมี lost update** |
| `services/fabric_cache.py` | `_LOCK` | เขียน cache |
| `services/app_runtime_settings.py` | `_LOCK` | เขียน settings |
| `services/usage_log_store.py` | `_LOCK` | append/rewrite jsonl |
| `services/sl_link_store.py` / `sku_link_store.py` | `_LOCK` | เขียน links |

## ที่ยังไม่ได้แก้ (รู้อยู่)

- **`config/user_access.json` lost update** — `routers/admin.py` ทำ `read_rows()` → แก้ → `write_rows()`
  แต่ `_STORE_LOCK` ถูกจับ *แยกกัน* ข้างในแต่ละฟังก์ชัน ไม่ได้ถือคร่อม RMW
  admin 2 คนแก้คนละ user พร้อมกัน → การแก้ของคนหนึ่งหาย
- **`data/managers_cache.json`** — `services/managers.py` เขียนด้วย `open(..., "w")` ตรง ๆ
  ไม่มี temp+replace อยู่บน hot path ของ login ทุกครั้ง (มี try/except รองรับ → แค่ช้าลง ไม่พัง)
- **export/download TOCTOU** — เขียนไฟล์ตาม sup+brand แล้วให้ client มา GET ทีหลัง
  สองคนที่ดูแล SL **และ** brand เดียวกัน (เช่น manager + supervisor) export พร้อมกันจะทับกัน
- **cache ราย SL อื่น ๆ ยังใช้ `to_csv` ตรง ๆ** (`employees.py` hist/tga_grain, `employee_payload_cache.py`
  ที่ใช้ tmp name ตายตัว `f"{path}.tmp"`) — torn read ได้ถ้าคนโหลด SL เดียวกันพร้อมกัน

## เขียน test concurrency ยังไง

ดู `tests/test_atomic_io.py` เป็นต้นแบบ — ใช้ `threading.Barrier` (ใส่ `timeout=` เสมอ
เพื่อให้ regression **fail** ไม่ใช่ค้าง CI) + `ThreadPoolExecutor` คุมเวลาต่อ test ≤ 2 วินาที

**test ต้อง fail บนโค้ดก่อนแก้จริง ๆ** ไม่งั้นแปลว่ามันไม่ได้ทดสอบอะไร
และระวัง test ที่ผลต่างกันตามระบบปฏิบัติการ — CI รัน ubuntu แต่ dev/prod เป็น Windows
(`test_readers_never_see_partial_json_even_without_lock` จึง assert เรื่อง `PermissionError`
เฉพาะเมื่อ `os.name != "nt"`)
