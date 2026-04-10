# 📦 Target Allocation Dashboard

ระบบกระจายเป้ายอดขาย (หีบ) ให้พนักงานขายรายคน  
ดึงข้อมูลจาก Microsoft Fabric · คำนวณด้วย OR Engine · Export เป็น Excel

---

## โครงสร้างโฟลเดอร์

โปรเจกต์แยกตามบทบาทชัดเจน: **frontend** หน้าเว็บ, **backend** API + ธุรกิจ, **config** ตัวค่าและตัวอย่าง env (ไม่ใส่ secret ใน Git), **scripts** สคริปต์ติดตั้ง/รัน/แพ็ก, **data** ไฟล์รันไทม์และ cache

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
│   ├── main.py             # FastAPI — endpoints, static, mount frontend
│   ├── load_env.py         # โหลด config/.env แล้ว .env ที่ราก (ราก override ได้)
│   ├── auth_entra.py       # ตรวจโทเคน Microsoft / กลุ่ม Entra
│   ├── OR_engine.py        # กระจายหีบ (L3M / L6M / EVEN / PUSH / LP)
│   ├── generate_excel.py   # สร้างไฟล์ Excel สรุปผล
│   └── fabric_dax_connector.py   # Fabric / Power BI ผ่าน DAX REST
│
├── config/
│   ├── .env.example        # ตัวอย่างค่า FABRIC_* / Entra — คัดลอกเป็น config/.env
│   └── README.md           # หมายเหตุการแพ็ก .env และความปลอดภัย
│
├── scripts/
│   ├── setup.bat                    # ติดตั้ง conda env allocation_env (ครั้งแรก)
│   ├── start_server.bat             # เริ่ม server (แบบ conda)
│   ├── test_powerbi_access.py       # วินิจฉัยสิทธิ์ SP / workspace ใน Power BI REST
│   ├── build_portable_runtime.bat   # สร้าง Python ใน runtime\ (แจกแบบ portable)
│   └── build_portable_runtime.ps1   # เรียกโดย build_portable_runtime.bat
│
├── data/                   # สร้างอัตโนมัติ — ไม่ขึ้น Git (.gitignore)
│   ├── target_boxes.csv
│   ├── target_sun.csv
│   ├── app.log
│   ├── token_cache.bin     # cache MSAL (ถ้าใช้แบบ interactive)
│   └── ...                 # cache อื่น (ลบอัตโนมัติทุก 7 วัน)
│
├── .vscode/                # (ถ้ามี) การตั้งค่า workspace — ไม่บังคับ
├── requirements.txt
├── requirements-dev.txt
├── .gitignore              # ไม่รวม .env, config/.env, runtime/, data/, ...
├── .env.example            # ชี้ไปที่ config/.env.example
├── Run_Local.bat           # รัน server + เปิดเบราว์เซอร์ (runtime\ หรือ .venv)
└── readme.md
```

**ไม่ขึ้น Git แต่ใช้ตอนรัน:** `runtime\` (Python portable), `.venv`, `config/.env`, `.env` ที่ราก, โฟลเดอร์ `data\`

---

## การติดตั้ง (ครั้งแรก)

### สิ่งที่ต้องมีก่อน

| โปรแกรม | ดาวน์โหลด | หมายเหตุ |
|---------|-----------|---------|
| Miniconda | https://docs.conda.io/en/latest/miniconda.html | เลือก Windows 64-bit |
| Git | https://git-scm.com | สำหรับดึงโค้ดจาก GitHub |

> ⚠️ ตอนติดตั้ง Miniconda ต้องติ๊ก **"Add Miniconda3 to PATH environment variable"** ด้วย  
> (ค่า default ไม่ติ๊กให้ — ถ้าลืมติ๊ก `.bat` จะหา conda ไม่เจอ)

---

### ขั้นตอนติดตั้ง

**1. ดึงโค้ดจาก GitHub**

```bash
git clone https://github.com/<username>/<repo-name>.git
cd <repo-name>
```

**2. รัน setup (ครั้งเดียว)**

ดับเบิลคลิก **`scripts\setup.bat`**  
ระบบจะสร้าง conda environment `allocation_env` และติดตั้ง packages ให้อัตโนมัติ

> ถ้า `scripts\setup.bat` แจ้งว่าหา Miniconda ไม่เจอ → ให้รันด้วยมือใน Anaconda Prompt แทน:
> ```bash
> conda create -n allocation_env python=3.11 -y
> conda activate allocation_env
> pip install -r requirements.txt
> ```

---

## การตั้งค่า Fabric / Power BI (`config/.env` / `.env`)

สร้าง **`config/.env`** (อ้างอิงจาก **`config/.env.example`**) หรือ **`.env`** ที่รากโปรเจกต์ — backend โหลด **`config/.env` ก่อน** แล้วโหลด `.env` ที่รากถ้ามี (ค่าที่รากทับค่าซ้ำได้) จากนั้นตั้งตัวแปรต่อไปนี้:

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

### ล็อกอิน Microsoft (Entra) — จำกัดเฉพาะสมาชิกกลุ่ม

เมื่อตั้ง **`AZURE_AUTH_CLIENT_ID`** ใน `config/.env` หรือ `.env` ที่ราก ระบบจะบังคับให้ผู้ใช้ล็อกอิน Microsoft ก่อนเรียก API หลัก และยอมรับเฉพาะผู้ที่อยู่ใน **security group** ที่ระบุใน **`AZURE_AUTH_ALLOWED_GROUP_ID`** (ค่าเริ่มต้น: กลุ่ม `AIAgentTesting` — Object ID `06043b2d-153b-4f88-965a-8b0500ca951e`)

| ตัวแปร | คำอธิบาย |
|--------|----------|
| `AZURE_AUTH_CLIENT_ID` | **Application (client) ID** ของ App registration แบบ **Single-page application** — ใส่ Redirect URI เป็น **`http://localhost:8000/`** (Entra **ไม่ยอมรับ** `http://127.0.0.1/...` สำหรับ HTTP) |
| `AZURE_AUTH_TENANT_ID` | ทางเลือก — ถ้าว่าง ใช้ `FABRIC_TENANT_ID` |
| `AZURE_AUTH_ALLOWED_GROUP_ID` | Object ID ของกลุ่มใน Entra (ไม่ใช่ชื่อกลุ่ม) |
| `AZURE_AUTH_DISABLED=1` | ปิดการบังคับล็อกอิน (ใช้ตอนพัฒนา) |

