# ENV Checklist สำหรับ IT (Production)

ใช้ตารางนี้ตรวจก่อน deploy / หลังย้ายเซิร์ฟเวอร์  
**หมายเหตุ:** URL Target Sun (อ่านเป้า / ส่งผล) **ไม่ใส่ใน .env** — ตั้งใน `backend/services/targetsun_endpoints.py` และสลับ preset จากแอดมิน (`config/app_runtime.json`)

## บังคับ (Production)

| ตัวแปร | หมายเหตุ |
|--------|----------|
| `FABRIC_TENANT_ID` | Azure AD tenant |
| `FABRIC_CLIENT_ID` | Service principal สำหรับ Fabric API |
| `FABRIC_CLIENT_SECRET` | เก็บใน secret store — อย่า commit |
| `FABRIC_DATASET_ID` | Semantic model dataset |
| `FABRIC_WORKSPACE_ID` | Workspace ที่มี model |
| `AZURE_AUTH_CLIENT_ID` | Entra app สำหรับ login ผู้ใช้ |
| `AZURE_AUTH_TENANT_ID` | Tenant เดียวกับองค์กร |
| `ALLOCATION_ADMIN_EMAILS` | อีเมลแอดมิน (คั่นด้วย comma) |
| `ALLOCATIONS_DATA_DIR` | Volume เก็บ snapshot กระจายหีบ |
| `USAGE_LOGS_DIR` | Volume เก็บ usage log |
| `FABRIC_CACHE_DIR` | Volume เก็บ cache DAX/hist |
| `USER_ACCESS_JSON_PATH` | ค่าแนะนำ: `config/user_access.json` |

## แนะนำ

| ตัวแปร | ค่าแนะนำ |
|--------|----------|
| `AZURE_AUTH_DISABLED` | `0` (production ต้องเปิด login) |
| `TARGETSUN_READ_ENABLED` | `1` |
| `TARGETSUN_READ_FALLBACK_FABRIC` | `1` |
| `EMPLOYEE_PAYLOAD_CACHE_TTL_SEC` | `3600` |
| `TARGETSUN_IMPORT_TIMEOUT_SEC` | `600` |
| `TARGETSUN_READ_TIMEOUT_SEC` | `120` |
| `USER_ACCESS_CACHE_TTL_SEC` | `300` |
| `MANAGERS_CACHE_TTL_SEC` | `86400` |

## Path ของไฟล์ config (ถ้าไม่ใช้ default)

| ตัวแปร | Default |
|--------|---------|
| `ACCESS_HIERARCHY_JSON_PATH` | `config/access_hierarchy.json` |
| `SL_LINKS_JSON_PATH` | `config/sl_links.json` |
| `SKU_LINKS_JSON_PATH` | `config/sku_links.json` |
| `APP_RUNTIME_SETTINGS_PATH` | `config/app_runtime.json` |

## Target Sun — ไม่ใช่ .env

| การตั้งค่า | ที่อยู่ |
|------------|--------|
| Default Read/Send URL | `backend/services/targetsun_endpoints.py` |
| Runtime preset (ทดสอบ / UAT / Prod) | แอดมิน → แท็บแหล่งข้อมูล → `config/app_runtime.json` |
| ก่อน go-live | ตั้ง preset **Production ทั้งคู่** หรือแก้ constant เป็น `spcws` ทั้งคู่ |

## ตรวจหลัง deploy

1. `GET /health` → `status: ok`
2. Login MSAL ได้
3. แอดมิน → แหล่งข้อมูล → ตรวจ Read URL / Send URL ตรงที่ต้องการ
4. รัน `python scripts/dev/smoke_deploy.py --base-url https://<host>`

ดูรายละเอียดตัวแปรทั้งหมดใน `config/.env.example`
