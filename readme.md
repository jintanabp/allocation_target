# 📦 Target Allocation Dashboard

ระบบกระจายเป้ายอดขาย (หีบ) ให้พนักงานขายรายคน  
ดึงข้อมูลจาก **Microsoft Fabric** · เป้าจาก **TGA (semantic model)** · คำนวณด้วย OR Engine · **ส่งผลเข้าระบบเป้า TargetSun (SPC)** หรือดาวน์โหลด Excel

**การใช้งานจริง:** พัฒนาแล้ว **push ขึ้น GitHub** → server บริษัท deploy อัตโนมัติ — ผู้ใช้เปิด URL บน server (ไม่ต้องติดตั้งแอปบนเครื่องตัวเอง) · ดู [Deploy ผ่าน GitHub → Server บริษัท](#deploy-ผ่าน-github--server-บริษัท-แนวทางหลัก)

---

## โครงสร้างโฟลเดอร์

โปรเจกต์แยกตามบทบาทชัดเจน: 
**frontend** หน้าเว็บ, 
**backend** API + ธุรกิจ, 
**config** ตัวค่าและตัวอย่าง env (ไม่ใส่ secret ใน Git),
**scripts** สคริปต์ช่วย dev / วินิจฉัย (ไม่จำเป็นบน server บริษัทถ้า deploy จาก Git)
**data** ไฟล์รันไทม์และ cache

```
allocation_target/
├── frontend/
│   ├── index.html          # หน้า Dashboard
│   ├── app.js              # Logic ฝั่ง frontend ทั้งหมด
│   ├── style.css           # สไตล์ UI
│   └── vendor/
│       └── msal-browser.min.js   # MSAL (เสิร์ฟคู่แอป — ไม่พึ่ง CDN)
│
├── backend/
│   ├── main.py             # Uvicorn entrypoint + mount frontend (สั้น)
│   ├── app_factory.py      # ประกอบ FastAPI app + routers + middleware
│   ├── deps.py             # Dependencies (ล็อกอิน, สิทธิ์ sup, สิทธิ์ส่ง Target Sun)
│   ├── schemas.py          # Pydantic models ของ request/response
│   ├── routers/            # แยก endpoints (auth, managers, data, optimize, export, lakehouse, health, debug …)
│   ├── services/           # business logic (employees, optimize, export, lakehouse, targetsun_import, access_control …)
│   ├── core/               # helpers shared (paths/constants/cache checks/targets loader)
│   ├── load_env.py         # โหลด config/.env แล้ว .env ที่ราก (ราก override ได้)
│   ├── auth_entra.py       # ตรวจโทเคน Microsoft (สิทธิ์รหัสจาก ACC_USER_CONTROL + services/access_control)
│   ├── OR_engine.py        # กระจายหีบ (L3M / L6M / EVEN / PUSH / LP)
│   ├── generate_excel.py   # สร้างไฟล์ Excel สรุปผล
│   └── fabric_dax_connector.py   # Fabric / Power BI ผ่าน DAX REST
│
├── config/
│   ├── .env.example              # ตัวอย่างตัวแปร (ไม่มี secret) — คัดลอกเป็น `config/.env`
│   ├── acc_local_test.json       # dev: จำลอง ACC + รายชื่ออีเมลที่ส่ง Target Sun ได้ (ดู README)
│   └── README.md                 # หมายเหตุการแพ็ก .env และความปลอดภัย
│
├── scripts/
│   ├── setup.bat                    # ติดตั้ง conda env allocation_env (ครั้งแรก)
│   ├── start_server.bat             # เริ่ม server (แบบ conda)
│   ├── test_powerbi_access.py       # วินิจฉัยสิทธิ์ SP / workspace ใน Power BI REST
│   ├── build_portable_runtime.bat   # สร้าง Python ใน runtime\ (แจกแบบ portable)
│   └── build_portable_runtime.ps1   # เรียกโดย build_portable_runtime.bat
│
├── data/                   # สร้างอัตโนมัติ — ไม่ขึ้น Git (.gitignore)
│   ├── target_boxes.csv        # cache เป้าหีบจาก Fabric (Step 1) — สร้างอัตโนมัติ
│   ├── target_sun.csv          # cache เป้าเงินรายคนจาก Fabric (Step 1)
│   ├── app.log
│   ├── token_cache.bin     # cache MSAL (ถ้าใช้แบบ interactive)
│   └── ...                 # cache อื่น (hist_cache_*, tga_lines_* ตามงวด, ลบอัตโนมัติทุก 7 วัน)
│                           # tga_lines_* = grain เป้า TGA ต่อ emp×SKU จากขั้นที่ 1 (ใช้ตอนส่ง Target Sun)
│
├── .vscode/                # (ถ้ามี) การตั้งค่า workspace — ไม่บังคับ
├── requirements.txt
├── requirements-dev.txt
├── .gitignore              # ไม่รวม .env, config/.env, runtime/, data/, ...
├── .env.example            # ตัวอย่างสำหรับ override ที่ราก (แนะนำใช้ `config/.env` เป็นหลัก)
├── Run_Local.bat           # (ทางเลือก) รันบนเครื่อง dev — ไม่ใช้บน server บริษัท
├── targetsun-importTargetSalesmanNextFromExcel.md   # คู่มือ API ส่ง Excel เข้า TargetSun (multipart)
└── readme.md
```

**ไม่ขึ้น Git:** `config/.env`, `.env` ที่ราก, `runtime\`, `.venv`, โฟลเดอร์ `data\` (cache / log บน server)

---

## Deploy ผ่าน GitHub → Server บริษัท (แนวทางหลัก)

โปรเจกต์นี้ใช้งานจริงบน **server ภายในองค์กร** — ทีมพัฒนา **push ขึ้น GitHub** แล้ว pipeline / กระบวนการ deploy ของบริษัทดึงโค้ดไปรันบน server (ไม่ต้องให้ผู้ใช้ติดตั้ง Miniconda หรือแจก zip portable)

### สิ่งที่ทีมพัฒนาทำ (Git)

1. พัฒนาและ commit ขึ้น branch ที่องค์กรกำหนด (เช่น `main`)
2. **อย่า commit** `config/.env`, secret, หรือ `data/` ขึ้น repo (มีใน `.gitignore` แล้ว)
3. ถ้าเพิ่ม package ใน `requirements.txt` — แจ้งทีม ops ให้ server รัน `pip install -r requirements.txt` หลัง deploy (หรือให้ pipeline ทำอัตโนมัติ)

### สิ่งที่ตั้งบน Server (ครั้งแรก / เมื่อเปลี่ยน environment)

| รายการ | หมายเหตุ |
|--------|----------|
| **`config/.env` บน server** | คัดลอกจาก `config/.env.example` แล้วกรอกค่า Fabric, Entra, Target Sun UAT/Prod — **เก็บเฉพาะบน server** |
| **Python 3.11+** + `pip install -r requirements.txt` | ตามมาตรฐานที่ทีม infra ใช้ (venv / service account) |
| **รันแอป** | เช่น `uvicorn backend.main:app --host 0.0.0.0 --port <port>` หรือ service ที่บริษัทกำหนด |
| **URL จริงของแอป** | ผู้ใช้เปิดผ่านโดเมน/พอร์ตบริษัท — ตั้ง **Redirect URI** ใน Entra ให้ตรง URL นี้ (ไม่ใช่แค่ `localhost`) |
| **`data/`** | สร้างอัตโนมัติเมื่อรัน — เก็บ cache (`target_boxes.csv`, `tga_lines_*`, …), `app.log` |

### หลัง push โค้ดใหม่

- Pipeline บริษัท deploy โค้ดล่าสุด → **รีสตาร์ท process แอป** (หรือ rolling deploy ตามที่ infra กำหนด)
- ถ้า `requirements.txt` เปลี่ยน → ติดตั้ง dependency บน server ก่อนเปิด service
- ค่าใน **`config/.env` บน server ไม่หาย** เมื่อ deploy โค้ด (ไม่อยู่ใน Git) — แก้ env แยกเมื่อเปลี่ยน UAT/Prod หรือ secret

### ผู้ใช้งาน (พนักงานขาย / Supervisor)

- เปิด **URL ของแอปบน server บริษัท** ใน browser
- ล็อกอิน Microsoft ตามที่ตั้งใน `AZURE_AUTH_CLIENT_ID`
- ไม่ต้อง clone repo หรือรัน `Run_Local.bat`

---

## การตั้งค่า Fabric / Power BI (`config/.env` บน server)

ตั้งบน **server** (ไม่ commit): คัดลอก **`config/.env.example` → `config/.env`** แล้วกรอกค่า — backend โหลด **`config/.env` ก่อน** แล้วโหลด `.env` ที่รากโปรเจกต์ถ้ามี (ค่าที่รากทับค่าซ้ำได้)

> **ทดสอบบนเครื่องตัวเอง (ทางเลือก):** ใช้ `scripts\setup.bat` + `scripts\start_server.bat` หรือ `Run_Local.bat` — ดูหัวข้อท้าย README

ตัวแปรหลัก:

| ตัวแปร | ความจำเป็น | คำอธิบาย |
|--------|------------|----------|
| `FABRIC_TENANT_ID` | จำเป็น | Directory (tenant) ID ของ Azure AD |
| `FABRIC_CLIENT_ID` | จำเป็น | Application (client) ID ของแอปที่ลงทะเบียนใน Entra |
| `FABRIC_DATASET_ID` | จำเป็น | ID ของ semantic model / dataset ใน Power BI ที่ใช้รัน DAX |
| `FABRIC_WORKSPACE_ID` | ทางเลือก | **Workspace (group) ID** — ถ้าใส่ จะเรียก API แบบ `groups/{id}/datasets/...` (ช่วยให้สอดคล้องกับสิทธิ์ใน workspace) |
| `FABRIC_CLIENT_SECRET` | ทางเลือก | Client secret ของแอป — **ถ้าใส่** ระบบใช้ **Service Principal** (client credentials) **ไม่เปิดเบราว์เซอร์** |

**โหมดล็อกอิน**

- **ไม่มี** `FABRIC_CLIENT_SECRET` (หรือว่าง): ใช้ล็อกอินผู้ใช้แบบ interactive / cache ที่ `data/token_cache.bin` เหมือนเดิม
- **มี** `FABRIC_CLIENT_SECRET`: ใช้ SP เท่านั้น — ต้องให้ tenant อนุญาต service principal ใช้ Power BI และเพิ่มแอปเป็น **Member** หรือ **Admin** ของ workspace ที่มี dataset นั้น (ตามนโยบายองค์กร)

**ถ้าได้ error `PowerBIEntityNotFound` / HTTP 404 ตอนดึงข้อมูล:** มักเป็นอย่างใดอย่างหนึ่งต่อไปนี้

1. **`FABRIC_CLIENT_ID` ไม่ใช่แอปเดียวกับที่เพิ่มใน workspace** — ใน Entra เปิดแอปที่ใช้ secret นี้ ดู **Application (client) ID** ให้ตรงกับ `FABRIC_CLIENT_ID` ใน `config/.env` หรือ `.env` ที่ราก (ถ้าใช้แอปชื่ออื่น เช่น MyFabricBotApp แต่ env ยังเป็น client id เก่า จะได้ token ของแอปผิดตัว → 404)
2. **ยังไม่ได้ให้สิทธิ์แอปใน Entra กับ Power BI API** — ที่ App registration → **API permissions** → **Add** → **Power BI Service** → เลือก **Application permissions** → อย่างน้อย **Dataset.Read.All** → **Grant admin consent for [tenant]**
3. **ยังไม่ได้เพิ่มแอปใน workspace** — Fabric / Power BI → workspace ของโมเดล → **Manage access** → เพิ่มแอป (หรือ service principal) เป็น **Member** หรือ **Admin**
4. **Tenant settings** — Power BI Admin Portal → อนุญาตให้ **service principals** ใช้ Power BI REST API ตามนโยบายองค์กร
5. **Dataset / workspace ID** — Dataset settings คัดลอก **Dataset ID** ใส่ `FABRIC_DATASET_ID`; จาก URL Fabric ใส่ `FABRIC_WORKSPACE_ID` (segment `…/groups/{workspace-id}/…` หรือใน URL แบบ `…/details/{workspace-id}/dataset/…`)

**ยัง 404 ทั้งที่เช็คครบแล้ว:** รันสคริปต์วินิจฉัย (โหลด env เหมือน backend — `config/.env` แล้ว `.env` ที่ราก) จากรากโปรเจกต์:

`python scripts/test_powerbi_access.py`

จะเรียก Power BI REST แบบ **GET** ว่า Service Principal เห็นรายการ dataset ใน workspace และเห็น dataset ตาม `FABRIC_DATASET_ID` หรือไม่ — ถ้า **ไม่มี** `fac4dff8-…` ในรายการ แปลว่า **workspace ID หรือ dataset ID ไม่ตรงกับสิ่งที่ Power BI API เห็น** (หรือ SP ยังไม่มีสิทธิ์ใน workspace นั้น)

**GET สำเร็จ แต่ `executeQueries` ยัง 404 (`PowerBIEntityNotFound`):** เป็นไปได้ว่า semantic model เป็น **composite** หรือมี **upstream semantic model** — ตาม [กรณีที่ Microsoft Fabric Community อธิบาย](https://community.fabric.microsoft.com/t5/Developer/Power-BI-REST-API-returns-PowerBIEntityNotFound-for-dataset-with/m-p/5004110) REST `executeQueries` อาจล้มเหลวแม้ caller เป็น admin ขณะที่ **XMLA** ยังใช้ได้ — ทางออกเชิงผลิตภัณฑ์: ชี้ `FABRIC_DATASET_ID` ไปที่ **dataset ต้นทาง (import)** ที่ REST รองรับ, ปรับโมเดลไม่ให้พึ่ง dataset ซ้อน dataset, หรือใช้ **XMLA** สำหรับ query โปรแกรม (ต้องพัฒนาเส้นทางแยก — ยังไม่มีใน repo นี้)

`backend/load_env.py` (เรียกจาก `main.py` และ `fabric_dax_connector.py`) โหลด **`config/.env`** แล้ว **`.env`** ที่รากอัตโนมัติ (ต้องติดตั้ง `python-dotenv` ผ่าน `requirements.txt`)

### ตัวแปรสภาพแวดล้อมเสริม (ทางเลือก)

| ตัวแปร | ค่าเริ่มต้น / พฤติกรรม | คำอธิบาย |
|--------|-------------------------|----------|
| `MANAGERS_CACHE_TTL_SEC` | `86400` | อายุ cache ของ `GET /managers` (วินาที) — หมดอายุแล้วจะลองดึงจาก Fabric ใหม่ |
| `ENABLE_DEBUG_ENDPOINTS` | ปิด | ตั้งเป็น `1` / `true` / `yes` เพื่อเปิด `GET /debug/fabric` (ใช้เฉพาะตอนวินิจฉัย) |
| `USE_LEGACY_TARGET_CSV` | ปิด | **dev เท่านั้น** — ตั้ง `1` ถ้ามี `data/target_boxes.csv` + `target_sun.csv` วางมือเองแทนการดึงเป้าจาก Fabric (ไม่มี UI อัปโหลดในแอป) |
| `LP_HIST_ANCHOR` | `0.15` | น้ำหนัก anchor ประวัติในกลยุทธ์ **LP** (`backend/OR_engine.py`) |
| `TGA_TABLE_NAME`, `TGA_COL_*`, `TGA_FILTER_BY_EFFECTIVE`, **`TGA_EFFECTIVE_IMPLIED_TARGET`** (`same`\|`next`), `TGA_ENFORCE_EFFECTIVE_WINDOW`, `TGA_COL_EFFECTIVE_FALLBACK` | `fabric_dax_connector.py`, `backend/core/tga_period.py`, `config/.env.example` | เป้า TGA และกติกางวดจาก **EFFECTIVEDATE** (ค่าเริ่มต้น `same` = งวดเป้าตรงเดือนเดียวกับวันที่อ้างอิง; `next` = เดือนถัดจากวันที่เหมือนพฤติกรรมเก่า); fallback `UPDATEDATE` เมื่อ EFFECTIVEDATE ว่าง |
| **`TARGETSUN_IMPORT_EXCEL_URL`**, `TARGETSUN_IMPORT_TIMEOUT_SEC`, `TARGETSUN_IMPORT_VERIFY_SSL`, `TARGETSUN_IMPORT_AUTH_HEADER` | `config/.env.example`, `backend/services/targetsun_import.py` | หลังกระจายหีบแล้ว **ส่งไฟล์รูปแบบ TGA** ไป API ฝั่ง SPC (Oracle ที่ service กำหนด — ไม่ต่อ DB จากโค้ดแอปนี้) — รายละเอียดใน `targetsun-importTargetSalesmanNextFromExcel.md` |
| **`LAKEHOUSE_COL_*`**, `ONELAKE_*` | `config/.env.example`, `backend/services/lakehouse.py` | ชื่อคอลัมน์ grain + **`POST /lakehouse/upload`** ส่ง CSV เข้า OneLake (API เท่านั้น — ไม่มีปุ่มใน UI; ผู้ใช้ทั่วไปใช้ **ส่งเข้า Target Sun**) |

### ล็อกอิน Microsoft (Entra) และสิทธิ์ ACC_USER_CONTROL

เมื่อตั้ง **`AZURE_AUTH_CLIENT_ID`** ใน `config/.env` หรือ `.env` ที่ราก ระบบจะบังคับให้ผู้ใช้ล็อกอิน Microsoft ก่อนเรียก API  
สิทธิ์เข้ากระจายเป้ากำหนดจากตาราง **Fabric `ACC_USER_CONTROL`** — เปรียบเทียบ `[EMAIL]` ในแถวกับอีเมลจากบัญชี Microsoft และใช้คอลัมน์ **`USERPL`** ว่าตรงกับ **รหัส Supervisor** (`trf_select_supervisor` / `Dim_Salesman`) หรือกับ **รหัส Manager** (DEPENDON) หรือไม่ — หากเป็น Manager ผู้ใช้จะกระจายได้เฉพาะ Supervisor ภายใต้ Manager เดียวกับที่ใช้อยู่ในระบบ (แถว `EMAIL`/`USERPL` ซ้ำใน `ACC_USER_CONTROL` ถือเป็นรายการเดียวในการตัดสิน)  
ถ้าระบุ **`ALLOCATION_ADMIN_EMAILS`** ใน `config/.env` — อีเมลเหล่านั้นเข้ามองเลือกรหัส Supervisor/Manager ได้เหมือนไม่ผูก ACC (เหมาะสำหรับผู้ดูแลระบบ; ควบคุมสิทธิ์จากไฟล์ env / pipeline deploy)

**ไม่บังคับ membership ใน security group ใน Entra** แล้ว (ฟิลด์ `AZURE_AUTH_ALLOWED_GROUP_ID` ไม่ใช้ในโค้ดฉบับนี้)

| ตัวแปร | คำอธิบาย |
|--------|----------|
| `AZURE_AUTH_CLIENT_ID` | **Application (client) ID** ของ App registration แบบ **Single-page application** — ใส่ Redirect URI เป็น **`http://localhost:8000/`** (Entra **ไม่ยอมรับ** `http://127.0.0.1/...` สำหรับ HTTP) |
| `AZURE_AUTH_TENANT_ID` | ทางเลือก — ถ้าว่าง ใช้ `FABRIC_TENANT_ID` |
| `ACC_USER_CONTROL_CACHE_TTL_SEC` | แคชการดึง `ACC_USER_CONTROL` จาก Fabric (ค่าเริ่มต้นประมาณ 300 วินาที) — เปลี่ยนข้อมูลใน Fabric แล้วอยากเห็นทันที ตั้งเป็น `0` ช่วงทดสอบ |
| `ALLOCATION_ADMIN_EMAILS` | ทางเลือก — รายการอีเมล (คั่นด้วย comma) ที่เข้ามองได้ทุกรหัสและเรียก API ได้ทุก `sup_id` โดยไม่ต้องอยู่ใน `ACC_USER_CONTROL` |
| `ALLOCATION_ALLOW_ACC_DEV_JSON` + `ACC_USER_CONTROL_DEV_JSON` | ทางเลือก **dev เท่านั้น** — เปิดเป็น `1` และชี้ path JSON จำลองแทนการดึง ACC จาก Fabric (เช่น `config/acc_local_test.json`) |
| **`config/acc_local_test.json`** | ไม่ใช่ env — รายการ `{email, userpl}` สำหรับ dev; **อีเมลในไฟล์นี้** (และ path ใน `ACC_USER_CONTROL_DEV_JSON` ถ้ามี) ใช้กำหนด **ใครกดส่ง Target Sun ได้** คู่กับ `ALLOCATION_ADMIN_EMAILS` (ดูหัวข้อด้านล่าง) |
| `AZURE_AUTH_DISABLED=1` | ปิดการบังคับล็อกอิน (ใช้ตอนพัฒนา) |

**ถ้า Sign-in ขึ้น `AADSTS50011` (redirect URI mismatch):** ใน Entra ให้ใส่ Redirect URI ให้ตรงกับที่แอปส่ง — ค่าเริ่มต้นคือ **`http://localhost:8000/`** (มี `/` ท้าย) ภายใต้ **Single-page application**

**หมายเหตุ:** ฟอร์ม Entra จะไม่ยอมให้บันทึก `http://127.0.0.1:8000/` (ข้อความว่าต้องเป็น HTTPS หรือ `http://localhost`) — โค้ดในแอปจึงแปลง redirect เป็น `localhost` อัตโนมัติเมื่อคุณเปิดหน้าด้วย `127.0.0.1`

**ห้าม**ใส่แค่ในแท็บ **Web** ถ้าแอปใช้ MSAL แบบ SPA + PKCE — ต้องอยู่ใต้ **Single-page application** ตาม [คู่มือ redirect URI](https://aka.ms/redirectUriMismatchError)

**ใน Entra (แอปเดียวกับ client id ด้านบน):** เพิ่ม **API permissions** แบบ **Delegated** สำหรับ Microsoft Graph — อย่างน้อย **User.Read** เพื่อให้ backend อ่านอีเมลจากโทเคน / Graph ได้

ทางเลือก: ตั้ง **Token configuration** → เพิ่ม optional claim **email** ใน **ID token** ถ้าโทเคนไม่มีอีเมลชัดเจน

#### ทดสอบว่า dropdown แสดงแค่ USERPL ของอีเมล (ทำใน dev)

1. **ล็อกอินด้วยจริง** — `AZURE_AUTH_CLIENT_ID`/`TENANT` เปิด และ **อย่า**ใส่อีเมลทดสอบใน **`ALLOCATION_ADMIN_EMAILS`** (ไม่งั้นจะเห็นทุกรหัส)
2. **ทางเลือก A — Fabric จริง:** ใน `ACC_USER_CONTROL` เพียงอย่างน้อยแถวหนึ่งให้ **`[EMAIL]`** ตรงกับบัญชี Microsoft และใส่เฉพาะ **`USERPL`** ที่อยากเห็นจากนั้นรีสตาร์ท server — มีแคช ใช้ `ACC_USER_CONTROL_CACHE_TTL_SEC=0` หรือ redeploy เพื่อล้างแถวเก่าชั่วคราว
3. **ทางเลือก B — ไฟล์จำลอง (เร็ว):**  
   `ALLOCATION_ALLOW_ACC_DEV_JSON=1` และ  
   `ACC_USER_CONTROL_DEV_JSON=config/acc_local_test.json` (รูปแบบ `[{"email":"...","userpl":"SL330"}]` — อีเมลต้องตรงบัญชีที่ล็อกอิน) แล้วรีสตาร์ท  
   เปิดหน้า login → เลื่อกจาก dropdown → จะเห็นเฉพาะ **`X (Supervisor)` / `Y (Manager)`** ที่มาจาก USERPL ในไฟล์  
   จาก DevTools ดู **`GET /managers`** Response: ฟิลด์ **`managers`** ต้องเป็นรายการ labels ถูกกรอง และ **`filtered_by_userpl_only`** เป็น **true**

ไลบรารี MSAL โหลดจาก **`frontend/vendor/msal-browser.min.js`** (เสิร์ฟคู่กับแอป) เพราะ CDN `alcdn.msauth.net` มักถูกบล็อกในเครือข่ายองค์กร

---

## การใช้งาน (บน Server บริษัท)

1. เปิด **URL แอปที่ทีม infra แจ้ง**
2. หน้า **เข้าสู่ระบบ** → ล็อกอิน Microsoft → เลือกผู้รับผิดชอบ + งวด → **เข้าสู่ระบบ Dashboard**
3. ทำ **ขั้นที่ 1 → 2 (ถ้าต้องการ) → 3** แล้วดาวน์โหลด Excel หรือ **ส่งเข้า Target Sun** (ถ้ามีสิทธิ)

**ตรวจสอบว่าแอปรันอยู่:** `GET <URL>/health` · **API docs:** `<URL>/docs` · **Log:** `data/app.log`  
**คู่มือในแอป:** ปุ่มลอย **คู่มือ** มุมขวาบน Dashboard (อ่านทีละขั้น ~4 หน้า)

---

## หน้าจอและขั้นตอน (ตรงกับ UI)

### หน้าเข้าสู่ระบบ

| องค์ประกอบ | รายละเอียด |
|------------|------------|
| **ล็อกอินด้วย Microsoft** | แสดงเมื่อตั้ง `AZURE_AUTH_CLIENT_ID` — ต้องล็อกอินก่อนเลือกทีม |
| **ผู้รับผิดชอบ (Supervisor / Manager)** | Dropdown จาก `GET /managers` (รอโหลดเสร็จก่อนกดเข้าระบบ) · ปุ่ม **↻ รีเฟรชรายการ** ถ้าดึงไม่สำเร็จ |
| **งวดเดือนที่จะกระจายเป้า** | เลือกเดือน/ปี — **ค่าเริ่มต้น = เดือนถัดจากวันนี้** |
| **เข้าสู่ระบบ Dashboard** | โหลดข้อมูลทีมจาก Fabric (`GET /data/employees`) แล้วเปิด Dashboard |

> ไม่มีการอัปโหลดไฟล์ Excel เป้าหมายบนหน้านี้ — เป้าดึงจาก Fabric / TGA อัตโนมัติ

### Dashboard — ภาพรวม flow

```
[Login] ล็อกอิน Microsoft → เลือก Supervisor/Manager + งวด → เข้าสู่ระบบ Dashboard
        ↓
[ขั้นที่ 1 ข้อมูลตั้งต้น] เป้ารวม · ตารางพนักงาน · ตาราง SKU (โหลดจาก Fabric แล้ว cache ใน data/)
        ↓
[ขั้นที่ 2 กำหนดเป้าหมาย] (optional) ปรับเป้าเงินรายคน · หักบิวเทรี่ยม · ยอดรวมต้องใกล้เป้ารวม
        ↓
[ขั้นที่ 3 กระจายหีบ] เลือกวิธี (หลายวิธีได้) → เริ่มคำนวณ → แก้หีบในตารางผล
        ↓
[หลังคำนวณ] บันทึกร่าง · Undo · ↓ ดาวน์โหลด Excel · 📤 ส่งเข้า Target Sun
```

บน Dashboard มี **สลับ Supervisor** (เปลี่ยนทีมโดยไม่ต้อง logout) และปุ่ม **ออกจากระบบ** ที่แถบบน

> ข้อความ UI เป็นภาษาไทย — คำ **Supervisor** / **Manager** ยังเป็นภาษาอังกฤษตามระบบเดิม

### ขั้นที่ 1 — ข้อมูลตั้งต้น

- แสดง **เป้ารวม** งวดที่เลือก — ถ้าไม่ตรง ใช้ลิงก์ **ติดต่อ IT** ใต้เป้ารวม
- **เป้าหมายตั้งต้นรายพนักงาน:** แท็บ **เทียบเฉลี่ย 3 เดือน** / **เทียบปีที่แล้ว**
- **เป้าหีบราย SKU:** จัดกลุ่ม **ราย SKU** · **แบรนด์** · **Section**
- Backend เขียน cache: `target_boxes.csv`, `target_sun.csv`, `tga_lines_{SuperCode}_{year}_{month}.csv`

### ขั้นที่ 2 — กำหนดเป้าหมาย *(optional)*

- แก้คอลัมน์ **เป้าหมายที่กำหนดเอง** (เริ่มต้นเท่า **เป้า Target Sun**) — **ข้ามขั้นนี้ได้**
- **หักบิวเทรี่ยม** — เปิดช่องหักจากยอดปีที่แล้วก่อนคำนวณ % เติบโต
- **รีเซ็ตเป็น Target Sun** — คืนค่าเป้าที่กำหนดเองให้เท่า Target Sun ทุกคน
- ยอดรวมเป้าที่กำหนดเอง: ต่างจากเป้ารวม **ไม่เกิน ~10 บาท** = พร้อมกดกระจาย · **ไม่เกิน ~99 บาท** = แจ้งเตือนแต่ยังกดได้ · มากกว่านั้น = ปุ่ม **เริ่มคำนวณ** ปิด
- ถ้ามีคนที่เป้าทำให้ **% เติบโตติดลบ** — ต้องกรอกเหตุผลอย่างน้อย **8 ตัวอักษร** ก่อนกด **เริ่มคำนวณ**

### ขั้นที่ 3 — กระจายหีบ

**วิธีที่เลือกได้บนหน้าจอ** (เลือกได้มากกว่า 1 — กำหนด **แบรนด์ → วิธี** เมื่อเลือกหลายวิธี):

| รหัส | ชื่อใน UI |
|------|-----------|
| **L3M** | ยอดขายเฉลี่ย 3 เดือนย้อนหลัง (ค่าเริ่มต้น) |
| **L6M** | ยอดขายเฉลี่ย 6 เดือนย้อนหลัง |
| **LY** | เดือนเดียวกันปีที่แล้ว |
| **PUSH** | ผลักดันพนักงาน |

ตัวเลือกเพิ่ม: **บังคับให้ทุกคนได้อย่างน้อย 1 หีบ (ต่อ SKU)** · **SKU ใหม่แบ่งเท่ากัน** (ทีมไม่มียอด 12 เดือน)

กด **เริ่มคำนวณ** (หลังเสร็จเปลี่ยนเป็น **คำนวณใหม่**) — แสดง progress 4 ขั้น แล้วตาราง **ผลลัพธ์การกระจายหีบ** (คลิกตัวเลขสีน้ำเงินแก้หีบ · ระบบเกลี่ยส่วนต่างให้)

> **EVEN** / **LP** ยังรองรับใน backend แต่ **ซ่อนใน UI** ชั่วคราว

กลยุทธ์ **L3M / L6M / LY / PUSH** ใช้สัดส่วนจากประวัติ + **เกลี่ยยอดเงิน (revenue balancer)** ให้ใกล้เป้ารายคน โดยคงหีบรวมต่อ SKU

### คอลัมน์ประวัติในผลลัพธ์ (หน้า Dashboard / Export)

API `POST /optimize` และไฟล์ Excel สามารถมีฟิลด์ประกอบการตัดสินใจดังนี้ (เมื่อ backend ส่งมาจาก Fabric):

- **`hist_avg`** — ค่าเฉลี่ยหีบในช่วงที่ใช้เกลี่ย (เช่น 3M / 6M)
- **`hist_ly_same_month`** — หีบรวมเดือนเดียวกับงวด แต่ปีก่อน (emp×sku)
- **`hist_prev_month`** — หีบเดือนก่อนงวด (emp×sku)

### แหล่งข้อมูลหลักใน Fabric (ย่อ)

- **ราคาต่อหีบ (รวมจากข้อมูล Fabric เมื่อดึง Step 1):** DAX อ่านราคาเครดิตจาก **`cfm_product_characteristic`** (`CREDITUNITPRICE` เป็นหลัก, คัดจาก `PRODUCTCODE` และช่วง `FROMDATE`/`TODATE` ให้ครอบคลุมงวด) — **SKU ในกลุ่มประวัติ:** จาก **`cross_sold_history_2y_qu`** ตามพนักงานและช่วงเดือน
- **Supervisor → พนักงาน:** ตาราง **`Dim_Salesman`** ใช้คอลัมน์ **`SuperCode`** จับคู่กับรหัส Supervisor ที่เลือกในแอป (รายละเอียด dropdown อยู่ที่มุมมอง **`trf_select_supervisor`**)

### ปุ่มหลังคำนวณ (ขั้นที่ 3)

| ปุ่ม | การทำงาน |
|------|----------|
| **💾 บันทึกร่าง** | เก็บผลใน browser (localStorage) — ไม่แทนการส่งเข้า Target Sun |
| **↩️ Undo** | ย้อนการแก้หีบล่าสุดในตารางผล |
| **↓ ดาวน์โหลด Excel** | เปิด modal เลือกแบรนด์ → Excel **สรุปผล Dashboard** (`POST /export/excel`) |
| **📤 ส่งเข้า Target Sun** | เปิด modal → ส่งอัตโนมัติ (`POST /lakehouse/import-targetsun`) หรือ **ดาวน์โหลด Excel อย่างเดียว** (รูปแบบ TGA) |

### Excel รูปแบบ TGA (`tga_target_salesman_next`)

- **ชีตเดียวชื่อ `TGA`** — 11 คอลัมน์: `PRODUCTCODE`, `SALESTYPE`, `DIVISIONCODE`, `SALESMANCODE`, `AREACODE`, `PROVINCECODE`, `WAREHOUSECODE`, `QUANTITYCASE`, `EFFECTIVEDATE`, `UPDATEDATE`, `USERCODE` (รูปแบบเดียวกับไฟล์อ้างอิง `alloc_*.xlsx`)
- **Grain จากเป้า TGA:** `SALESTYPE`, `DIVISIONCODE`, `AREACODE`, `PROVINCECODE` มาจาก cache ขั้นที่ 1 (`data/tga_lines_{SuperCode}_{year}_{month}.csv`) — **ไม่เติมค่า dim เอง** ถ้าไม่มี grain แถวนั้นจะไม่ส่งและแจ้งผู้ใช้
- **ส่งเฉพาะผลขั้นที่ 3:** ไม่ขยาย matrix พนักงาน×SKU ทั้งทีม — ส่งแค่คู่ที่ปรากฏในตารางผลหลังกระจายหีบ (รวมแถว `QUANTITYCASE=0` ที่จำเป็นต่อการทับเป้าเดิมใน Oracle)
- **`tga_lines` cache:** เมื่อดึงพนักงาน (Step 1) ระบบเขียน grain ลง **`data/tga_lines_*.csv`** — **รักษา `AREACODE=0`** ตาม Fabric (ไม่ตัดเป็นค่าว่าง)

### สิทธิ์ส่งเข้า Target Sun (UAT / ทดสอบ)

| ใคร | กระจายหีบ / ใช้แอป | กด **ส่งเข้า Target Sun** |
|-----|---------------------|---------------------------|
| อีเมลใน **`ALLOCATION_ADMIN_EMAILS`** | ✅ (ทุกรหัส) | ✅ |
| อีเมลใน **`config/acc_local_test.json`** (หรือไฟล์ที่ `ACC_USER_CONTROL_DEV_JSON` ชี้) | ✅ (ตาม ACC / dev JSON) | ✅ |
| ผู้ใช้อื่นที่ล็อกอินได้ | ✅ | ❌ ปุ่มเทา / API 403 |

- ไม่ต้องตั้ง env แยกสำหรับ allowlist — **แก้รายชื่ออีเมลใน JSON** แล้วรีสตาร์ท server
- รูปแบบ JSON: `[{"email":"user@sahapat.co.th","userpl":"SL330"}, ...]`
- **`POST /lakehouse/export-csv`** (ดาวน์โหลด Excel อย่างเดียว) — ยังใช้ได้ตามสิทธิ์ Supervisor ปกติ (ไม่จำกัดแคบเท่าการส่ง)
- Frontend อ่าน **`can_import_targetsun`** จาก **`GET /managers`** หลังล็อกอิน

### ส่งผลเข้า TargetSun (SPC)

- กด **📤 ส่งเข้า Target Sun** → ใน modal กด **ส่งเข้า Target Sun** (ไม่ต้องแนบไฟล์) → **`POST /lakehouse/import-targetsun`**
- Backend สร้าง Excel ในหน่วยความจำ (ใช้ **xlsxwriter** ถ้าติดตั้งแล้ว) แล้ว POST **multipart** ไป **`TARGETSUN_IMPORT_EXCEL_URL`** (ค่าเริ่มต้น UAT — ดู `targetsun-importTargetSalesmanNextFromExcel.md`)
- **แอปไม่เชื่อม Oracle โดยตรง** — insert/update อยู่ฝั่งบริการ SPC หลัง import สำเร็จ
- คู่ที่ไม่มี grain ใน TGA ณ ตอนส่ง → **ไม่ส่ง** และแสดงจำนวนใน response / modal (**ไม่มีใน Target Sun ณ ตอนนี้**)
- **Performance (ดู `data/app.log`):** แยกเวลา `build_xlsx` (เตรียมข้อมูล + Excel) กับ `post_upstream` (รอ UAT) — ถ้า grain cache ครบทุกแถว ระบบข้าม Fabric DAX ซ้ำ; ถ้ายังมีแถว dim ว่างจะดึง Fabric ~2–3 วินาทีก่อนส่ง

ถ้ารับ **502**, **timeout**, หรือ **SSL** — response มี `hint_th` และ toast ภาษาไทยใน UI

---

## API Endpoints

| Method | Path | หน้าที่ |
|--------|------|---------|
| `GET` | `/auth/config` | เปิดการล็อกอิน MSAL และ tenant/public client id |
| `GET` | `/data/employees` | ดึงพนักงาน + SKU + เป้า/ประวัติจาก Fabric (Step 1) |
| `POST` | `/optimize` | คำนวณกระจายหีบตาม strategy |
| `POST` | `/export/excel` | สร้างไฟล์ Excel สรุปผล (Dashboard) |
| `GET` | `/download/excel` | ดาวน์โหลด Excel ที่สร้างแล้ว |
| `POST` | `/lakehouse/export-csv` | ดาวน์โหลด Excel คอลัมน์ **`tga_target_salesman_next`** (รวมแถว `QUANTITYCASE=0`) |
| `POST` | `/lakehouse/import-targetsun` | ส่ง Excel TGA ไป **`importTargetSalesmanNextFromExcel`** (ต้องมีสิทธิ์ admin หรืออีเมลใน allowlist JSON) |
| `POST` | `/lakehouse/upload` | ส่งผลเข้า **OneLake** (API/ops — ไม่มีใน UI; ดู `ONELAKE_*`) |
| `GET` | `/managers` | ดึงรายชื่อ Supervisor / Manager (+ ฟิลด์ **`can_import_targetsun`** หลังล็อกอิน) |
| `GET` | `/health` | ตรวจสอบสถานะ server |
| `GET` | `/debug/fabric` | debug Fabric (ต้องตั้ง `ENABLE_DEBUG_ENDPOINTS=1`) |

Swagger UI: `<URL แอปบน server>/docs`

---

## การอัปเดตโค้ด

| บทบาท | สิ่งที่ทำ |
|--------|----------|
| **พัฒนา** | `git push` ขึ้น GitHub → รอ deploy บน server |
| **Ops / Server** | ดึงโค้ดล่าสุด, `pip install -r requirements.txt` ถ้า dependencies เปลี่ยน, รีสตาร์ท service |
| **Env** | แก้ `config/.env` **บน server** โดยตรง (ไม่ผ่าน Git) เมื่อเปลี่ยน secret / URL Target Sun |

`config/.env` และ `data/` บน server **ไม่ถูกทับ** เมื่อ deploy โค้ดใหม่จาก Git

---

## แก้ปัญหาเบื้องต้น

| อาการ | สาเหตุ | วิธีแก้ |
|-------|--------|---------|
| หลัง deploy แล้วแอป error | service ไม่รีสตาร์ท / ขาด `config/.env` บน server | รีสตาร์ท process; ตรวจ `config/.env` บน server |
| ล็อกอิน Microsoft ไม่ได้ (redirect) | Redirect URI ใน Entra ไม่ตรง URL จริง | เพิ่ม URL แอปบน server ใน App registration (SPA) |
| Fabric / ดึงข้อมูลไม่ได้ | secret / workspace / dataset ผิดบน server | ตรวจ `config/.env`; รัน `python scripts/test_powerbi_access.py` บน server |
| Dashboard ขึ้น error เชื่อมต่อ | แอปไม่รันหรือ reverse proxy ผิด | เช็ค `/health`; ตรวจพอร์ตและ service |
| Step 1 โหลดไม่ได้ / เป้าว่าง | Fabric ไม่ตอบหรือไม่มีเป้า TGA งวดนั้น | ตรวจ dataset / งวด; ดู `data/app.log` และ `GET /debug/fabric` (ถ้าเปิด) |
| Dropdown Supervisor ว่าง | Fabric ไม่ตอบหรือ cache เก่า | ล็อกอินสำเร็จ, ตรวจ dataset; ลด `MANAGERS_CACHE_TTL_SEC` หรือลบ `data/managers_cache.json` บน server |
| ส่งเข้า TargetSun แล้ว **502 / Timeout / SSL error** | server → UAT ไม่ถึง / SSL | ตรวจ `TARGETSUN_IMPORT_*` ใน `config/.env` บน server; ดู `data/app.log` (`post_upstream`) |
| ปุ่ม **ส่งเข้า Target Sun** เป็นสีเทา | ไม่มีสิทธิส่ง หรือยังไม่มีผลขั้นที่ 3 | แก้ allowlist บน server (`acc_local_test.json` / `ALLOCATION_ADMIN_EMAILS`); กด "เริ่มคำนวณ" ก่อน |
| ส่งแล้วแจ้ง **ไม่มีใน Target Sun** บางคู่ | ไม่มี grain ใน cache ขั้นที่ 1 | โหลด Step 1 ใหม่ แล้วกระจายหีบอีกครั้ง |
| push แล้ว feature ใหม่ error import | ยังไม่ได้ `pip install` บน server | รัน `pip install -r requirements.txt` บน server แล้วรีสตาร์ท |

Log: **`data/app.log`** บน server (path ตาม working directory ของ service)

---

## รายชื่อ Supervisor / Manager (`GET /managers`)

รายการใน dropdown มาจาก **Microsoft Fabric** — ดึงแถวจากมุมมอง/ตาราง **`trf_select_supervisor`** (รวมโค้ด Supervisor และ Manager ที่เกี่ยวข้อง) ผ่าน API `GET /managers`

- **ตอน startup** ถ้า backend เชื่อม Fabric ได้ (เช่น ตั้ง `FABRIC_CLIENT_SECRET` เป็น Service Principal) ระบบจะพยายาม **preload** รายการลง `data/managers_cache.json` เพื่อให้หลังผู้ใช้ล็อกอิน Microsoft แล้วโหลดหน้า Login ได้เร็วขึ้น (ภายใน `MANAGERS_CACHE_TTL_SEC`)
- ถ้าเพิ่ม Supervisor ใหม่ใน **ข้อมูลฝั่ง Fabric** แล้ว refresh โมเดล — รอบหน้าที่เรียก `/managers` (หรือหมด TTL ของ cache) จะเห็นรหัสใหม่ (หรือกดโหลดรายการใหม่ในหน้า Login)
- ถ้า Fabric ตอบไม่ได้ แต่เคยสำเร็จมาก่อน ระบบใช้ไฟล์ cache `data/managers_cache.json`
- ถ้าได้รายการว่าง ยัง **พิมพ์รหัส Supervisor เอง** ในช่องได้ — ระบบจะลองดึงพนักงานตามรหัสนั้น

---

## Fabric: Service Principal vs ล็อกอินผู้ใช้ (แยกจาก Entra ของแอป)

| โหมด | เมื่อไหร่ | หมายเหตุ |
|------|----------|----------|
| **Service Principal** | มี `FABRIC_CLIENT_SECRET` บน server | ดึง DAX โดยไม่เปิด browser · เหมาะ production |
| **ล็อกอินผู้ใช้ (interactive)** | ไม่มี client secret | token เก็บ `data/token_cache.bin` — บัญชีต้องมีสิทธิ์ workspace/dataset |

**ล็อกอิน Microsoft บนหน้าแอป** (`AZURE_AUTH_*`) ใช้ตรวจสิทธิ์ผู้ใช้และ ACC — **ไม่ใช่** token เดียวกับการ query Fabric เสมอไป (บน server มักใช้ SP แยก)

ตั้งค่าใน **`config/.env` บน server:** `FABRIC_*`, `AZURE_AUTH_*`, `TARGETSUN_IMPORT_*`

---

## พัฒนาบนเครื่องตัวเอง (ทางเลือก — ไม่ใช่ flow บริษัท)

ใช้เมื่อแก้โค้ดก่อน push GitHub เท่านั้น:

1. `git clone` + `scripts\setup.bat` (conda `allocation_env`) หรือ venv / `Run_Local.bat`
2. สร้าง **`config/.env`** ในเครื่อง (อย่า commit)
3. `uvicorn backend.main:app --host 127.0.0.1 --port 8000` → http://localhost:8000/

สคริปต์ `build_portable_runtime.bat` / แจก zip — **ไม่จำเป็น** ถ้า deploy ผ่าน server บริษัทแล้ว

---

*Target Allocation Dashboard · Python 3.11+ · FastAPI · Microsoft Fabric · Target Sun (SPC) · Deploy ผ่าน GitHub*