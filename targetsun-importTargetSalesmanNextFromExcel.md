# Import Target Salesman Next from Excel

Import next-period salesman targets from an Excel file into table `TGA_TARGET_SALESMAN_NEXT`.

---

## Endpoint (UAT)

| Item | Value |
|------|-------|
| **URL** | `https://spcuatws.sahapat.com/spc/targetsun/importTargetSalesmanNextFromExcel` |
| **Method** | `POST` |
| **Content-Type** | `multipart/form-data` |

---

## Request

| Field | Required | Description |
|-------|----------|-------------|
| `file` | Yes | Excel file `.xls` or `.xlsx` |

### cURL example

```bash
curl -X POST "https://spcuatws.sahapat.com/spc/targetsun/importTargetSalesmanNextFromExcel" \
  -F "file=@/path/to/target_salesman_next.xlsx"
```

### Postman example

1. Method: **POST**
2. URL: as in the table above
3. Body → **form-data**
4. Key: `file` → Type: **File** → select the Excel file

---

## Excel format

The first row that matches headers (e.g. `PRODUCTCODE`, `SALESMANCODE`) is skipped automatically.  
If there is no header row, use the column order below (column A = index 0).

| Column | Field | Required | Notes |
|--------|-------|----------|-------|
| A | PRODUCTCODE | Yes | Product code (6 chars) |
| B | SALESTYPE | Yes | Sales type (1 char) |
| C | DIVISIONCODE | Yes | Division code (1 char) |
| D | SALESMANCODE | Yes | Salesman code (5 chars) |
| E | AREACODE | Yes | Area code (1 char) |
| F | PROVINCECODE | Yes | Province code |
| G | WAREHOUSECODE | No | Warehouse code (4 chars) |
| H | QUANTITYCASE | Yes | Quantity in cases (numeric) |
| I | EFFECTIVEDATE | Yes | Effective date, e.g. `1/6/2569` |
| J | UPDATEDATE | No | Optional; if empty, server time is used |
| K | USERCODE | Yes | User code (5 chars) |

**Insert/update key:** `PRODUCTCODE` + `SALESTYPE` + `DIVISIONCODE` + `SALESMANCODE` + `AREACODE` + `PROVINCECODE`  
Duplicate keys within the same file are skipped.

**Supported date formats:** `d/m/Y`, `d/m/Y H:i:s`, `Y-m-d`, Buddhist year (25xx converted to CE automatically), or Excel serial dates.

---

## Response

### Success (`success: true`)

```json
{
  "success": true,
  "result": {
    "inserted": 10,
    "updated": 5,
    "skipped": 2,
    "totalRows": 17,
    "errors": [
      { "rowNum": 8, "message": "Missing required fields: USERCODE" }
    ],
    "starttime": "2026-05-25 10:00:00",
    "endtime": "2026-05-25 10:00:15",
    "durationSeconds": 15.234
  },
  "resultMsg": "importTargetSalesmanNextFromExcel success"
}
```

### Failure (`success: false`)

```json
{
  "success": false,
  "result": null,
  "resultMsg": "Required Excel file."
}
```

| Example `resultMsg` | Cause |
|---------------------|-------|
| `Required Excel file.` | No file attached |
| `Invalid file type. Allowed: xls, xlsx.` | Invalid file extension |
| Other error message | Excel read or DB failure (transaction rolled back) |

- `errors` returns at most **50** rows
- Processing timeout is about **10 minutes**

---

## Database

### UAT (UAT server uses this connection)

| Item | Value |
|------|-------|
| **Oracle host** | `10.109.9.41` |
| **Port** | `1521` |
| **Service / SID** | `db01` |
| **Laravel connection** | `spc_test` |
| **Schema user** | `dev1` |
| **Table** | `TGA_TARGET_SALESMAN_NEXT` |

### Production (reference — when deployed to production)

| Item | Value |
|------|-------|
| **Oracle host** | `10.109.10.10` |
| **Port** | `1521` |
| **Service / SID** | `db01` |
| **Laravel connection** | `oracle` |
| **Table** | `TGA_TARGET_SALESMAN_NEXT` |


### Upsert behavior

| Condition | Action |
|-----------|--------|
| Key not in DB | **INSERT** all columns |
| Key already exists | **UPDATE** only `QUANTITYCASE`, `EFFECTIVEDATE`, `UPDATEDATE`, `USERCODE` |

**Important — zero quantity:** `QUANTITYCASE = 0` is a valid value (clear a target). Import logic must **not** treat `0` as empty/missing (e.g. PHP `empty(0)` is `true` and would skip the row). If skipped count is high while Excel contains zeros, fix the import service to run `UPDATE … SET QUANTITYCASE = 0` when the key matches.

The entire batch runs in a single transaction — any error rolls back all changes.

---

## allocation_target app integration

The **Target allocation** web app calls the same import API from the backend (not from the browser):

- **Route:** `POST /lakehouse/import-targetsun` (JSON body = same payload as Excel export; server builds `.xlsx` and posts `multipart/form-data` field `file`).
- **Config:** `TARGETSUN_IMPORT_EXCEL_URL` (default: UAT URL above), `TARGETSUN_IMPORT_TIMEOUT_SEC`, optional `TARGETSUN_IMPORT_AUTH_HEADER`, `TARGETSUN_IMPORT_VERIFY_SSL`.

The UI button **TargetSun (TGA)** sends the file to this API; Oracle host for UAT remains **10.109.9.41** as configured on the SPC service side.