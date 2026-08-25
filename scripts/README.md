# Scripts

สคริปต์ช่วยดำเนินการนอก runtime ของแอป — รันจาก **รากโปรเจกต์**

## สิทธิ์ผู้ใช้ (`scripts/access/`)

```bash
python scripts/access/import_user_access_from_division_xlsx.py
python scripts/access/rebuild_access_hierarchy.py
python scripts/access/validate_access_with_dim.py
python scripts/access/repair_user_access.py
```

ดูรายละเอียด workflow ใน `config/README.md`

## พัฒนา / ทดสอบ (`scripts/dev/`)

| สคริปต์ | หน้าที่ |
|---------|---------|
| `setup.bat` | สร้าง conda env `allocation_env` |
| `start_server.bat` | รัน uvicorn (ต้อง setup ก่อน) |
| `test_powerbi_access.py` | ทดสอบ SP เห็น workspace/dataset (ไม่รัน DAX) |
| `smoke_deploy.py` | หลัง deploy — `GET /health` + unittest ชุดหลัก (`--skip-http` ถ้าไม่มี server) |
| `diagnose_supervisor.py` | ทีมหนึ่งไม่มีเป้าเลย — ไล่ดูตั้งแต่ Dim_Salesman ถึง TGA (ต่อ Fabric) |
| `diagnose_target_total.py` | เป้ารวมของทีมต่ำกว่าความจริง — ชั่งทีละสาเหตุจากไฟล์แคช (ออฟไลน์) |

```bash
python scripts/dev/diagnose_target_total.py --sup SL359 --month 9 --year 2026 --expect 40949718.68
```

## Build portable (`scripts/build/`)

| สคริปต์ | หน้าที่ |
|---------|---------|
| `build_portable_runtime.bat` | สร้าง `runtime/python/` สำหรับ `Run_Local.bat` |

## เอกสาร (`docs/`)

```bash
python docs/build-manual-html.py          # สร้าง HTML คู่มือ
python docs/build-manual-html.py --pdf    # + PDF
```
