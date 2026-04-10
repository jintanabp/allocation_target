"""
main.py — Target Allocation API (v3 — Production)
────────────────────────────────────────────────────────────────────
uvicorn backend.main:app --reload  (จากรากโปรเจกต์)

การไหลของข้อมูล:
  1. GET  /data/employees  → ดึงพนักงาน + SKU + LY + 3M hist จาก Fabric
  2. POST /optimize        → OR engine ตาม strategy
  3. POST /export/excel    → สร้าง Excel (กรองรายแบรนด์ได้)
  4. GET  /download/excel  → ดาวน์โหลดไฟล์ที่สร้าง
"""

import os
import re
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path

from .load_env import load_project_dotenv

load_project_dotenv()

from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import pandas as pd
from .OR_engine import allocate_boxes
from .generate_excel import create_target_excel
from . import auth_entra
from .fabric_dax_connector import FabricDAXConnector

# ── Logging ──────────────────────────────────────────────
os.makedirs("data", exist_ok=True) 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/app.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("target_allocation")


def require_entra_member(
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """ถ้าเปิด Entra auth — ต้องส่ง Bearer token และอยู่ในกลุ่ม AZURE_AUTH_ALLOWED_GROUP_ID"""
    if not auth_entra.auth_enabled():
        return {}
    if not authorization or not authorization.lower().startswith("bearer "):
        logger.info("Entra auth: missing bearer token")
        raise HTTPException(
            status_code=401,
            detail="กรุณาล็อกอินด้วย Microsoft (กดปุ่มล็อกอินก่อน)",
        )
    token = authorization[7:].strip()
    try:
        return auth_entra.verify_bearer_and_group(token)
    except PermissionError as e:
        logger.info("Entra auth: forbidden: %s", str(e))
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        logger.info("Entra auth: invalid token: %s", str(e))
        raise HTTPException(status_code=401, detail=str(e))


# ── Startup helpers (ต้อง define ก่อน app เพราะ FastAPI ต้องการ lifespan ตอน init) ──
def _cleanup_old_caches(max_age_days: int = 7):
    cutoff = datetime.now() - timedelta(days=max_age_days)
    try:
        for fname in os.listdir("data"):
            if fname.startswith(("hist_cache_", "emp_cache_")):
                fpath = os.path.join("data", fname)
                if datetime.fromtimestamp(os.path.getmtime(fpath)) < cutoff:
                    os.remove(fpath)
                    logger.info("Cleaned old cache: %s", fname)
    except Exception as e:
        logger.warning("Cache cleanup error: %s", e)

@asynccontextmanager
async def lifespan(app_: FastAPI):
    os.makedirs("data", exist_ok=True)
    _cleanup_old_caches(max_age_days=7)
    yield

# ── App ───────────────────────────────────────────────────
app = FastAPI(title="Target Allocation API", version="3.0", lifespan=lifespan)

if auth_entra.auth_enabled():
    gid = (
        os.environ.get("AZURE_AUTH_ALLOWED_GROUP_ID")
        or "06043b2d-153b-4f88-965a-8b0500ca951e"
    ).strip()
    logger.info(
        "Entra login เปิดใช้งาน — กลุ่มที่อนุญาต object id: %s…",
        gid[:8],
    )

# CORS — allow_credentials=False เพราะไม่ได้ใช้ cookie/session
# ⚠️ allow_credentials=True + allow_origins=["*"] ผิด HTTP spec
#    Starlette จะ drop CORS headers เงียบๆ → browser block ทุก request จาก file://
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Fallback price per SKU ────────────────────────────────
PRICE_FALLBACK = {
    "624007": 240.00,
    "624015": 212.00,
    "624049": 335.00,
    "624056": 290.00,
    "624114": 212.00,
    "624163": 232.71,
}

VALID_STRATEGIES = ("L3M", "L6M", "EVEN", "PUSH", "LP")

# Production: ตั้ง ENABLE_DEBUG_ENDPOINTS=1 เท่านั้นเมื่อต้องการเปิด GET /debug/fabric
_ENABLE_DEBUG = os.environ.get("ENABLE_DEBUG_ENDPOINTS", "").strip().lower() in ("1", "true", "yes")

# ══════════════════════════════════════════════════════════
#  Pydantic models
# ══════════════════════════════════════════════════════════
class YellowTargetInput(BaseModel):
    emp_id: str
    yellow_target: float = Field(ge=0)

class LockedEditInput(BaseModel):  # ข้อ 11
    emp_id: str
    sku: str
    locked_boxes: int = Field(ge=0)

class OptimizeRequest(BaseModel):
    yellowTargets: list[YellowTargetInput]
    strategy: str = "L3M"
    force_min_one: bool = False
    locked_edits: list[LockedEditInput] = []
    cap_multiplier: float | None = None  # Custom strategy override (1.5–5.0)

class AllocationRow(BaseModel):
    emp_id: str
    sku: str
    allocated_boxes: int = Field(ge=0)
    hist_avg: float = 0.0
    price_per_box: float = 0.0
    brand_name_thai: str = ""
    brand_name_english: str = ""
    product_name_thai: str = ""

class ExportRequest(BaseModel):
    allocations: list[AllocationRow]
    brand_filter: str = "ALL"
    yellow_targets: list[YellowTargetInput] = []


# ══════════════════════════════════════════════════════════
#  Helpers: safe path builders (prevent path traversal)
# ══════════════════════════════════════════════════════════
def _safe_id(s: str) -> str:
    """Sanitize sup_id / strategy สำหรับใส่ใน filename"""
    return re.sub(r"[^A-Za-z0-9_]", "_", str(s))

def hist_cache_path(sup_id: str, month: int, year: int, n_months: int = 3) -> str:
    """
    3 เดือน: data/hist_cache_{sup}_{year}_{mm}.csv (รูปแบบเดิม)
    6 เดือน: data/hist_cache_{sup}_{year}_{mm}_6m.csv (สำหรับกลยุทธ์ L6M)
    """
    base = f"data/hist_cache_{_safe_id(sup_id)}_{year}_{month:02d}"
    if n_months == 3:
        return f"{base}.csv"
    return f"{base}_{int(n_months)}m.csv"

def emp_cache_path(sup_id: str, month: int, year: int) -> str:
    return f"data/emp_cache_{_safe_id(sup_id)}_{year}_{month:02d}.csv"

def result_path(sup_id: str) -> str:
    return f"data/final_allocation_{_safe_id(sup_id)}.csv"

def excel_path(sup_id: str) -> str:
    return f"data/Final_Dashboard_{_safe_id(sup_id)}.xlsx"

# ══════════════════════════════════════════════════════════
#  Helpers: Export
# ══════════════════════════════════════════════════════════

def export_result_path(sup_id: str, brand: str) -> str:
    brand_safe = _safe_id(brand) if brand != "ALL" else "ALL"
    return f"data/export_{_safe_id(sup_id)}_{brand_safe}.csv"


def _validate_allocation_vs_targets(df_alloc: pd.DataFrame, df_sku: pd.DataFrame) -> list[dict]:
    """ตรวจว่าผลรวมหีบที่กระจายแล้วต่อ SKU ตรงกับ supervisor_target_boxes หรือไม่"""
    if df_alloc.empty or df_sku is None or df_sku.empty:
        return []
    df_a = df_alloc.copy()
    df_a["sku"] = df_a["sku"].astype(str).str.strip()
    sums = df_a.groupby("sku", as_index=True)["allocated_boxes"].sum()
    out: list[dict] = []
    for _, row in df_sku.iterrows():
        sku = str(row["sku"]).strip()
        try:
            tgt = int(round(float(row.get("supervisor_target_boxes", 0) or 0)))
        except (TypeError, ValueError):
            tgt = 0
        got = int(sums[sku]) if sku in sums.index else 0
        if got != tgt:
            out.append({
                "sku": sku,
                "expected_boxes": tgt,
                "allocated_sum": got,
                "message": f"SKU {sku}: กระจายรวม {got} หีบ แต่เป้าหีบจากหัวหน้า {tgt} หีบ",
            })
    return out


# ══════════════════════════════════════════════════════════
#  Helpers: CSV loaders
# ══════════════════════════════════════════════════════════
def load_target_csv() -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    df_sku = df_sun = None
    if os.path.exists("data/target_boxes.csv"):
        df_sku = (
            pd.read_csv("data/target_boxes.csv", dtype={"sku": str})
            .dropna(subset=["sku"])
            .fillna(0)
        )
        df_sku["sku"] = df_sku["sku"].astype(str).str.strip()
    if os.path.exists("data/target_sun.csv"):
        df_sun = (
            pd.read_csv("data/target_sun.csv", dtype={"emp_id": str})
            .dropna(subset=["emp_id"])
            .fillna(0)
        )
        df_sun["emp_id"] = df_sun["emp_id"].astype(str).str.strip()
    return df_sku, df_sun


def _build_sku_and_sun_from_tga(
    df_tga: pd.DataFrame,
    df_product: pd.DataFrame,
    emp_list: list,
    sku_list: list,
) -> tuple[pd.DataFrame, pd.DataFrame, set[str]]:
    """
    จาก TGA (จำนวนหีบ = QUANTITYCASE ต่อคู่ emp×sku):
    - supervisor_target_boxes ต่อ SKU = SUM หีบของทีมต่อ SKU
    - target_sun ต่อคน = SUM(หีบ × ราคา/หีบจาก dim_product) รายพนักงาน
    """
    sku_list = [str(s).strip() for s in sku_list]
    team_set = set(str(e).strip() for e in emp_list)

    df_p = df_product.copy() if df_product is not None and not df_product.empty else pd.DataFrame()
    if not df_p.empty:
        df_p["sku"] = df_p["sku"].astype(str).str.strip()

    sum_dict: dict[str, float] = {}
    emp_with_tga: set[str] = set()
    if df_tga is not None and not df_tga.empty:
        d = df_tga.copy()
        d["emp_id"] = d["emp_id"].astype(str).str.strip()
        d["sku"] = d["sku"].astype(str).str.strip()
        sub = d[d["emp_id"].isin(team_set)]
        emp_with_tga = set(sub["emp_id"].unique())
        sum_dict = sub.groupby("sku")["qty"].sum().to_dict()

    rows_sku: list[dict] = []
    for sku in sku_list:
        row_p = df_p[df_p["sku"] == sku] if not df_p.empty else pd.DataFrame()
        price = 0.0
        brand_th = brand_en = pname = ""
        if not row_p.empty:
            r0 = row_p.iloc[0]
            price = float(r0.get("unit_cost") or r0.get("price_per_box") or 0)
            brand_th = str(r0.get("brand_name_thai", "") or "")
            brand_en = str(r0.get("brand_name_english", "") or "")
            pname = str(r0.get("product_name_thai", "") or "")
        if price <= 0:
            price = float(PRICE_FALLBACK.get(sku, 0.0) or 0.0)
        sup_boxes = int(round(float(sum_dict.get(sku, 0))))
        rows_sku.append({
            "sku": sku,
            "price_per_box": price,
            "supervisor_target_boxes": max(0, sup_boxes),
            "brand_name_thai": brand_th,
            "brand_name_english": brand_en,
            "product_name_thai": pname,
        })

    df_sku = pd.DataFrame(rows_sku)
    price_by_sku = dict(zip(df_sku["sku"].astype(str), df_sku["price_per_box"]))

    sun_map: dict[str, float] = {str(e).strip(): 0.0 for e in emp_list}
    if df_tga is not None and not df_tga.empty:
        d = df_tga.copy()
        d["emp_id"] = d["emp_id"].astype(str).str.strip()
        d["sku"] = d["sku"].astype(str).str.strip()
        d["price"] = d["sku"].map(lambda s: float(price_by_sku.get(str(s).strip(), 0.0)))
        d["line_value"] = d["qty"] * d["price"]
        g = d.groupby("emp_id", as_index=True)["line_value"].sum()
        for emp in sun_map:
            if emp in g.index:
                sun_map[emp] = round(float(g[emp]), 2)

    df_sun = pd.DataFrame([{"emp_id": k, "target_sun": v} for k, v in sun_map.items()])
    return df_sku, df_sun, emp_with_tga


# ══════════════════════════════════════════════════════════
#  GET /auth/config — ค่าสาธารณะสำหรับ MSAL (ไม่มี secret)
# ══════════════════════════════════════════════════════════


@app.get("/auth/config")
def auth_public_config():
    return auth_entra.spa_config_payload()


@app.get("/favicon.ico", include_in_schema=False)
def favicon_placeholder():
    """ลด 404 ใน log — เบราว์เซอร์ขอ /favicon.ico อัตโนมัติ"""
    return Response(status_code=204)


# ══════════════════════════════════════════════════════════
#  GET /managers (ดึงรายชื่อ Super Code ทั้งหมดแบบอัตโนมัติ)
# ══════════════════════════════════════════════════════════


@app.api_route("/manegers", methods=["GET", "HEAD"], include_in_schema=False)
def managers_common_typo():
    """พิมพ์ผิดบ่อย (manegers) — redirect ไป /managers"""
    return RedirectResponse(url="/managers", status_code=307)


@app.get("/managers")
def get_managers(_user: dict = Depends(require_entra_member)):
    os.makedirs("data", exist_ok=True)
    cache_path = "data/managers_cache.json"
    
    try:
        fabric = FabricDAXConnector()
        managers = fabric.get_all_super_codes()
        
        if managers:
            # เซฟเก็บไว้ในไฟล์ เผื่อรอบหน้า Power BI ตอบช้า
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(managers, f)
            return {"managers": managers}
            
    except Exception as e:
        logger.warning("get_all_super_codes error: %s", e)
        
    # ถ้ายิง Fabric ไม่สำเร็จ ให้โหลดจาก Cache ล่าสุดมาใช้แทน
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return {"managers": json.load(f)}
        except Exception as cache_err:
            logger.warning("managers cache corrupt: %s", cache_err)
            
    # ไม่มีรายชื่อจาก Fabric และไม่มี cache — คืน [] ให้ผู้ใช้พิมพ์ SuperCode เอง
    logger.warning("ไม่มีรายชื่อ Supervisor — ตรวจสอบการล็อกอิน Fabric และสิทธิ์ dataset")
    return {"managers": []}


# ══════════════════════════════════════════════════════════
#  GET /data/employees
# ══════════════════════════════════════════════════════════
@app.get("/data/employees")
def get_employees(
    _user: dict = Depends(require_entra_member),
    sup_id:       str  = Query(..., description="SuperCode เช่น SL330"),
    target_month: int  = Query(..., ge=1, le=12),
    target_year:  int  = Query(..., ge=2020, le=2100),
    regen_target: bool = Query(False, description="บังคับ regenerate dummy targets"),
):
    os.makedirs("data", exist_ok=True)

    # ── Step 1: ดึงพนักงาน ───────────────────────────────
    fabric = None
    df_emp_fabric = pd.DataFrame()
    try:
        fabric = FabricDAXConnector()
        df_emp_fabric = fabric.get_employees_by_manager(sup_id)
    except Exception as e:
        cp = emp_cache_path(sup_id, target_month, target_year)
        if os.path.exists(cp):
            logger.warning("Fabric error → emp cache: %s", e)
            df_emp_fabric = pd.read_csv(cp, dtype={"emp_id": str})
        else:
            raise HTTPException(503, detail=f"ไม่สามารถดึงพนักงานได้ และไม่มี cache: {e}")

    if df_emp_fabric.empty:
        raise HTTPException(404, detail=f"ไม่พบพนักงานใต้ SuperCode '{sup_id}'")

    emp_list = df_emp_fabric["emp_id"].tolist()
    df_emp_fabric.to_csv(emp_cache_path(sup_id, target_month, target_year), index=False)
    logger.info("Employees: %d คน %s", len(emp_list), emp_list)

    # ── Step 2: เป้าหมาย — ค่าเริ่มต้นจาก Fabric (tga_target_salesman) ─────
    # ตั้ง USE_LEGACY_TARGET_CSV=1 เพื่อใช้ target_boxes.csv / target_sun.csv แทน (ทดสอบ/ย้อนหลัง)
    use_legacy = os.environ.get("USE_LEGACY_TARGET_CSV", "").strip().lower() in ("1", "true", "yes")
    df_sku_csv, _df_sun_loaded = load_target_csv()
    emp_with_tga_set: set[str] | None = None

    if use_legacy and df_sku_csv is not None and not regen_target:
        logger.info("ใช้ target_boxes.csv / target_sun.csv (USE_LEGACY_TARGET_CSV)")
        df_sku = df_sku_csv
        sku_list = df_sku["sku"].tolist()
        df_sun_csv = _df_sun_loaded
        if df_sun_csv is None and os.path.exists("data/target_sun.csv"):
            df_sun_csv = pd.read_csv("data/target_sun.csv", dtype={"emp_id": str}).fillna(0)
            df_sun_csv["emp_id"] = df_sun_csv["emp_id"].astype(str).str.strip()
    else:
        try:
            sku_list = fabric.get_skus_sold_by_team(emp_list, target_month, target_year, n_months=6)
        except Exception as e:
            logger.warning("get_skus_sold_by_team error: %s → fallback SKUs", e)
            sku_list = list(PRICE_FALLBACK.keys())

        if not sku_list:
            sku_list = list(PRICE_FALLBACK.keys())

        df_tga = pd.DataFrame()
        try:
            df_tga = fabric.get_tga_target_salesman(emp_list, target_month, target_year)
        except Exception as e:
            logger.warning("get_tga_target_salesman error: %s — เป้าจะเป็น 0 ทั้งหมด", e)

        tga_skus: list[str] = []
        if df_tga is not None and not df_tga.empty:
            tga_skus = (
                df_tga["sku"].dropna().astype(str).str.strip().unique().tolist()
            )
        seen_sku: set[str] = set()
        sku_union: list[str] = []
        for s in sku_list + tga_skus:
            s = str(s).strip()
            if s and s not in seen_sku:
                seen_sku.add(s)
                sku_union.append(s)

        df_sku_base = pd.DataFrame()
        try:
            df_sku_base = fabric.get_product_info(sku_list=sku_union)
        except Exception as e:
            logger.warning("get_product_info error: %s", e)
            df_sku_base = pd.DataFrame({"sku": sku_union})

        if df_sku_base.empty:
            df_sku_base = pd.DataFrame({"sku": sku_union})

        df_sku, df_sun_csv, emp_with_tga = _build_sku_and_sun_from_tga(
            df_tga, df_sku_base, emp_list, sku_union
        )
        emp_with_tga_set = emp_with_tga

        os.makedirs("data", exist_ok=True)
        df_sku.to_csv("data/target_boxes.csv", index=False)
        df_sun_csv.to_csv("data/target_sun.csv", index=False)
        logger.info(
            "บันทึกเป้าจาก Fabric (TGA): %d SKU, พนักงาน %d คน, มีแถว TGA %d คน",
            len(df_sku), len(df_sun_csv), len(emp_with_tga_set),
        )

    if df_sun_csv is None and os.path.exists("data/target_sun.csv"):
        df_sun_csv = pd.read_csv("data/target_sun.csv", dtype={"emp_id": str}).fillna(0)
        df_sun_csv["emp_id"] = df_sun_csv["emp_id"].astype(str).str.strip()

    sku_list = df_sku["sku"].tolist()

    # ── Step 3: merge target_sun ──────────────────────────
    df_emp = df_emp_fabric.copy()
    if df_sun_csv is not None and not df_sun_csv.empty:
        df_emp = pd.merge(df_emp, df_sun_csv[["emp_id", "target_sun"]], on="emp_id", how="left")
    if "target_sun" not in df_emp.columns:
        df_emp["target_sun"] = 0.0
    df_emp["target_sun"] = df_emp["target_sun"].fillna(0.0)

    if emp_with_tga_set is not None:
        df_emp["has_tga_rows"] = df_emp["emp_id"].astype(str).str.strip().isin(emp_with_tga_set)
    else:
        df_emp["has_tga_rows"] = True

    # ── Step 4: LY sales ──────────────────────────────────
    # get_ly_sales แล้ว fill 0 ให้ครบทุก emp (handled ใน connector)
    try:
        df_ly = fabric.get_ly_sales(target_month, target_year, sku_list=sku_list, emp_list=emp_list)
        if not df_ly.empty and "emp_id" in df_ly.columns:
            # merge เฉพาะ emp_id + ly_sales (connector ทำ fill 0 ให้แล้ว)
            df_emp = pd.merge(df_emp, df_ly[["emp_id", "ly_sales"]], on="emp_id", how="left")
        else:
            df_emp["ly_sales"] = 0.0
    except Exception as e:
        logger.warning("LY sales error: %s", e)
        df_emp["ly_sales"] = 0.0
    df_emp["ly_sales"] = df_emp["ly_sales"].fillna(0.0)

    # ── Step 5: Historical 3M (+ cache 6M แยกไฟล์สำหรับกลยุทธ์ L6M) ─────
    df_hist = pd.DataFrame()
    try:
        df_hist = fabric.get_historical_sales(
            target_month, target_year, sku_list=sku_list, emp_list=emp_list, n_months=3
        )
        if not df_hist.empty:
            df_hist_emp = df_hist[df_hist["emp_id"].isin(emp_list)].copy()
            df_hist_emp.to_csv(hist_cache_path(sup_id, target_month, target_year, n_months=3), index=False)

            df_hist_val = pd.merge(df_hist_emp, df_sku[["sku", "price_per_box"]], on="sku", how="left")
            df_hist_val["hist_value"] = df_hist_val["hist_boxes"] * df_hist_val["price_per_box"]
            df_hist_agg = df_hist_val.groupby("emp_id")["hist_value"].sum().reset_index()
            df_hist_agg["hist_avg_3m"] = df_hist_agg["hist_value"] / 3.0
            df_emp = pd.merge(df_emp, df_hist_agg[["emp_id", "hist_avg_3m"]], on="emp_id", how="left")
        else:
            df_emp["hist_avg_3m"] = 0.0

        # ดึง 6 เดือนเพิ่ม (ไม่ fatal) — ใช้ตอน optimize ด้วยกลยุทธ์ L6M
        try:
            df_hist_6 = fabric.get_historical_sales(
                target_month, target_year, sku_list=sku_list, emp_list=emp_list, n_months=6
            )
            if not df_hist_6.empty:
                df_hist_6_emp = df_hist_6[df_hist_6["emp_id"].isin(emp_list)].copy()
                p6 = hist_cache_path(sup_id, target_month, target_year, n_months=6)
                df_hist_6_emp.to_csv(p6, index=False)
                logger.info("historical 6M cache saved: %d rows → %s", len(df_hist_6_emp), p6)
        except Exception as e6:
            logger.warning("historical 6M cache skipped: %s", e6)
    except Exception as e:
        logger.warning("historical 3M: %s", e)
        df_emp["hist_avg_3m"] = 0.0
    if "hist_avg_3m" not in df_emp.columns:
        df_emp["hist_avg_3m"] = 0.0
    df_emp["hist_avg_3m"] = df_emp["hist_avg_3m"].fillna(0.0)

    # ── Step 6: Warehouse ─────────────────────────────────
    try:
        df_wh = fabric.get_warehouse_by_emp(emp_list)
        if not df_wh.empty:
            df_emp = pd.merge(df_emp, df_wh[["emp_id", "warehouse_code"]], on="emp_id", how="left")
    except Exception as e:
        logger.warning("warehouse: %s", e)
    if "warehouse_code" not in df_emp.columns:
        df_emp["warehouse_code"] = ""
    df_emp["warehouse_code"] = df_emp["warehouse_code"].fillna("")

    # ── Cleanup dtypes ────────────────────────────────────
    numeric_cols = df_emp.select_dtypes(include=["number"]).columns
    df_emp[numeric_cols] = df_emp[numeric_cols].fillna(0)
    for col in ["emp_name", "manager_code", "warehouse_code"]:
        if col in df_emp.columns:
            df_emp[col] = df_emp[col].fillna("")

    logger.info("Response: %d emp, %d sku", len(df_emp), len(df_sku))

    # ── Fix 3: เช็ค emp_id ใน target_sun.csv ที่ไม่ตรงกับพนักงานจริง (โหมด legacy เท่านั้น) ──
    sku_warnings = []

    if not use_legacy and emp_with_tga_set is not None:
        for e in emp_list:
            se = str(e).strip()
            if se not in emp_with_tga_set:
                sku_warnings.append({
                    "type": "no_tga_employee",
                    "sku": "",
                    "brand": "",
                    "message": (
                        f"พนักงาน {se} ไม่มีแถวเป้าใน tga_target_salesman สำหรับงวดนี้ "
                        f"— target_sun = 0 (ตรวจสอบข้อมูลใน Fabric)"
                    ),
                })
        zero_skus = [
            str(r["sku"]).strip()
            for _, r in df_sku.iterrows()
            if int(r.get("supervisor_target_boxes", 0) or 0) == 0
        ]
        if zero_skus:
            preview = ", ".join(zero_skus[:20])
            more = f" และอีก {len(zero_skus) - 20} SKU" if len(zero_skus) > 20 else ""
            sku_warnings.append({
                "type": "no_tga_sku",
                "sku": "",
                "brand": "",
                "message": (
                    f"มี {len(zero_skus)} SKU ที่ทีมเคยขายแต่ไม่มีเป้าหีบใน TGA งวดนี้ "
                    f"(supervisor_target_boxes = 0): {preview}{more}"
                ),
            })

    if use_legacy and df_sun_csv is not None and not df_sun_csv.empty:
        sun_emp_ids  = set(df_sun_csv["emp_id"].astype(str).str.strip())
        fabric_emp_ids = set(str(e) for e in emp_list)
        unmatched = sun_emp_ids - fabric_emp_ids
        if unmatched:
            logger.warning("target_sun emp_id ไม่ตรงกับ Fabric: %s", unmatched)
            sku_warnings.append({
                "type": "emp_mismatch",
                "sku": "",
                "brand": "",
                "message": (
                    f"⚠️ target_sun.csv มีรหัสพนักงานที่ไม่มีในทีม {sup_id}: "
                    f"{', '.join(sorted(unmatched)[:5])} "
                    f"— เป้าเงินของพนักงานเหล่านี้จะถูกตั้งเป็น 0"
                )
            })

    # ── Fix 4: SKU Reconciliation — รันทุกครั้งที่มี target_boxes.csv ──
    # (ไม่รอให้ hist ไม่ว่าง เพราะถ้า Fabric ล่มก็ยังควรเตือนได้)
    # normalize SKU keys (กันเคสช่องว่าง/ชนิดข้อมูล ทำให้เทียบกันผิด)
    target_skus = set(str(s).strip() for s in df_sku["sku"].tolist() if str(s).strip())

    if not df_hist.empty:
        # ประวัติจาก Fabric — เช็คทีมทั้งหมด (ไม่ใช่รายคน)
        hist_skus = set(str(s).strip() for s in df_hist["sku"].dropna().astype(str).unique() if str(s).strip())

        # เป้าหีบรวมทีมต่อ SKU (จาก TGA) — เตือนเฉพาะ SKU ที่เป้าทีม > 0 จริง (กันคลายกับ SKU ที่อยู่ในรายการเพราะเคยขาย 6 เดือนแต่เป้า 0)
        team_boxes_by_sku: dict[str, int] = {}
        for _, row in df_sku.iterrows():
            sk = str(row.get("sku", "")).strip()
            if sk:
                team_boxes_by_sku[sk] = int(row.get("supervisor_target_boxes", 0) or 0)

        # SKU มีเป้าหีบรวมทีม แต่ไม่มียอดในประวัติ 3 เดือน (ทั้งทีม)
        new_skus = sorted(
            s for s in (target_skus - hist_skus) if team_boxes_by_sku.get(s, 0) > 0
        )
        for sku in new_skus:
            info = df_sku[df_sku["sku"].astype(str).str.strip() == sku]
            brand = ""
            if not info.empty:
                brand = str(info.iloc[0].get("brand_name_thai") or info.iloc[0].get("brand_name_english") or "")
            sku_warnings.append({
                "type": "no_history",
                "sku": sku,
                "brand": brand,
                "message": (
                    f"SKU {sku}{' (' + brand + ')' if brand else ''} "
                    f"มีเป้าหีบรวมทีมในงวดนี้ แต่ไม่มียอดขายย้อนหลัง 3 เดือนในทีมนี้ "
                    f"(ช่องรายคนอาจว่าง/น้อยจนกว่าจะกระจาย) — กลยุทธ์ L3M/L6M/PUSH จะใช้ EVEN แทนประวัติ"
                ),
            })

        # SKU เคยขายในทีมแต่ไม่มีในเป้าเดือนนี้
        missing_skus = sorted(hist_skus - target_skus)
        for sku in missing_skus:
            hist_rows = df_hist[df_hist["sku"] == sku]
            total_hist = float(hist_rows["hist_boxes"].sum()) if "hist_boxes" in hist_rows.columns else 0
            sku_warnings.append({
                "type": "no_target",
                "sku": sku,
                "brand": "",
                "message": f"SKU {sku} เคยขายได้ {total_hist:.0f} หีบ (3M) แต่ไม่ถูกรวมในเป้าเดือนนี้"
            })
    else:
        # Fabric ดึงประวัติไม่ได้ — เตือนเฉพาะกรณีไม่มี hist cache เลย
        cache_file = hist_cache_path(sup_id, target_month, target_year)
        if not os.path.exists(cache_file) and target_skus:
            sku_warnings.append({
                "type": "no_history",
                "sku": "",
                "brand": "",
                "message": "⚠️ ไม่สามารถดึงประวัติขายจาก Fabric ได้ — การกระจายหีบจะใช้ EVEN แทนประวัติ"
            })

    if sku_warnings:
        logger.info("reconciliation warnings: %d รายการ", len(sku_warnings))

    def _clean(df: pd.DataFrame) -> list:
        """แปลง NaN → None ก่อน serialize เพื่อกัน JSON invalid"""
        return df.where(pd.notna(df), None).to_dict(orient="records")

    return {
        "employees":    _clean(df_emp),
        "skus":         _clean(df_sku),
        "sku_warnings": sku_warnings,
    }


# ══════════════════════════════════════════════════════════
#  POST /optimize
# ══════════════════════════════════════════════════════════
@app.post("/optimize")
def run_optimization(
    req:          OptimizeRequest,
    _user: dict = Depends(require_entra_member),
    sup_id:       str = Query("SL330"),
    target_month: int = Query(..., ge=1, le=12),
    target_year:  int = Query(..., ge=2020, le=2100),
):
    if req.strategy.upper() not in VALID_STRATEGIES:
        raise HTTPException(400, detail=f"strategy ไม่ถูกต้อง ต้องเป็น {VALID_STRATEGIES}")

    os.makedirs("data", exist_ok=True)

    df_sku, _ = load_target_csv()
    if df_sku is None:
        raise HTTPException(500, detail="ไม่พบ target_boxes.csv กรุณาโหลดหน้า Dashboard ก่อน")

    df_emp_targets = pd.DataFrame([t.model_dump() for t in req.yellowTargets])
    emp_list = df_emp_targets["emp_id"].tolist()

    strategy_u = req.strategy.upper()
    # L6M ใช้ประวัติ 6 เดือน (ไฟล์ _6m.csv) — ถ้าไม่มีให้ fallback 3 เดือน
    want_6m = strategy_u == "L6M"
    cache_6 = hist_cache_path(sup_id, target_month, target_year, n_months=6)
    cache_3 = hist_cache_path(sup_id, target_month, target_year, n_months=3)
    cache_file = cache_6 if want_6m and os.path.exists(cache_6) else cache_3
    if want_6m and not os.path.exists(cache_6) and os.path.exists(cache_3):
        logger.warning("ไม่พบ hist 6M cache — ใช้ cache 3M แทนสำหรับ L6M (โหลดหน้า Dashboard ใหม่เพื่อสร้าง 6M cache)")
    if os.path.exists(cache_file):
        df_hist = pd.read_csv(cache_file, dtype={"sku": str, "emp_id": str})
        logger.info("hist cache loaded (%s): %d rows", os.path.basename(cache_file), len(df_hist))
    else:
        logger.warning("ไม่พบ hist cache → ใช้ตารางเปล่า")
        df_hist = pd.DataFrame(columns=["emp_id", "sku", "hist_boxes"])

    df_hist = df_hist[df_hist["emp_id"].isin(emp_list)]

    hist_months = 6 if (want_6m and os.path.exists(cache_6)) else 3

    # รัน allocation ตาม strategy ที่เลือก
    logger.info("Running strategy=%s for sup=%s", req.strategy, sup_id)
    locked_edits_data = [{"emp_id": le.emp_id, "sku": le.sku, "locked_boxes": le.locked_boxes} for le in req.locked_edits]
    df_allocation = allocate_boxes(
        df_emp_targets, df_sku, df_hist,
        strategy=req.strategy,
        force_min_one=req.force_min_one,
        locked_edits=locked_edits_data if locked_edits_data else None,
        cap_multiplier=req.cap_multiplier,
    )

    # คำนวณ hist_avg รายพนักงาน×SKU (เฉลี่ยต่อเดือนจากช่วงที่โหลด: 3 หรือ 6 เดือน)
    if not df_hist.empty:
        df_hist_avg = df_hist.groupby(["emp_id", "sku"])["hist_boxes"].sum().reset_index()
        df_hist_avg["hist_avg"] = (df_hist_avg["hist_boxes"] / float(hist_months)).round(1)
    else:
        df_hist_avg = pd.DataFrame(columns=["emp_id", "sku", "hist_avg"])

    df_final = pd.merge(
        df_allocation,
        df_hist_avg[["emp_id", "sku", "hist_avg"]],
        on=["emp_id", "sku"], how="left"
    )
    df_final["hist_avg"] = df_final["hist_avg"].fillna(0)

    # merge brand + price_per_box
    brand_cols = [c for c in ["brand_name_thai", "brand_name_english", "product_name_thai", "price_per_box"]
                  if c in df_sku.columns]
    if brand_cols:
        df_final = pd.merge(df_final, df_sku[["sku"] + brand_cols], on="sku", how="left")
        for c in brand_cols:
            df_final[c] = df_final[c].fillna("" if "name" in c else 0)

    # บันทึก per-supervisor (ป้องกัน race condition)
    df_final.to_csv(result_path(sup_id), index=False)

    # สร้าง Excel ทั้งหมด (ALL brand) ทันที
    yellow_map = {y.emp_id: y.yellow_target for y in req.yellowTargets}
    sku_checks = _validate_allocation_vs_targets(df_final, df_sku)
    if sku_checks:
        logger.warning("allocation vs target mismatch: %s", sku_checks)

    create_target_excel(
        result_csv=result_path(sup_id),
        output_path=excel_path(sup_id),
        brand_filter="ALL",
        yellow_map=yellow_map,
        sup_id=sup_id,
        target_boxes_csv="data/target_boxes.csv",
    )

    return {
        "allocations": df_final.to_dict(orient="records"),
        "sku_total_checks": sku_checks,
        "hist_window_months": hist_months,
    }


# ══════════════════════════════════════════════════════════
#  POST /export/excel
# ══════════════════════════════════════════════════════════
@app.post("/export/excel")
def export_excel(
    req:    ExportRequest,
    _user: dict = Depends(require_entra_member),
    sup_id: str = Query("SL330"),
):
    os.makedirs("data", exist_ok=True)

    # Build DataFrame จาก validated Pydantic models
    df_final = pd.DataFrame([a.model_dump() for a in req.allocations])

    # เติม price_per_box ถ้าไม่ครบ
    df_sku_tmp, _ = load_target_csv()
    if df_sku_tmp is not None:
        missing_cols = [c for c in ["price_per_box", "brand_name_thai", "brand_name_english", "product_name_thai"]
                        if c not in df_final.columns or (df_final[c] == 0).all()]
        if missing_cols:
            merge_cols = ["sku"] + [c for c in missing_cols if c in df_sku_tmp.columns]
            df_final = pd.merge(df_final, df_sku_tmp[merge_cols], on="sku", how="left", suffixes=("", "_csv"))
            for c in missing_cols:
                if f"{c}_csv" in df_final.columns:
                    df_final[c] = df_final[c].where(df_final[c] != 0, df_final[f"{c}_csv"])
                    df_final.drop(columns=[f"{c}_csv"], inplace=True)

    # filter by brand
    brand_filter = req.brand_filter
    df_export = df_final.copy()
    if brand_filter != "ALL":
        df_export = df_final[df_final["brand_name_thai"] == brand_filter].copy()
        if df_export.empty:
            raise HTTPException(404, detail=f"ไม่พบข้อมูลสำหรับแบรนด์ '{brand_filter}'")

    ep = export_result_path(sup_id, brand_filter)
    df_export.to_csv(ep, index=False)

    yellow_map = {y.emp_id: y.yellow_target for y in req.yellow_targets}

    create_target_excel(
        result_csv=ep,
        output_path=excel_path(sup_id),
        brand_filter=brand_filter,
        yellow_map=yellow_map,
        sup_id=sup_id,
        target_boxes_csv="data/target_boxes.csv",
    )

    logger.info("Export excel: sup=%s brand=%s rows=%d", sup_id, brand_filter, len(df_export))
    return {"status": "ok", "brand_filter": brand_filter, "rows": len(df_export)}


# ══════════════════════════════════════════════════════════
#  GET /download/excel
# ══════════════════════════════════════════════════════════
@app.get("/download/excel")
def download_excel(
    _user: dict = Depends(require_entra_member),
    sup_id: str = Query("SL330"),
):
    fpath = excel_path(sup_id)
    if not os.path.exists(fpath):
        raise HTTPException(404, detail="ไม่พบไฟล์ Excel กรุณา Optimize ก่อน")

    return FileResponse(
        fpath,
        filename=f"Target_{_safe_id(sup_id)}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ══════════════════════════════════════════════════════════
#  GET /health
# ══════════════════════════════════════════════════════════
@app.get("/health")
def health():
    return {
        "status": "ok",
        "entra_auth_required": auth_entra.auth_enabled(),
        "managers_source": "GET /managers (ดึง SuperCode จาก dim_salesman ใน Fabric)",
        "valid_strategies": list(VALID_STRATEGIES),
        "debug_endpoints_enabled": _ENABLE_DEBUG,
        "files": {
            "target_boxes.csv": os.path.exists("data/target_boxes.csv"),
            "target_sun.csv":   os.path.exists("data/target_sun.csv"),
        },
    }


# ══════════════════════════════════════════════════════════
#  GET /debug/fabric  — ดู SuperCode ทั้งหมดที่มีใน dim_salesman
#  ใช้สำหรับ debug เมื่อหาพนักงานไม่เจอ
# ══════════════════════════════════════════════════════════
@app.get("/debug/fabric")
def debug_fabric(
    _user: dict = Depends(require_entra_member),
    sup_id: str = Query("SL330"),
):
    """
    ดึงข้อมูล debug จาก Fabric:
    - SuperCode ทั้งหมดที่มีใน dim_salesman
    - พนักงานที่ SuperCode นี้ดูแล (ถ้าเจอ)
    เปิด: http://localhost:8000/debug/fabric?sup_id=SL330
    Production: ตั้ง ENABLE_DEBUG_ENDPOINTS=1 เท่านั้น
    """
    if not _ENABLE_DEBUG:
        raise HTTPException(404, detail="ไม่พบ endpoint")
    try:
        fabric = FabricDAXConnector()

        # ดึง SuperCode sample ทั้งหมด
        dax_all = """
EVALUATE
SUMMARIZECOLUMNS(
    'dim_salesman'[SuperCode],
    "cnt", COUNTROWS('dim_salesman')
)
ORDER BY 'dim_salesman'[SuperCode]
"""
        rows_all = fabric._execute_dax(dax_all, debug=True)
        all_super_codes = []
        for r in rows_all:
            sc = fabric._get(r, "[SuperCode]", "dim_salesman[SuperCode]", default="")
            cnt = fabric._get(r, "[cnt]", default=0)
            all_super_codes.append({"super_code": repr(str(sc)), "count": cnt})

        # ลอง query พนักงาน
        df_emp = fabric.get_employees_by_manager(sup_id)

        return {
            "query_super_code": sup_id,
            "query_super_code_repr": repr(sup_id),
            "employees_found": len(df_emp),
            "employees": df_emp.to_dict(orient="records") if not df_emp.empty else [],
            "all_super_codes_in_db": all_super_codes,
        }
    except Exception as e:
        logger.error("debug_fabric error: %s", e)
        raise HTTPException(500, detail=str(e))


# เสิร์ฟ frontend จาก http://127.0.0.1:8000/ (ลงท้าย — ให้ API routes ถูกจับก่อน)
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")

# ══════════════════════════════════════════════════════════
#  สำหรับรันเป็นไฟล์ .exe โดยไม่ต้องใช้คำสั่งผ่าน Terminal
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    # เปิดเซิร์ฟเวอร์ที่ port 8000 (เอา --reload ออกเพราะเป็น production)
    uvicorn.run(app, host="127.0.0.1", port=8000)