**ถ้า Sign-in ขึ้น `AADSTS50011` (redirect URI mismatch):** ใน Entra ให้ใส่ Redirect URI ให้ตรงกับที่แอปส่ง — ค่าเริ่มต้นคือ **`http://localhost:8000/`** (มี `/` ท้าย) ภายใต้ **Single-page application**

**หมายเหตุ:** ฟอร์ม Entra จะไม่ยอมให้บันทึก `http://127.0.0.1:8000/` (ข้อความว่าต้องเป็น HTTPS หรือ `http://localhost`) — โค้ดในแอปจึงแปลง redirect เป็น `localhost` อัตโนมัติเมื่อคุณเปิดหน้าด้วย `127.0.0.1`

**ห้าม**ใส่แค่ในแท็บ **Web** ถ้าแอปใช้ MSAL แบบ SPA + PKCE — ต้องอยู่ใต้ **Single-page application** ตาม [คู่มือ redirect URI](https://aka.ms/redirectUriMismatchError)

**ใน Entra (แอปเดียวกับ client id ด้านบน):** เพิ่ม **API permissions** แบบ **Delegated** สำหรับ Microsoft Graph — อย่างน้อย **User.Read** และแนะนำ **GroupMember.Read.All** จากนั้น **Grant admin consent** เพื่อให้ backend ตรวจสมาชิกกลุ่มผ่าน `checkMemberGroups` ได้

ทางเลือก: ตั้ง **Token configuration** → เพิ่ม claim **groups** ใน **ID token** แล้ว backend จะอ่านจากโทเคนโดยไม่ต้องเรียก Graph (ระวังขนาดโทเคนถ้าผู้ใช้อยู่กลุ่มจำนวนมาก)

ไลบรารี MSAL โหลดจาก **`frontend/vendor/msal-browser.min.js`** (เสิร์ฟคู่กับแอป) เพราะ CDN `alcdn.msauth.net` มักถูกบล็อกในเครือข่ายองค์กร

---

## การใช้งาน

### เริ่ม Server

ดับเบิลคลิก **`scripts\start_server.bat`** — หรือรันด้วยมือ:

```bash
conda activate allocation_env
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### แบบ portable (แจกให้คนอื่นโดยไม่ต้องลง Python)

1. บนเครื่องผู้ดูแล (มีอินเทอร์เน็ต, Windows 64-bit): รัน **`scripts\build_portable_runtime.bat`** ครั้งหนึ่ง  
2. สร้าง **`config/.env`** จาก **`config/.env.example`** (หรือคัดลอกจากเครื่อง dev) — โฟลเดอร์นี้เหมาะกับการใส่ไฟล์ env ลง zip แจกทีม demo (อย่า commit ไฟล์จริงขึ้น Git)  
3. Zip ทั้งโฟลเดอร์โปรเจกต์ **รวม `runtime\`** และ **`config/.env`** ถ้าต้องการให้ผู้รับรันได้ทันที — แล้วส่งให้ผู้ใช้  
4. ผู้ใช้แตก zip แล้วดับเบิลคลิก **`Run_Local.bat`** — เปิด **http://localhost:8000/**

โฟลเดอร์ **`runtime\`** ไม่ขึ้น Git; ถ้า clone ใหม่ต้องสร้าง portable ใหม่หรือใช้ conda / `.venv` ตามด้านบน

### เปิด Dashboard

หลังรัน server แล้ว เปิด **http://localhost:8000/** ใน browser (แนะนำ Chrome; ใช้ล็อกอิน Microsoft ได้ — `127.0.0.1` ยังเปิดได้แต่ OAuth จะส่งกลับที่ localhost)  
หรือใช้ `Run_Local.bat` จะเปิด URL นี้ให้อัตโนมัติ

> Server ต้องรันอยู่ก่อนเสมอ — ถ้าเปิด Dashboard แล้วขึ้น error ให้เช็คว่า `Run_Local.bat` หรือ `scripts\start_server.bat` ทำงานอยู่

---

## การอัปโหลดข้อมูลเป้าหมาย (Excel Upload)

**ก่อนเข้า Dashboard ทุกเดือน** supervisor ต้องอัปโหลดข้อมูลเป้าหมายผ่านหน้า Login

### ดาวน์โหลด Template

กดลิงก์ **"⬇ ดาวน์โหลด Template"** ในหน้า Login — จะได้ไฟล์ `Target_Upload_Template.xlsx`  
ที่มี **2 sheet** พร้อม header ถูกต้องและตัวอย่างข้อมูล

### รูปแบบข้อมูล

**Sheet `target_boxes`** — เป้าหีบรายแบรนด์ (คอลัมน์ที่ต้องมี: สีน้ำเงินเข้ม)

| คอลัมน์ | จำเป็น | คำอธิบาย |
|---------|--------|----------|
| `sku` | ✅ | รหัสสินค้า |
| `price_per_box` | ✅ | ราคาต่อหีบ (บาท) ต้อง > 0 |
| `supervisor_target_boxes` | ✅ | เป้าหีบรวมทั้งทีม |
| `brand_name_thai` | - | ชื่อแบรนด์ภาษาไทย |
| `brand_name_english` | - | ชื่อแบรนด์ภาษาอังกฤษ |
| `product_name_thai` | - | ชื่อสินค้าภาษาไทย |

**Sheet `target_sun`** — เป้าเงินรายพนักงาน

| คอลัมน์ | จำเป็น | คำอธิบาย |
|---------|--------|----------|
| `emp_id` | ✅ | รหัสพนักงาน |
| `target_sun` | ✅ | เป้าเงินตั้งต้น (บาท) |

> ⚠️ ยอดรวม `target_sun` ทุกคนควรเท่ากับ `price_per_box × supervisor_target_boxes` รวมทุก SKU

### วิธีอัปโหลด

| วิธี | ขั้นตอน |
|-----|---------|
| **ไฟล์เดียว 2 sheet** | อัปโหลดที่ช่อง "เป้าหีบ SKU" — ระบบอ่านทั้งคู่อัตโนมัติ |
| **แยก 2 ไฟล์** | อัปโหลดคนละช่อง |
| **Drag & Drop** | ลากไฟล์มาวางบนกล่องอัปโหลดได้เลย |

หลังอัปโหลดสำเร็จ ระบบจะแสดงสรุป เช่น `SKU 6 รายการ · มูลค่ารวม 1,250,000 บาท`

---

## ขั้นตอนการใช้งาน Dashboard

```
[Login] เลือก Supervisor + เดือน/ปี + อัปโหลด Excel เป้าหมาย
        ↓
[Step 1] ตรวจสอบข้อมูลพนักงาน + SKU ที่ดึงจาก Fabric
        ↓
[Step 2] ปรับเป้าเงินรายพนักงาน (ยอดรวมต้องตรงกับเป้ารวม)
        ↓
[Step 3] เลือก Strategy แล้วกด "กระจายหีบ"
        ↓
[Step 4] ตรวจสอบผล / แก้หีบด้วยมือ (ระบบเกลี่ยส่วนต่างให้อัตโนมัติ)
        ↓
[Export] ดาวน์โหลด Excel สรุปผล
```

### Strategy ที่มีให้เลือก

| Strategy | ใช้เมื่อ |
|----------|---------|
| **L3M** | กระจายตามยอดขายเฉลี่ย 3 เดือนล่าสุด (แนะนำ) |
| **L6M** | กระจายตามยอดขายเฉลี่ย 6 เดือน (เรียบกว่า) |
| **EVEN** | เกลี่ยเท่ากันทุกคน |
| **PUSH** | ผลักดันคนขายน้อย (ให้หีบมากกว่าปกติ) |
| **LP** | Linear Programming ตามเป้าเงิน (แม่นยำสุด แต่ช้ากว่า) |

---

## API Endpoints

| Method | Path | หน้าที่ |
|--------|------|---------|
| `GET` | `/data/employees` | ดึงพนักงาน + SKU + ประวัติจาก Fabric |
| `POST` | `/optimize` | คำนวณกระจายหีบตาม strategy |
| `POST` | `/upload/targets` | อัปโหลด Excel เป้าหมาย (boxes/sun/both) |
| `GET` | `/upload/template` | ดาวน์โหลด Excel template |
| `POST` | `/export/excel` | สร้างไฟล์ Excel สรุปผล |
| `GET` | `/download/excel` | ดาวน์โหลด Excel ที่สร้างแล้ว |
| `GET` | `/managers` | ดึงรายชื่อ Supervisor ทั้งหมด |
| `GET` | `/health` | ตรวจสอบสถานะ server |
| `GET` | `/debug/fabric` | debug การเชื่อมต่อ Fabric |

Swagger UI: http://localhost:8000/docs

---

## การอัปเดตโค้ด

```bash
git pull
```

ไม่ต้องรัน `scripts\setup.bat` ใหม่ — รัน `Run_Local.bat` หรือ `scripts\start_server.bat` ได้เลย

---

## แก้ปัญหาเบื้องต้น

| อาการ | สาเหตุ | วิธีแก้ |
|-------|--------|---------|
| `conda is not recognized` | ยังไม่ได้ลง Miniconda หรือไม่ได้ติ๊ก Add to PATH | ติดตั้ง Miniconda ใหม่ ติ๊ก Add to PATH |
| Fabric / ล็อกอินไม่ทำงานหลังแตก zip | ไม่มี `config/.env` หรือ `.env` ที่ราก | วางไฟล์ตาม **`config/.env.example`** แล้วรีสตาร์ท server |
| Dashboard ขึ้น error เชื่อมต่อ | Server ไม่ได้รัน | เปิด `Run_Local.bat` หรือ `scripts\start_server.bat` ก่อน |
| อัปโหลด Excel แล้วขึ้น error คอลัมน์ | ชื่อ header ไม่ตรง | ดาวน์โหลด Template แล้วกรอกข้อมูลตาม |
| หน้าเว็บแสดงผลไม่ครบ | ข้อมูลจาก Fabric ดึงไม่ได้ | เช็ค log ที่ `data/app.log` |
| Dropdown Supervisor ว่าง | Fabric ไม่ตอบหรือไม่มี SuperCode ใน `dim_salesman` | ล็อกอิน Microsoft ให้สำเร็จ, ตรวจสิทธิ์ dataset, หรือพิมพ์ SuperCode เอง |
| ติดตั้งช้ามาก | conda กำลัง solve dependencies | รอได้เลย อาจใช้เวลา 5-10 นาที |

Log file อยู่ที่ `data/app.log` — เปิดดูได้ทุกเมื่อ

---

## รายชื่อ Supervisor (SuperCode)

รายการใน dropdown มาจาก **Microsoft Fabric / Power BI semantic model** — ดึงค่า `SuperCode` ที่ไม่ซ้ำจากตาราง `dim_salesman` ผ่าน API `GET /managers`

- ถ้าเพิ่ม Supervisor ใหม่ใน **ข้อมูลฝั่ง Fabric** แล้ว refresh โมเดล — รอบหน้าที่เรียก `/managers` จะเห็นรหัสใหม่ (หรือกดโหลดรายการใหม่ในหน้า Login)
- ถ้า Fabric ตอบไม่ได้ แต่เคยสำเร็จมาก่อน ระบบใช้ไฟล์ cache `data/managers_cache.json`
- ถ้าได้รายการว่าง ยัง **พิมพ์ SuperCode เอง** ในช่อง Supervisor ได้ — ระบบจะลองดึงพนักงานจาก `dim_salesman` ตามรหัสนั้น

---

## การล็อกอิน Microsoft (ใช้งานกับ Fabric)

Backend ใช้ **MSAL (Public Client)** ล็อกอิน Microsoft แล้วขอ token ไปเรียก **Power BI REST API** (`executeQueries`) กับ dataset ที่ตั้งใน `FABRIC_DATASET_ID` (หรือค่า default ใน `fabric_dax_connector.py`)

**โดยทั่วไป:** ใช้บัญชี **อีเมลองค์กร (Microsoft Entra ID)** ที่ **มีสิทธิ์เข้าถึง workspace / dataset นั้น** (เช่น สมาชิก workspace, หรือได้รับสิทธิ์ Build บน dataset) แล้วล็อกอินผ่านหน้าต่างเบราว์เซอร์ที่ MSAL เปิดให้ — หลังสำเร็จ token เก็บที่ `data/token_cache.bin` รอบถัดไปจะไม่ต้องล็อกอินบ่อย

**ไม่ใช่**แค่ “มีเมลองค์กร” อย่างเดียว — ต้อง **ได้รับสิทธิ์บน Power BI/Fabric จริง** ตามนโยบายองค์กร ถ้าไม่มีสิทธิ์ query จะ error 403/401

ตั้งค่าเฉพาะองค์กร (แนะนำสำหรับ production): ตั้ง environment variables `FABRIC_CLIENT_ID`, `FABRIC_TENANT_ID`, `FABRIC_DATASET_ID` ให้ตรงกับแอปลงทะเบียนและ dataset ของคุณ

---

*Target Allocation Dashboard v3 · Python 3.11 · FastAPI · Microsoft Fabric*