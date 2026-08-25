import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pandas as pd
from fastapi import HTTPException

from ..core.allocation_checks import detect_new_product_skus
from ..core.atomic_io import atomic_write_csv
from ..core.constants import PRICE_FALLBACK
from . import demo_data, no_target_store
from ..core.paths import (
    emp_cache_path,
    hist_cache_path,
    hist_calendar_year_cache_path,
    hist_ly_same_month_cache_path,
    hist_prev_month_cache_path,
    target_boxes_cache_path,
    target_sun_cache_path,
    tga_grain_cache_path,
)
from ..core.employee_filter import (
    drop_van_employees,
    filter_employees_for_display,
    is_allocation_eligible,
)
from ..core.targets import load_target_csv, load_target_csv_for, target_csv_ready
from ..core.tga_period import (
    enforce_tga_has_targets_for_period,
    enforce_tga_selection_matches_effective_window,
)

_SKU_OUTPUT_COLUMNS = [
    "sku",
    "price_per_box",
    "price_missing",
    "price_from_sales_history",
    "supervisor_target_boxes",
    "brand_name_thai",
    "brand_name_english",
    "section",
    "product_name_thai",
    "product_name_english",
]
from ..fabric_dax_connector import FabricDAXConnector
from .sku_link_store import (
    collapse_hist_to_canonical,
    expand_skus_for_dax,
    extra_aliases_for_canonical,
    read_links,
)
from .target_baseline import capture_baseline_once, diff_against_baseline
from .usage_log_store import append_log
from .wh_split import expand_employee_rows, warehouses_per_emp_from_tga
from .employee_payload_cache import (
    read_cached_employee_payload,
    write_cached_employee_payload,
)
from . import targetsun_read

logger = logging.getLogger("target_allocation")


def _build_sku_and_sun_from_tga(
    df_tga: pd.DataFrame,
    df_product: pd.DataFrame,
    emp_list: list,
    sku_list: list,
    price_latest_by_sku: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, set[str]]:
    """
    จาก TGA (จำนวนหีบ = QUANTITYCASE ต่อคู่ emp×sku):
    - supervisor_target_boxes ต่อ SKU = SUM หีบของทีมต่อ SKU
    - target_sun ต่อคน = SUM(หีบ × ราคา/หีบ) รายพนักงาน
      ราคา: หลัก cfm_product_characteristic[CREDITUNITPRICE] (PRODUCTSIZE=0, PRODUCTCODE);
      ไม่มี → Amount÷Qty ประวัติ (ไฮไลต์ฟ้า); ไม่มีเลย → 0 + เหลือง
    """
    sku_list = [str(s).strip() for s in sku_list if str(s).strip()]
    team_set = set(str(e).strip() for e in emp_list)

    if not sku_list:
        df_sku = pd.DataFrame(columns=_SKU_OUTPUT_COLUMNS)
        df_sun = pd.DataFrame(
            [{"emp_id": str(e).strip(), "target_sun": 0.0} for e in emp_list]
        )
        return df_sku, df_sun, set()

    df_p = (
        df_product.copy()
        if df_product is not None and not df_product.empty
        else pd.DataFrame()
    )
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
        price_missing = True
        price_from_sales_history = False
        brand_th = brand_en =         pname_th = pname_en = ""
        section = ""
        credit_unit_price = 0.0
        if not row_p.empty:
            r0 = row_p.iloc[0]
            brand_th = str(r0.get("brand_name_thai", "") or "")
            brand_en = str(r0.get("brand_name_english", "") or "")
            pname_th = str(r0.get("product_name_thai", "") or "")
            pname_en = str(r0.get("product_name_english", "") or "")
            section = str(r0.get("section", "") or "").strip()
            credit_unit_price = float(r0.get("credit_unit_price", 0) or 0)
        sk = str(sku).strip()
        sales_price: float | None = None
        if price_latest_by_sku is not None and sk in price_latest_by_sku:
            sales_price = float(price_latest_by_sku.get(sk) or 0.0)
        # หลัก: CREDITUNITPRICE (PRODUCTSIZE=0); สำรอง: Amount÷Qty ประวัติ (ฟ้า); ไม่มีเลย: เหลือง
        if credit_unit_price > 0:
            price = credit_unit_price
            price_missing = False
            price_from_sales_history = False
        elif sales_price is not None and sales_price > 0:
            price = sales_price
            price_missing = False
            price_from_sales_history = True
        else:
            price = 0.0
            price_missing = True
            price_from_sales_history = False
        sup_boxes = int(round(float(sum_dict.get(sku, 0))))
        rows_sku.append(
            {
                "sku": sku,
                "price_per_box": price,
                "price_missing": bool(price_missing),
                "price_from_sales_history": bool(price_from_sales_history),
                "supervisor_target_boxes": max(0, sup_boxes),
                "brand_name_thai": brand_th,
                "brand_name_english": brand_en,
                "section": section,
                "product_name_thai": pname_th,
                "product_name_english": pname_en,
            }
        )

    df_sku = pd.DataFrame(rows_sku)
    price_by_sku = dict(zip(df_sku["sku"].astype(str), df_sku["price_per_box"]))

    sun_map: dict[str, float] = {str(e).strip(): 0.0 for e in emp_list}
    if df_tga is not None and not df_tga.empty:
        d = df_tga.copy()
        d["emp_id"] = d["emp_id"].astype(str).str.strip()
        d["sku"] = d["sku"].astype(str).str.strip()
        d["price"] = d["sku"].map(
            lambda s: float(price_by_sku.get(str(s).strip(), 0.0))
        )
        d["line_value"] = d["qty"] * d["price"]
        g = d.groupby("emp_id", as_index=True)["line_value"].sum()
        for emp in sun_map:
            if emp in g.index:
                sun_map[emp] = round(float(g[emp]), 2)

    df_sun = pd.DataFrame([{"emp_id": k, "target_sun": v} for k, v in sun_map.items()])
    return df_sku, df_sun, emp_with_tga


def _build_allocations_preview_from_grain(
    df_granular: pd.DataFrame | None,
    df_sku: pd.DataFrame,
    emp_list: list[str],
) -> list[dict[str, Any]]:
    """แถว emp×sku (และคลังถ้ามี) สำหรับแสดงตารางก่อนกระจายหีบ"""
    team_set = {str(e).strip() for e in emp_list if str(e).strip()}
    if not team_set or df_granular is None or df_granular.empty:
        return []

    dg = df_granular.copy()
    dg["emp_id"] = dg["emp_id"].astype(str).str.strip()
    dg["sku"] = dg["sku"].astype(str).str.strip()
    dg = dg[dg["emp_id"].isin(team_set)]
    dg["qty"] = pd.to_numeric(dg["qty"], errors="coerce").fillna(0)
    dg = dg[dg["qty"] != 0]
    if dg.empty:
        return []

    if "warehouse_code" not in dg.columns:
        dg["warehouse_code"] = ""
    else:
        dg["warehouse_code"] = dg["warehouse_code"].fillna("").astype(str).str.strip()

    grouped = (
        dg.groupby(["emp_id", "sku", "warehouse_code"], as_index=False)["qty"]
        .sum()
    )

    sku_meta: dict[str, dict[str, Any]] = {}
    if df_sku is not None and not df_sku.empty:
        for row in df_sku.to_dict(orient="records"):
            sku_meta[str(row.get("sku") or "").strip()] = row

    out: list[dict[str, Any]] = []
    for row in grouped.to_dict(orient="records"):
        sku = str(row.get("sku") or "").strip()
        emp_id = str(row.get("emp_id") or "").strip()
        if not sku or not emp_id:
            continue
        boxes = int(round(float(row.get("qty") or 0)))
        if boxes <= 0:
            continue
        meta = sku_meta.get(sku, {})
        out.append(
            {
                "emp_id": emp_id,
                "sku": sku,
                "warehouse_code": str(row.get("warehouse_code") or "").strip(),
                "allocated_boxes": boxes,
                "price_per_box": float(meta.get("price_per_box") or 0),
                "brand_name_thai": str(meta.get("brand_name_thai") or ""),
                "brand_name_english": str(meta.get("brand_name_english") or ""),
                "product_name_thai": str(meta.get("product_name_thai") or ""),
                "baseline_boxes": boxes,
            }
        )
    return out


def _preview_from_grain_cache(
    sup_id: str,
    target_month: int,
    target_year: int,
    skus: list[dict[str, Any]],
    emp_list: list[str],
) -> list[dict[str, Any]]:
    p_grain = tga_grain_cache_path(sup_id, target_month, target_year)
    if not os.path.isfile(p_grain):
        return []
    try:
        dg = pd.read_csv(p_grain, dtype={"emp_id": str, "sku": str})
    except Exception as e:
        logger.warning("read grain cache for preview %s: %s", p_grain, e)
        return []
    df_sku = pd.DataFrame(skus) if skus else pd.DataFrame()
    return _build_allocations_preview_from_grain(dg, df_sku, emp_list)


def _clean(df: pd.DataFrame) -> list:
    """แปลง NaN → None ก่อน serialize เพื่อกัน JSON invalid"""
    return df.where(pd.notna(df), None).to_dict(orient="records")


_HIST_COLS = ["emp_id", "sku", "hist_boxes", "hist_amount"]


def _read_hist_cache_file(path: str) -> pd.DataFrame:
    """อ่านไฟล์ประวัติที่เคยดึงสำเร็จไว้ — ไฟล์หาย/เสีย ให้คืนตารางว่างแทนการโยน"""
    if not os.path.exists(path):
        return pd.DataFrame(columns=_HIST_COLS)
    try:
        df = pd.read_csv(path, dtype={"emp_id": str, "sku": str})
    except Exception as e:
        logger.warning("อ่านไฟล์ประวัติ %s ไม่ได้: %s", path, e)
        return pd.DataFrame(columns=_HIST_COLS)
    return df if not df.empty else pd.DataFrame(columns=_HIST_COLS)


def _load_history(
    label: str,
    path: str,
    fetch,
    sku_links,
    sup_id: str,
) -> pd.DataFrame:
    """
    ดึงประวัติขายจาก Fabric — ดึงไม่ได้เมื่อไหร่ ให้ใช้ไฟล์ที่เคยเก็บไว้แทน

    ยอดขายของเดือนที่ปิดไปแล้วเป็นค่าคงที่ ไฟล์ที่เคยดึงสำเร็จจึงยังใช้ได้เสมอ
    ของเดิมพอ Fabric ล่มหรือคิวรีล้ม จะได้ตารางว่างแล้วเดินต่อเงียบ ๆ ทำให้
    "ยอดขายเฉลี่ย 3 เดือน / ปีที่แล้ว" กลายเป็น 0 ทั้งทีมทั้งที่ไฟล์เดิมยังอยู่
    ครบในเครื่อง · เป้าไม่ได้รับผลกระทบเพราะมาจาก Target Sun คนละเส้นทาง
    อาการเลยออกมาเหมือน "ทีมนี้ไม่เคยขายอะไรเลย" ซึ่งไม่มีใครเดาถูก

    เขียนทับไฟล์เฉพาะตอนดึงได้จริงเท่านั้น — ของเก่าจะไม่ถูกลบทิ้งเพราะรอบนี้ล่ม
    """
    try:
        df = fetch()
    except Exception as e:
        cached = _read_hist_cache_file(path)
        logger.warning(
            "ประวัติ%s ของ %s ดึงจาก Fabric ไม่ได้ (%s) — %s",
            label, sup_id, e,
            f"ใช้ไฟล์ที่เก็บไว้เดิม {len(cached)} แถว" if not cached.empty else "ไม่มีไฟล์เดิมให้ใช้",
        )
        return cached

    if df is not None and not df.empty:
        df = collapse_hist_to_canonical(df, sku_links)
        try:
            df.to_csv(path, index=False)
            logger.info("ประวัติ%s: เก็บไว้ %d แถว → %s", label, len(df), path)
        except OSError as e:
            logger.warning("เขียนไฟล์ประวัติ%s ไม่สำเร็จ: %s", label, e)
        return df

    cached = _read_hist_cache_file(path)
    if not cached.empty:
        logger.warning(
            "ประวัติ%s ของ %s ดึงมาได้ 0 แถว — ใช้ไฟล์ที่เก็บไว้เดิม %d แถวแทน",
            label, sup_id, len(cached),
        )
        return cached
    logger.info("ประวัติ%s ของ %s: ไม่มีข้อมูลทั้งจาก Fabric และไฟล์เดิม", label, sup_id)
    return pd.DataFrame(columns=_HIST_COLS)


def _enrich_employee_allocation_flags(
    emp_records: list[dict[str, Any]],
    sup_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    คำนวณ allocation_eligible / include_in_allocation ทุกครั้ง (รวม cache เก่า)

    จุดเดียวที่ทุกเส้นทางผ่าน (cache hit / สร้างใหม่ / โหมดรวมภาค) รายชื่อ
    "ไม่ต้องตั้งเป้า" จึงมาเกาะที่นี่ ไม่ต้องไล่แก้ทีละเส้นแล้วลืมเส้นใดเส้นหนึ่ง

    ทีมของแต่ละแถวเอาจาก `supervisor_code` ของแถวเองก่อน (โหมดรวมภาคมีหลายทีม
    ในลิสต์เดียว) แล้วค่อยตกมาที่ sup_id ที่กำลังโหลด
    """
    blocked = no_target_store.no_target_map_safe()
    default_sup = no_target_store.norm_sup(sup_id)
    for rec in emp_records:
        sup = no_target_store.norm_sup(rec.get("supervisor_code")) or default_sup
        emp = no_target_store.norm_emp(rec.get("emp_id"))
        no_target = bool(emp and emp in blocked.get(sup, set()))
        eligible = is_allocation_eligible(
            bool(rec.get("has_tga_rows")),
            float(rec.get("target_sun") or 0),
        ) and not no_target
        rec["no_target"] = no_target
        rec["allocation_eligible"] = eligible
        rec["include_in_allocation"] = eligible
        rec["view_only"] = not eligible
    return emp_records


def load_employees_payload(
    sup_id: str,
    target_month: int,
    target_year: int,
    regen_target: bool = False,
    refresh: bool = False,
) -> dict:
    """
    Logic ของ GET /data/employees (ย้ายออกจาก router เพื่อให้อ่านง่าย)
    ต้องคง behavior เดิม: เขียน cache ที่ data/, สร้าง target_boxes/target_sun, สร้าง history caches

    refresh=True หรือ regen_target=True → ข้าม JSON cache แล้วยิง DAX ใหม่
    """
    # ทีมสาธิต — ข้อมูลสมมติล้วน ไม่แตะ Fabric/Target Sun และไม่แคช
    # ต้องอยู่บนสุดก่อนทุกอย่าง เพราะไม่มีทั้ง cache และแหล่งข้อมูลจริงให้ดึง
    if demo_data.is_demo_supervisor(sup_id):
        # เขียน cache ชุดเดียวกับ Step 1 จริง — ขั้นกระจายหีบและดาวน์โหลด Excel
        # อ่านเป้า/ประวัติจากไฟล์เหล่านี้ ไม่ได้อ่านจาก payload ที่ส่งกลับไป
        demo_data.write_demo_caches(sup_id, target_month, target_year)
        # เก็บเป้าตั้งต้นให้ทีมสาธิตด้วย — ทีมสาธิตเขียนไฟล์เป้าจริงเหมือนทีมปกติ
        # และเป็นทางเดียวที่สาธิต/ทดสอบเรื่องกู้คืนเป้าได้โดยไม่แตะข้อมูลจริง
        try:
            _dsku, _dsun = load_target_csv_for(sup_id, target_month, target_year)
            capture_baseline_once(sup_id, target_month, target_year, _dsku, _dsun)
        except Exception as e:
            logger.warning("เก็บเป้าตั้งต้นของทีมสาธิตไม่สำเร็จ: %s", e)
        return demo_data.build_employees_payload(sup_id, target_month, target_year)
    # ต้องเช็ค target_csv_ready ด้วย: ถ้า cache hit จะ return ก่อนถึงจุดที่เขียนไฟล์เป้าราย sup
    # ทีมที่มี cache ค้างจะไม่มีวันสร้างไฟล์ แล้วตกไปใช้ไฟล์ global ของทีมอื่นตลอดไป
    # ยกเว้นโหมด legacy (dev) ที่ไม่เคยเขียนไฟล์ราย sup อยู่แล้ว — ไม่งั้น cache จะใช้ไม่ได้เลย
    _legacy_mode = os.environ.get("USE_LEGACY_TARGET_CSV", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    _targets_ok = _legacy_mode or target_csv_ready(sup_id, target_month, target_year)
    if not regen_target and not refresh and _targets_ok:
        cached = read_cached_employee_payload(sup_id, target_month, target_year)
        if cached is not None:
            current_src = targetsun_read.get_target_read_source()
            cached_src = str(cached.get("target_read_source") or "fabric").strip().lower()
            if cached_src != current_src:
                logger.info(
                    "payload cache skip %s — source %s != %s",
                    sup_id,
                    cached_src,
                    current_src,
                )
            else:
                emps = cached.get("employees")
                if isinstance(emps, list):
                    cached["employees"] = _enrich_employee_allocation_flags(
                        [dict(e) for e in emps], sup_id
                    )
                return cached

    os.makedirs("data", exist_ok=True)

    # ── Step 1: ดึงพนักงาน ───────────────────────────────
    fabric = None
    df_emp_fabric = pd.DataFrame()
    sup_name = ""
    try:
        fabric = FabricDAXConnector()
        df_emp_fabric = fabric.get_employees_by_manager(sup_id)
        try:
            sup_name = fabric.get_supervisor_name(sup_id)
        except Exception:
            sup_name = ""
    except Exception as e:
        cp = emp_cache_path(sup_id, target_month, target_year)
        if os.path.exists(cp):
            logger.warning("Fabric error → emp cache: %s", e)
            df_emp_fabric = pd.read_csv(cp, dtype={"emp_id": str})
        else:
            raise HTTPException(503, detail=f"ไม่สามารถดึงพนักงานได้ และไม่มี cache: {e}")

    if df_emp_fabric.empty:
        raise HTTPException(404, detail=f"ไม่พบพนักงานใต้ SuperCode '{sup_id}'")

    df_emp_fabric, van_excluded = drop_van_employees(df_emp_fabric)
    if van_excluded:
        logger.info(
            "ตัดรหัส V (Van — ไม่แสดง/ไม่คำนวณ): %d คน ใต้ %s",
            van_excluded,
            sup_id,
        )
    if df_emp_fabric.empty:
        raise HTTPException(
            404,
            detail=f"ไม่พบพนักงานใต้ SuperCode '{sup_id}' (หลังตัดรหัส V)",
        )

    emp_list = df_emp_fabric["emp_id"].tolist()
    df_emp_fabric.to_csv(emp_cache_path(sup_id, target_month, target_year), index=False)
    logger.info("Employees: %d คน %s", len(emp_list), emp_list)

    # ── Step 2: เป้าหมาย — ค่าเริ่มต้นจาก Fabric (tga_target_salesman_next) ─────
    use_legacy = os.environ.get("USE_LEGACY_TARGET_CSV", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    df_sku_csv, _df_sun_loaded = load_target_csv()
    emp_with_tga_set: set[str] | None = None
    df_tga_granular: pd.DataFrame | None = None
    df_tga: pd.DataFrame | None = None
    sold_only_excluded = 0

    if use_legacy and df_sku_csv is not None and not regen_target:
        logger.info("ใช้ target_boxes.csv / target_sun.csv (USE_LEGACY_TARGET_CSV)")
        try:
            _gc = [
                "emp_id",
                "sku",
                "qty",
                "salestype",
                "divisioncode",
                "areacode",
                "provincecode",
                "warehouse_code",
            ]
            pd.DataFrame(columns=_gc).to_csv(
                tga_grain_cache_path(sup_id, target_month, target_year),
                index=False,
            )
        except Exception:
            pass
        df_sku = df_sku_csv
        sku_list = df_sku["sku"].tolist()
        df_sun_csv = _df_sun_loaded
        if df_sun_csv is None and os.path.exists("data/target_sun.csv"):
            df_sun_csv = pd.read_csv("data/target_sun.csv", dtype={"emp_id": str}).fillna(0)
            df_sun_csv["emp_id"] = df_sun_csv["emp_id"].astype(str).str.strip()
    else:
        if fabric is None:
            try:
                fabric = FabricDAXConnector()
            except Exception as e:
                raise HTTPException(
                    503,
                    detail=f"ไม่สามารถเชื่อมต่อ Fabric สำหรับดึงเป้าและประวัติ: {e}",
                )
        ts_div = ts_st = None
        ts_max_effective: dict | None = None
        if targetsun_read.is_enabled():
            ts_div, ts_st = targetsun_read.resolve_targetsun_scope(sup_id, fabric=fabric)
            try:
                ts_max_effective = targetsun_read.fetch_max_effective_date(ts_div, ts_st)
            except HTTPException:
                if not targetsun_read.fallback_to_fabric():
                    raise
                logger.warning(
                    "TargetSun maxEffectiveDate failed — skip period pre-check"
                )
                ts_div = ts_st = None
            except Exception as e:
                logger.warning("TargetSun maxEffectiveDate error: %s", e)
                ts_div = ts_st = None

        enforce_tga_selection_matches_effective_window(
            fabric,
            target_month,
            target_year,
            division_code=ts_div if targetsun_read.is_enabled() else None,
            sales_type=ts_st if targetsun_read.is_enabled() else None,
        )
        sold_skus: list[str] = []
        try:
            sold_skus = fabric.get_skus_sold_by_team(
                emp_list, target_month, target_year, n_months=6
            )
        except Exception as e:
            logger.warning("get_skus_sold_by_team error: %s", e)
            sold_skus = []

        df_tga = pd.DataFrame()
        df_tga_granular = pd.DataFrame()
        if targetsun_read.is_enabled():
            df_ts = targetsun_read.try_granular_df_for_team(
                emp_list,
                target_month,
                target_year,
                sup_id=sup_id,
                fabric=fabric,
            )
            if df_ts is not None:
                df_tga_granular = df_ts
                logger.info(
                    "เป้า TGA จาก Target Sun: sup=%s period=%02d/%d rows=%d",
                    sup_id,
                    target_month,
                    target_year,
                    len(df_tga_granular),
                )
            else:
                try:
                    df_tga_granular = fabric.get_tga_target_salesman_granular(
                        emp_list, target_month, target_year
                    )
                except Exception as e:
                    logger.warning(
                        "Fabric TGA fallback error: %s — เป้าจะเป็น 0 ทั้งหมด", e
                    )
        else:
            try:
                df_tga_granular = fabric.get_tga_target_salesman_granular(
                    emp_list, target_month, target_year
                )
            except Exception as e:
                logger.warning(
                    "get_tga_target_salesman_granular error: %s — เป้าจะเป็น 0 ทั้งหมด",
                    e,
                )

        grain_cols = [
            "emp_id",
            "sku",
            "qty",
            "salestype",
            "divisioncode",
            "areacode",
            "provincecode",
            "warehouse_code",
        ]

        try:
            p_grain = tga_grain_cache_path(sup_id, target_month, target_year)
            if df_tga_granular is None or df_tga_granular.empty:
                pd.DataFrame(columns=grain_cols).to_csv(p_grain, index=False)
            else:
                df_tga_granular.to_csv(p_grain, index=False)
            logger.info(
                "tga grain cache: %s (%d rows)", p_grain, len(df_tga_granular)
            )
        except Exception as e:
            logger.warning("tga grain cache write failed: %s", e)

        if df_tga_granular is not None and not df_tga_granular.empty:
            df_tga = (
                df_tga_granular.groupby(["emp_id", "sku"], as_index=False)["qty"]
                .sum()
            )
            df_tga = df_tga[df_tga["qty"] != 0]
        else:
            df_tga = pd.DataFrame(columns=["emp_id", "sku", "qty"])

        tga_skus: list[str] = []
        if df_tga is not None and not df_tga.empty:
            tga_skus = (
                df_tga["sku"].dropna().astype(str).str.strip().unique().tolist()
            )
        # แสดง/เกลี่ย/ส่ง Target Sun เฉพาะ SKU ที่มีเป้า TGA งวดนี้
        # ประวัติขาย (sold_skus) ใช้แค่เป็นน้ำหนักกระจายหีบ — ไม่รวมใน sku_union
        sku_union = list(dict.fromkeys(str(s).strip() for s in tga_skus if str(s).strip()))
        sold_only_excluded = len(set(sold_skus) - set(sku_union)) if sold_skus else 0
        if sold_only_excluded:
            logger.info(
                "SKU ที่เคยขายแต่ไม่มีเป้า TGA งวดนี้ (ไม่แสดง/ไม่เกลี่ย): %d",
                sold_only_excluded,
            )
        if not sku_union:
            logger.warning(
                "SuperCode=%s period=%02d/%d | team=%d | tga_granular=%d | ไม่มี SKU เป้า",
                sup_id,
                target_month,
                target_year,
                len(emp_list),
                len(df_tga_granular) if df_tga_granular is not None else 0,
            )
            enforce_tga_has_targets_for_period(
                fabric,
                target_month,
                target_year,
                df_tga,
                0,
                debug={
                    "supervisor_code": sup_id,
                    "team_size": len(emp_list),
                    "tga_granular_rows": int(
                        len(df_tga_granular) if df_tga_granular is not None else 0
                    ),
                },
                max_effective=ts_max_effective,
            )

        df_sku_base = pd.DataFrame()
        from . import fabric_cache as fc

        if sku_union:
            cached_product = fc.read_product_info_df(target_year, target_month)
            sku_set = (
                set(cached_product["sku"].astype(str))
                if cached_product is not None and not cached_product.empty
                else set()
            )
            if sku_set and all(str(s) in sku_set for s in sku_union):
                df_sku_base = cached_product[cached_product["sku"].astype(str).isin(sku_union)].copy()
            else:
                try:
                    # ต้องส่งงวดเป้าไปด้วย — ราคาในตารางมีช่วงวันที่ ถ้าไม่ส่งจะได้
                    # ราคา ณ วันนี้ ซึ่งเป็นราคาเก่าเมื่อทำเป้าของเดือนหน้าล่วงหน้า
                    df_fresh = fabric.get_product_info(
                        sku_list=sku_union,
                        target_year=target_year,
                        target_month=target_month,
                    )
                    if df_fresh is not None and not df_fresh.empty:
                        if cached_product is not None and not cached_product.empty:
                            merged = pd.concat([cached_product, df_fresh]).drop_duplicates(
                                subset=["sku"], keep="last"
                            )
                        else:
                            merged = df_fresh
                        fc.write_product_info_df(target_year, target_month, merged)
                        df_sku_base = merged[merged["sku"].astype(str).isin(sku_union)].copy()
                except Exception as e:
                    logger.warning("get_product_info error: %s", e)

        if df_sku_base.empty and sku_union:
            df_sku_base = pd.DataFrame({"sku": sku_union})

        price_latest = fc.read_price_map(target_year, target_month) or {}
        missing_price_skus = [s for s in sku_union if str(s) not in price_latest]
        if missing_price_skus:
            try:
                df_price = fabric.get_latest_price_per_box_by_sku(
                    target_month, target_year, sku_union
                )
                if df_price is not None and not df_price.empty:
                    fetched = dict(
                        zip(
                            df_price["sku"].astype(str),
                            df_price["price_per_box"].astype(float),
                        )
                    )
                    price_latest = {**price_latest, **fetched}
                    fc.write_price_map(target_year, target_month, price_latest)
            except Exception as e:
                logger.warning(
                    "get_latest_price_per_box_by_sku error: %s (price จะเป็น 0 + flag missing)",
                    e,
                )

        df_sku, df_sun_csv, emp_with_tga = _build_sku_and_sun_from_tga(
            df_tga, df_sku_base, emp_list, sku_union, price_latest_by_sku=price_latest
        )
        emp_with_tga_set = emp_with_tga

        # เขียนแยกราย (sup, งวด) — ไฟล์ global เดิมไม่มี sup_id ทีมจึงทับกันเอง
        atomic_write_csv(
            target_boxes_cache_path(sup_id, target_month, target_year), df_sku, index=False
        )
        atomic_write_csv(
            target_sun_cache_path(sup_id, target_month, target_year), df_sun_csv, index=False
        )
        logger.info(
            "บันทึกเป้าจาก Fabric (TGA): %d SKU, พนักงาน %d คน, มีแถว TGA %d คน",
            len(df_sku),
            len(df_sun_csv),
            len(emp_with_tga_set),
        )

    if df_sun_csv is None:
        _, df_sun_csv = load_target_csv_for(sup_id, target_month, target_year)

    # ── เป้าตั้งต้น: เก็บครั้งแรกที่งวดนี้ถูกเปิด ────────────────────────
    #
    # วางไว้ตรงนี้เพราะเป็นจุดเดียวที่ "ทุกเส้นทาง" มาบรรจบกัน — ทั้งตอนดึงใหม่จาก
    # Fabric และตอนอ่านจากไฟล์เป้าที่มีอยู่แล้ว (ถ้าวางไว้เฉพาะฝั่งดึงใหม่ ทีมที่
    # เคยเปิดงวดไว้ก่อนจะไม่มีวันได้ baseline เลย เพราะเข้าทางอ่านไฟล์ตลอด)
    #
    # เขียนครั้งเดียวเท่านั้น รอบต่อ ๆ ไปจึงเหลือแค่การเทียบว่าต่างจากตั้งต้นตรงไหน
    if not capture_baseline_once(sup_id, target_month, target_year, df_sku, df_sun_csv):
        _drift = diff_against_baseline(sup_id, target_month, target_year, df_sku, df_sun_csv)
        if _drift:
            logger.warning(
                "เป้างวดนี้ต่างจากตั้งต้น: %s %s-%02d | หีบ %d -> %d (%+d) | SKU เปลี่ยน %d | เป้าเงินเปลี่ยน %d คน",
                sup_id, target_year, target_month,
                _drift["boxes_before"], _drift["boxes_after"], _drift["boxes_delta"],
                _drift["sku_changed"], _drift["emp_target_changed"],
            )
            try:
                append_log(
                    level="warn",
                    email="",
                    role="system",
                    sup_id=sup_id,
                    action="target_baseline_drift",
                    message=(
                        f"เป้างวด {target_month:02d}/{target_year} ต่างจากตอนเปิดครั้งแรก — "
                        f"หีบรวม {_drift['boxes_before']} → {_drift['boxes_after']} "
                        f"({_drift['boxes_delta']:+d})"
                    ),
                    detail=_drift,
                )
            except Exception as e:   # การบันทึกต้องไม่ทำให้โหลดหน้าจอพัง
                logger.warning("บันทึก target_baseline_drift ไม่สำเร็จ: %s", e)

    sku_list = df_sku["sku"].tolist()

    # ── Step 3: merge target_sun ──────────────────────────
    df_emp = df_emp_fabric.copy()
    if df_sun_csv is not None and not df_sun_csv.empty:
        df_emp = pd.merge(
            df_emp, df_sun_csv[["emp_id", "target_sun"]], on="emp_id", how="left"
        )
    if "target_sun" not in df_emp.columns:
        df_emp["target_sun"] = 0.0
    df_emp["target_sun"] = df_emp["target_sun"].fillna(0.0)

    if emp_with_tga_set is not None:
        df_emp["has_tga_rows"] = (
            df_emp["emp_id"].astype(str).str.strip().isin(emp_with_tga_set)
        )
    else:
        df_emp["has_tga_rows"] = True

    df_emp["target_sun"] = pd.to_numeric(df_emp["target_sun"], errors="coerce").fillna(0.0)
    team_size_fabric = len(df_emp)

    ly_sales_by_emp: dict[str, float] = {}
    team_ids_for_ly = df_emp["emp_id"].astype(str).str.strip().tolist()
    if team_ids_for_ly:
        if fabric is None:
            try:
                fabric = FabricDAXConnector()
            except Exception as e:
                logger.warning("Fabric unavailable for LY visibility: %s", e)
        if fabric is not None:
            try:
                df_ly_team = fabric.get_ly_sales(
                    target_month, target_year, emp_list=team_ids_for_ly
                )
                if df_ly_team is not None and not df_ly_team.empty:
                    ly_sales_by_emp = {
                        str(r["emp_id"]).strip(): float(r["ly_sales"] or 0.0)
                        for _, r in df_ly_team.iterrows()
                        if str(r.get("emp_id") or "").strip()
                    }
            except Exception as e:
                logger.warning("get_ly_sales (employee visibility) failed: %s", e)

    df_emp, hidden_no_target, ly_only_shown = filter_employees_for_display(
        df_emp, ly_sales_by_emp
    )
    df_emp["allocation_eligible"] = (
        df_emp["has_tga_rows"].astype(bool) & (df_emp["target_sun"] > 0)
    )
    if hidden_no_target > 0 or ly_only_shown > 0:
        logger.info(
            "กรองพนักงานงวด %02d/%d: ซ่อน %d | แสดงจากยอด LY ไม่มีเป้า %d | เหลือ %d จาก %d คน",
            target_month,
            target_year,
            hidden_no_target,
            ly_only_shown,
            len(df_emp),
            team_size_fabric,
        )
    if df_emp.empty:
        logger.warning(
            "SuperCode=%s | team=%d | ไม่มีพนักงานที่มีเป้าในงวด %02d/%d",
            sup_id,
            team_size_fabric,
            target_month,
            target_year,
        )
    emp_list = df_emp["emp_id"].astype(str).str.strip().tolist()
    excluded_from_allocation = 0

    # ── Step 4: History caches (3M/6M + LY same-month + prev-month) ──
    sku_warnings: list[dict] = []
    sku_links = read_links()
    dax_sku_list = expand_skus_for_dax(sku_list, sku_links)
    if len(dax_sku_list) > len(sku_list):
        logger.info(
            "sku_links: ขยาย SKU สำหรับ DAX %d → %d รหัส",
            len(sku_list),
            len(dax_sku_list),
        )
    df_hist = _load_history(
        "3 เดือน", hist_cache_path(sup_id, target_month, target_year, n_months=3),
        lambda: fabric.get_historical_sales(
            target_month, target_year,
            sku_list=dax_sku_list, emp_list=emp_list, n_months=3,
        ),
        sku_links, sup_id,
    )
    df_hist6 = _load_history(
        "6 เดือน", hist_cache_path(sup_id, target_month, target_year, n_months=6),
        lambda: fabric.get_historical_sales(
            target_month, target_year,
            sku_list=dax_sku_list, emp_list=emp_list, n_months=6,
        ),
        sku_links, sup_id,
    )
    df_lysm = _load_history(
        "ปีที่แล้วเดือนเดียวกัน",
        hist_ly_same_month_cache_path(sup_id, target_month, target_year),
        lambda: fabric.get_same_month_prior_year_by_emp_sku(
            target_month, target_year, sku_list=dax_sku_list, emp_list=emp_list,
        ),
        sku_links, sup_id,
    )
    df_prev = _load_history(
        "เดือนที่แล้ว",
        hist_prev_month_cache_path(sup_id, target_month, target_year),
        lambda: fabric.get_prev_month_by_emp_sku(
            target_month, target_year, sku_list=dax_sku_list, emp_list=emp_list,
        ),
        sku_links, sup_id,
    )

    # ── Step 5c: calendar-year caches (CY + LY) — ใช้ตรวจสินค้าใหม่ตอน optimize ──
    try:
        for cy in (int(target_year), int(target_year) - 1):
            df_cy = fabric.get_calendar_year_sales_by_emp_sku(
                cy, sku_list=dax_sku_list, emp_list=emp_list
            )
            pcy = hist_calendar_year_cache_path(sup_id, cy)
            if df_cy is not None and not df_cy.empty:
                df_cy = collapse_hist_to_canonical(df_cy, sku_links)
                df_cy.to_csv(pcy, index=False)
                logger.info(
                    "historical calendar-year %d cache: %d rows → %s",
                    cy,
                    len(df_cy),
                    pcy,
                )
            else:
                pd.DataFrame(
                    columns=["emp_id", "sku", "hist_boxes", "hist_amount"]
                ).to_csv(pcy, index=False)
                logger.info("historical calendar-year %d: empty → %s", cy, pcy)
    except Exception as e:
        logger.warning("historical calendar-year caches skipped: %s", e)

    # ── Step 5b: เติมตัวเลขสรุปให้หน้า Step1 (LY ยอดขาย / เฉลี่ย 3M) ─────────
    # Frontend ใช้ฟิลด์ชื่อ: ly_sales, hist_avg_3m
    df_emp["ly_sales"] = (
        df_emp["emp_id"]
        .astype(str)
        .str.strip()
        .map(lambda e: float(ly_sales_by_emp.get(e, 0.0)))
        .fillna(0.0)
    )
    df_emp["hist_avg_3m"] = 0.0

    try:
        if df_lysm is not None and not df_lysm.empty:
            ly_by_emp_sku = (
                df_lysm.groupby("emp_id", as_index=True)["hist_amount"]
                .sum()
                .astype(float)
                .to_dict()
            )
            for emp_id, amt in ly_by_emp_sku.items():
                eid = str(emp_id).strip()
                cur = float(ly_sales_by_emp.get(eid, 0.0) or 0.0)
                if float(amt or 0.0) > cur:
                    ly_sales_by_emp[eid] = float(amt)
            df_emp["ly_sales"] = (
                df_emp["emp_id"]
                .astype(str)
                .str.strip()
                .map(lambda e: float(ly_sales_by_emp.get(e, 0.0)))
                .fillna(0.0)
            )
            df_emp["ly_sales"] = pd.to_numeric(df_emp["ly_sales"], errors="coerce").fillna(0.0)
    except Exception as e:
        logger.warning("compute ly_sales failed: %s", e)

    try:
        if df_hist is not None and not df_hist.empty:
            avg3_by_emp = (
                (df_hist.groupby("emp_id", as_index=True)["hist_amount"].sum().astype(float) / 3.0)
                .to_dict()
            )
            df_emp["hist_avg_3m"] = (
                df_emp["emp_id"].astype(str).str.strip().map(avg3_by_emp).fillna(0.0)
            )
            df_emp["hist_avg_3m"] = pd.to_numeric(df_emp["hist_avg_3m"], errors="coerce").fillna(0.0)
    except Exception as e:
        logger.warning("compute hist_avg_3m failed: %s", e)

    # ── ยอดขายย้อนหลังเป็น 0 ทั้งทีม = ต้องบอก ไม่ใช่ปล่อยให้เดาเอง ──────────
    #
    # เป้ามาจาก Target Sun แต่ "ยอดขายเฉลี่ย 3 เดือน / ปีที่แล้ว" ยังมาจาก Fabric
    # สองเส้นทางนี้พังแยกกันได้ · ถ้าฝั่ง Fabric ล่มหรือกรองไม่ตรง ทุกช่องจะขึ้น 0
    # เหมือนกันหมดทั้งทีม ซึ่งหน้าตาเหมือน "ทีมนี้ไม่เคยขายอะไรเลย" เป๊ะ ๆ
    # ของเดิมเตือนเฉพาะตอนดึง 3 เดือนไม่ได้เลย (no_history) — แต่เคสที่ดึงมาได้
    # แล้วจับคู่รหัสพนักงานไม่ติดสักคน กลับเงียบสนิท ทั้งที่ผลลัพธ์บนจอเหมือนกัน
    try:
        _ly_zero = float(pd.to_numeric(df_emp.get("ly_sales", 0), errors="coerce").fillna(0).sum()) <= 0
        _avg_zero = float(pd.to_numeric(df_emp.get("hist_avg_3m", 0), errors="coerce").fillna(0).sum()) <= 0
        _rows_3m = 0 if df_hist is None or df_hist.empty else len(df_hist)
        _rows_ly = 0 if df_lysm is None or df_lysm.empty else len(df_lysm)
        if len(df_emp) > 0 and _ly_zero and _avg_zero:
            if _rows_3m or _rows_ly:
                detail = (
                    f"ดึงมาได้ {_rows_3m} แถว (3 เดือน) และ {_rows_ly} แถว (ปีที่แล้ว) "
                    "แต่จับคู่กับรหัสพนักงานในทีมไม่ติดสักคน — รหัสพนักงานสองฝั่งไม่ตรงกัน"
                )
            else:
                detail = "ดึงจาก Fabric ไม่ได้เลยสักแถว — เป้ายังถูกต้องเพราะมาจาก Target Sun คนละทาง"
            logger.warning(
                "ยอดขายย้อนหลังเป็น 0 ทั้งทีม %s %s-%02d: %s",
                sup_id, target_year, target_month, detail,
            )
            sku_warnings.append(
                {
                    "type": "history_all_zero",
                    "sku": "",
                    "brand": "",
                    "message": (
                        f"⚠️ ยอดขายย้อนหลังเป็น 0 ทั้งทีม ({len(df_emp)} คน) — {detail} "
                        "· การกระจายหีบจะเกลี่ยเท่า ๆ กันแทนการอิงประวัติขาย"
                    ),
                }
            )
    except Exception as e:                      # การเตือนต้องไม่ทำให้หน้าจอพัง
        logger.warning("ตรวจยอดขายย้อนหลังเป็น 0 ไม่สำเร็จ: %s", e)

    # ── Step 6: Warehouse ─────────────────────────────────
    try:
        df_wh = fabric.get_warehouse_by_emp(emp_list)
        if not df_wh.empty:
            df_emp = pd.merge(
                df_emp, df_wh[["emp_id", "warehouse_code"]], on="emp_id", how="left"
            )
    except Exception as e:
        logger.warning("warehouse: %s", e)
    if "warehouse_code" not in df_emp.columns:
        df_emp["warehouse_code"] = ""
    df_emp["warehouse_code"] = df_emp["warehouse_code"].fillna("")

    numeric_cols = df_emp.select_dtypes(include=["number"]).columns
    df_emp[numeric_cols] = df_emp[numeric_cols].fillna(0)
    for col in ["emp_name", "manager_code", "warehouse_code"]:
        if col in df_emp.columns:
            df_emp[col] = df_emp[col].fillna("")

    logger.info("Response: %d emp, %d sku", len(df_emp), len(df_sku))

    if van_excluded > 0:
        sku_warnings.append(
            {
                "type": "employees_excluded_van_code",
                "sku": "",
                "brand": "",
                "message": (
                    f"ตัดพนักงานรหัส V (Van) {van_excluded} คน — ไม่แสดงและไม่นำมาคำนวณ"
                ),
            }
        )

    if ly_only_shown > 0:
        sku_warnings.append(
            {
                "type": "employees_shown_ly_no_target",
                "sku": "",
                "brand": "",
                "message": (
                    f"แสดงใน Step 1 เท่านั้น {ly_only_shown} คน "
                    f"(เคยขายเดือนเดียวกันปีที่แล้ว แต่ไม่มีเป้างวดนี้) "
                    f"— ไม่เข้าขั้นกำหนดเป้าและไม่กระจายหีบ"
                ),
            }
        )

    if hidden_no_target > 0:
        sku_warnings.append(
            {
                "type": "employees_hidden_no_target",
                "sku": "",
                "brand": "",
                "message": (
                    f"ซ่อนพนักงาน {hidden_no_target} คนที่ไม่มีเป้าและไม่มียอดขายปีที่แล้ว "
                    f"(แสดง {len(df_emp)} คน)"
                ),
            }
        )

    if not use_legacy and sold_only_excluded > 0:
        sku_warnings.append(
            {
                "type": "sold_only_skus_excluded",
                "sku": "",
                "brand": "",
                "message": (
                    f"มี {sold_only_excluded} SKU ที่ทีมเคยขายใน 6 เดือนย้อนหลัง "
                    "แต่ไม่มีเป้าใน Target Sun งวดนี้ — ไม่แสดงใน Dashboard และไม่ส่งเข้า Target Sun "
                    "(ใช้ประวัติขายเป็นน้ำหนักกระจายหีบเท่านั้น)"
                ),
            }
        )

    if use_legacy and df_sun_csv is not None and not df_sun_csv.empty:
        sun_emp_ids = set(df_sun_csv["emp_id"].astype(str).str.strip())
        fabric_emp_ids = set(str(e) for e in emp_list)
        unmatched = sun_emp_ids - fabric_emp_ids
        if unmatched:
            logger.warning("target_sun emp_id ไม่ตรงกับ Fabric: %s", unmatched)
            sku_warnings.append(
                {
                    "type": "emp_mismatch",
                    "sku": "",
                    "brand": "",
                    "message": f"มี emp_id ใน target_sun.csv ไม่พบใน Fabric: {sorted(list(unmatched))[:20]}",
                }
            )

    if df_hist is None or df_hist.empty:
        sku_warnings.append(
            {
                "type": "no_history",
                "sku": "",
                "brand": "",
                "message": "⚠️ ไม่สามารถดึงประวัติขายจาก Fabric ได้ — การกระจายหีบจะใช้ EVEN แทนประวัติ",
            }
        )

    tga_period_status = "ok"
    if not use_legacy and fabric is not None:
        total_sup_boxes = 0
        if "supervisor_target_boxes" in df_sku.columns:
            total_sup_boxes = int(
                pd.to_numeric(df_sku["supervisor_target_boxes"], errors="coerce")
                .fillna(0)
                .sum()
            )
        enforce_tga_has_targets_for_period(
            fabric,
            target_month,
            target_year,
            df_tga,
            total_sup_boxes,
            debug={
                "supervisor_code": sup_id,
                "team_with_targets": int(df_emp["allocation_eligible"].sum()),
                "sku_count": len(df_sku),
            },
            max_effective=ts_max_effective if targetsun_read.is_enabled() else None,
        )

    if sku_warnings:
        logger.info("reconciliation warnings: %d รายการ", len(sku_warnings))

    # CSV เดิมอาจไม่มี flag ราคา — เติมให้ครบก่อนส่ง JSON
    df_sku = df_sku.copy()
    if "price_from_sales_history" not in df_sku.columns:
        df_sku["price_from_sales_history"] = (
            df_sku["price_from_cfm_cost"].astype(bool)
            if "price_from_cfm_cost" in df_sku.columns
            else False
        )
    if "price_from_cfm_cost" in df_sku.columns:
        df_sku.drop(columns=["price_from_cfm_cost"], inplace=True, errors="ignore")
    if "price_missing" not in df_sku.columns:
        df_sku["price_missing"] = (
            pd.to_numeric(df_sku.get("price_per_box", 0), errors="coerce").fillna(0.0)
            <= 0
        )

    linked_skus = {
        row["canonical_sku"]: extra_aliases_for_canonical(row["canonical_sku"], sku_links)
        for row in sku_links
        if extra_aliases_for_canonical(row["canonical_sku"], sku_links)
    }
    sku_ids_list = df_sku["sku"].astype(str).str.strip().tolist()
    if linked_skus:
        df_sku["linked_history_skus"] = df_sku["sku"].astype(str).str.strip().map(
            lambda s: linked_skus.get(s, [])
        )
        sku_id_set = set(sku_ids_list)
        for canon, aliases in linked_skus.items():
            if canon in sku_id_set:
                sku_warnings.append(
                    {
                        "type": "sku_linked_history",
                        "sku": canon,
                        "brand": "",
                        "message": (
                            f"SKU {canon} ผูกประวัติรหัสเก่า: {', '.join(aliases)}"
                        ),
                    }
                )

    new_product_skus, new_products_detection_mode = detect_new_product_skus(
        sup_id, target_year, sku_ids_list, df_hist
    )

    price_by_sku = dict(
        zip(
            df_sku["sku"].astype(str).str.strip(),
            pd.to_numeric(df_sku["price_per_box"], errors="coerce").fillna(0.0),
        )
    )
    ly_amount_by_emp_wh: dict[tuple[str, str], float] | None = None
    avg3_amount_by_emp_wh: dict[tuple[str, str], float] | None = None
    wh_split_emps = [
        e
        for e, whs in warehouses_per_emp_from_tga(df_tga_granular).items()
        if len(set(whs)) >= 2
    ]
    if wh_split_emps and fabric is not None:
        try:
            df_ly_wh = fabric.get_ly_same_month_amount_by_emp_wh(
                target_month, target_year, wh_split_emps
            )
            if not df_ly_wh.empty:
                ly_amount_by_emp_wh = {
                    (str(r["emp_id"]).strip(), str(r.get("warehouse_code") or "").strip()): float(
                        r.get("hist_amount") or 0.0
                    )
                    for _, r in df_ly_wh.iterrows()
                }
        except Exception as e:
            logger.warning("ly amount by emp×wh skipped: %s", e)
        try:
            df_3m_wh = fabric.get_sales_amount_by_emp_wh(
                target_month, target_year, wh_split_emps, n_months=3
            )
            if not df_3m_wh.empty:
                avg3_amount_by_emp_wh = {
                    (str(r["emp_id"]).strip(), str(r.get("warehouse_code") or "").strip()): float(
                        r.get("hist_amount") or 0.0
                    )
                    / 3.0
                    for _, r in df_3m_wh.iterrows()
                }
        except Exception as e:
            logger.warning("3M amount by emp×wh skipped: %s", e)

    emp_records = expand_employee_rows(
        _clean(df_emp),
        df_tga_granular,
        price_by_sku,
        ly_amount_by_emp_wh=ly_amount_by_emp_wh,
        avg3_amount_by_emp_wh=avg3_amount_by_emp_wh,
    )
    emp_records = _enrich_employee_allocation_flags(emp_records, sup_id)
    if wh_split_emps:
        sku_warnings.append(
            {
                "type": "wh_split_active",
                "sku": "",
                "brand": "",
                "message": (
                    f"พนักงาน {len(wh_split_emps)} คนมีหลายคลัง — "
                    "แสดงแยกตาม W/H ใน Dashboard"
                ),
            }
        )

    payload = {
        "employees": emp_records,
        "skus": _clean(df_sku),
        "sku_warnings": sku_warnings,
        "tga_period_status": tga_period_status,
        "supervisor_name": sup_name,
        "new_product_skus": new_product_skus,
        "new_products_detection_mode": new_products_detection_mode,
        "target_read_source": targetsun_read.get_target_read_source(),
        "data_from_cache": False,
        "data_cached_at": None,
    }
    write_cached_employee_payload(sup_id, target_month, target_year, payload)
    return payload


def _authoritative_price_map(
    skus: set[str],
    target_month: int,
    target_year: int,
) -> dict[str, tuple[float, bool]]:
    """
    ราคาต่อหีบที่ถือว่าถูกต้องของงวดนี้ — {sku: (ราคา, มาจากประวัติขายไหม)}

    ลำดับเดียวกับตอนสร้างเป้าราย SKU (ดู _build_sku_and_sun_from_tga):
    CREDITUNITPRICE ณ วันที่ 1 ของงวดก่อน แล้วค่อยถอยไปราคาจากยอดขายจริง
    อ่านจากแคชในเครื่องล้วน — ไม่ยิง Fabric/Target Sun เพิ่มแม้แต่ครั้งเดียว
    """
    from . import fabric_cache as fc

    out: dict[str, tuple[float, bool]] = {}
    try:
        df_prod = fc.read_product_info_df(target_year, target_month)
    except Exception as e:
        logger.warning("อ่านแคชสินค้าเพื่อเทียบราคาไม่ได้: %s", e)
        df_prod = None
    if df_prod is not None and not df_prod.empty and "credit_unit_price" in df_prod.columns:
        for _, r in df_prod.iterrows():
            sku = str(r.get("sku") or "").strip()
            if sku not in skus:
                continue
            try:
                price = float(r.get("credit_unit_price") or 0)
            except (TypeError, ValueError):
                continue
            if price > 0:
                out[sku] = (price, False)

    missing = skus - set(out)
    if missing:
        try:
            price_map = fc.read_price_map(target_year, target_month) or {}
        except Exception as e:
            logger.warning("อ่านแคชราคาจากยอดขายไม่ได้: %s", e)
            price_map = {}
        for sku in missing:
            try:
                price = float(price_map.get(sku) or 0)
            except (TypeError, ValueError):
                continue
            if price > 0:
                out[sku] = (price, True)
    return out


def _tga_qty_by_emp_sku(sup_id: str, target_month: int, target_year: int):
    """หีบราย (พนักงาน, คลัง, SKU) จากแคชแถวเป้าดิบ — ใช้คิดส่วนต่างเป้าเงินรายคน"""
    path = tga_grain_cache_path(sup_id, target_month, target_year)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, dtype={"emp_id": str, "sku": str})
    except Exception as e:
        logger.warning("อ่าน %s ไม่ได้: %s", path, e)
        return None
    if df.empty or "emp_id" not in df.columns or "sku" not in df.columns:
        return None
    df["emp_id"] = df["emp_id"].astype(str).str.strip()
    df["sku"] = df["sku"].astype(str).str.strip()
    df["qty"] = pd.to_numeric(df.get("qty", 0), errors="coerce").fillna(0.0)
    if "warehouse_code" not in df.columns:
        df["warehouse_code"] = ""
    df["warehouse_code"] = df["warehouse_code"].fillna("").astype(str).str.strip()
    return df


def _apply_price_fix_to_payload(
    payload: dict[str, Any],
    fixes: dict[str, tuple[float, float, bool]],
    target_month: int,
    target_year: int,
) -> bool:
    """
    แก้ราคาของทีมเดียวให้ตรงกับราคาที่ถูกต้อง แล้วขยับเป้าเงินรายคนตามส่วนต่าง

    ใช้วิธี "บวกส่วนต่าง" ไม่ใช่คิดเป้าเงินใหม่ทั้งก้อน เพราะเป้าเงินรายคนที่มีอยู่
    ผ่านการแยกตามคลัง (wh_split) มาแล้ว คิดใหม่จากศูนย์จะทำให้การแยกนั้นหายไป
    ส่วนต่างต่อคน = Σ หีบของคนนั้นใน SKU ที่ราคาเปลี่ยน × (ราคาใหม่ − ราคาเก่า)

    เขียนไฟล์เป้าของทีมนั้นทับด้วย — ขั้นกระจายอ่านเป้าหีบจากไฟล์ ไม่ใช่จาก payload
    (core.targets.load_summed_target_boxes) ถ้าแก้แต่ในหน่วยความจำ ตอนกระจายจริง
    จะกลับไปใช้ราคาเก่าอีก แล้วเป้าเงินก็เพี้ยนเหมือนเดิม
    """
    sid = str(payload.get("_source_sup_id") or "").strip().upper()
    if not sid or not fixes:
        return False

    grain = _tga_qty_by_emp_sku(sid, target_month, target_year)
    if grain is None:
        logger.warning("ไม่มีแถวเป้าดิบของ %s — ข้ามการแก้ราคาให้ตรงกัน", sid)
        return False

    changed = grain[grain["sku"].isin(fixes)].copy()
    if changed.empty:
        # ทีมนี้ไม่มีหีบใน SKU ที่ราคาเปลี่ยน — แก้แค่ราคาในตาราง ไม่ต้องขยับเป้าเงิน
        delta_emp: dict[str, float] = {}
        delta_key: dict[tuple[str, str], float] = {}
    else:
        changed["_delta"] = changed.apply(
            lambda r: float(r["qty"]) * (fixes[r["sku"]][1] - fixes[r["sku"]][0]), axis=1
        )
        delta_emp = changed.groupby("emp_id")["_delta"].sum().to_dict()
        delta_key = {
            (str(e), str(w)): float(v)
            for (e, w), v in changed.groupby(["emp_id", "warehouse_code"])["_delta"].sum().items()
        }

    for s in payload.get("skus") or []:
        sku = str(s.get("sku") or "").strip()
        if sku in fixes:
            _old, new_price, from_history = fixes[sku]
            s["price_per_box"] = new_price
            s["price_missing"] = False
            s["price_from_sales_history"] = bool(from_history)

    for emp in payload.get("employees") or []:
        eid = str(emp.get("emp_id") or "").strip()
        wh = str(emp.get("warehouse_code") or "").strip()
        d = delta_key.get((eid, wh)) if (wh and emp.get("wh_split")) else delta_emp.get(eid)
        if d:
            emp["target_sun"] = round(float(emp.get("target_sun") or 0) + float(d), 2)

    try:
        p_sku = target_boxes_cache_path(sid, target_month, target_year)
        df_sku = pd.DataFrame(payload.get("skus") or [])
        if not df_sku.empty:
            atomic_write_csv(p_sku, df_sku, index=False)
        p_sun = target_sun_cache_path(sid, target_month, target_year)
        if os.path.exists(p_sun):
            df_sun = pd.read_csv(p_sun, dtype={"emp_id": str})
            df_sun["emp_id"] = df_sun["emp_id"].astype(str).str.strip()
            df_sun["target_sun"] = df_sun.apply(
                lambda r: round(
                    float(pd.to_numeric(r.get("target_sun"), errors="coerce") or 0)
                    + float(delta_emp.get(str(r["emp_id"]), 0.0)),
                    2,
                ),
                axis=1,
            )
            atomic_write_csv(p_sun, df_sun, index=False)
    except Exception as e:                      # แก้ในหน่วยความจำสำเร็จแล้ว อย่าให้ล้มทั้งคำขอ
        logger.warning("เขียนไฟล์เป้าที่แก้ราคาแล้วของ %s ไม่สำเร็จ: %s", sid, e)
    return True


def reconcile_prices_across_payloads(
    payloads: list[dict[str, Any]],
    target_month: int,
    target_year: int,
) -> list[dict[str, str]]:
    """
    ทำให้ทุกทีมใช้ราคาต่อหีบชุดเดียวกันก่อนรวมเป็นก้อนเดียว

    ไฟล์เป้าของแต่ละทีมถูกสร้างคนละเวลา ทีมที่สร้างไว้ก่อนสินค้าขึ้นราคาจึงยังถือ
    ราคาเก่าค้างอยู่ พอเอามารวม merge_employees_payloads บวกแต่หีบ ส่วนราคาใช้ของ
    ทีมแรกที่เจอ — ผลรวมของก้อนรวมเลยไม่เท่าผลบวกรายทีม แล้ว revenue_scale
    (OR_engine._revenue_scale_factor) ก็ไปดันเป้าเงินรายคนทั้งภาคตามส่วนต่างนั้น
    ผลกระจายจึงห่างจากเป้าเหลืองเป็นหลักแสนหลักล้านทั้งที่ควรห่างแค่หลักพัน

    คนที่เปิดหน้ารวมภาคตั้งใจจะกำหนดเป้าทั้งภาคอยู่แล้ว จึงซ่อมให้เองตรงนี้เลย
    ไม่ใช่ให้ไปไล่เปิดทีมทีละทีมเอง · อ่านแต่แคชในเครื่อง ไม่ยิงระบบภายนอกเพิ่ม
    """
    if len(payloads) < 2:
        return []

    seen: dict[str, dict[str, float]] = {}
    for p in payloads:
        sid = str(p.get("_source_sup_id") or "").strip().upper()
        for s in p.get("skus") or []:
            sku = str(s.get("sku") or "").strip()
            if not sku:
                continue
            try:
                price = round(float(s.get("price_per_box") or 0), 4)
            except (TypeError, ValueError):
                continue
            if price > 0:
                seen.setdefault(sku, {})[sid] = price

    conflicts = {k: v for k, v in seen.items() if len(set(v.values())) > 1}
    if not conflicts:
        return []

    truth = _authoritative_price_map(set(conflicts), target_month, target_year)
    report: list[dict[str, str]] = []
    per_payload: dict[str, dict[str, tuple[float, float, bool]]] = {}

    # แคชราคาหมดอายุได้ (TTL 1 วัน) ตอนนั้นยังตัดสินได้จากไฟล์เป้าที่ใหม่ที่สุด —
    # ทีมที่เพิ่งดึงข้อมูลย่อมถือราคาที่ใหม่กว่าทีมที่สร้างไฟล์ทิ้งไว้ตั้งแต่วันก่อน
    # ดีกว่าปล่อยให้ "ทีมแรกที่เจอ" ชนะแบบสุ่มเหมือนเดิม
    def _newest_price(by_sup: dict[str, float]) -> float | None:
        best, best_mtime = None, -1.0
        for sid, price in by_sup.items():
            try:
                mt = os.path.getmtime(
                    target_boxes_cache_path(sid, target_month, target_year)
                )
            except OSError:
                continue
            if mt > best_mtime:
                best, best_mtime = price, mt
        return best

    for sku, by_sup in sorted(conflicts.items()):
        entry = truth.get(sku)
        if not entry:
            newest = _newest_price(by_sup)
            if newest is None:
                report.append({
                    "sku": sku,
                    "status": "unresolved",
                    "detail": " · ".join(f"{k} {v:,.2f}" for k, v in sorted(by_sup.items())),
                })
                continue
            entry = (newest, False)
        correct, from_history = entry
        stale = {k: v for k, v in by_sup.items() if abs(v - correct) > 0.005}
        if not stale:
            continue
        for sid, old in stale.items():
            per_payload.setdefault(sid, {})[sku] = (old, correct, from_history)
        report.append({
            "sku": sku,
            "status": "fixed",
            "detail": (
                f"{correct:,.2f} · แก้ให้ "
                + ", ".join(f"{k} ({v:,.2f})" for k, v in sorted(stale.items()))
            ),
        })

    fixed_sups: list[str] = []
    for p in payloads:
        sid = str(p.get("_source_sup_id") or "").strip().upper()
        fixes = per_payload.get(sid)
        if fixes and _apply_price_fix_to_payload(p, fixes, target_month, target_year):
            fixed_sups.append(sid)

    if fixed_sups:
        logger.info(
            "ปรับราคาให้ตรงกันก่อนรวมภาค: %d SKU ใน %d ทีม (%s)",
            sum(len(v) for v in per_payload.values()),
            len(fixed_sups),
            ", ".join(sorted(fixed_sups)),
        )
    return report


def merge_employees_payloads(
    payloads: list[dict[str, Any]],
    *,
    aggregate_label: str,
    aggregate_sup_ids: list[str],
    price_report: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """รวมหลาย supervisor payload เป็นมุมมองเดียว (read-only overview)"""
    if not payloads:
        raise HTTPException(404, detail="ไม่มีข้อมูลจาก Supervisor ที่เลือก")

    employees: list[dict[str, Any]] = []
    sku_map: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, Any]] = []
    new_products: set[str] = set()
    skipped: list[dict[str, str]] = []
    # เป้าหีบแยกรายซุป — ต้องเก็บไว้ ไม่ใช่บวกทิ้ง
    # โหมดรวมภาคย้ายหีบข้ามทีมได้ตามที่ออกแบบไว้ หน้าจอจึงต้องบอกได้ว่า
    # ตอนนี้แต่ละทีม "เกิน/ขาด" เป้าของตัวเองอยู่เท่าไร ไม่งั้นย้ายไปโดยไม่รู้ตัว
    target_by_sup: dict[str, dict[str, int]] = {}
    # SKU ที่ราคาไม่ตรงกันระหว่างทีม — ต้องบอกผู้ใช้ ไม่ใช่เกลี่ยเงียบ ๆ
    price_conflicts: dict[str, dict[str, float]] = {}

    for p in payloads:
        sid = str(p.get("_source_sup_id") or "").strip().upper()
        for emp in p.get("employees") or []:
            row = dict(emp)
            row["supervisor_code"] = sid
            employees.append(row)
        for s in p.get("skus") or []:
            sku = str(s.get("sku") or "").strip()
            if not sku:
                continue
            boxes = float(s.get("supervisor_target_boxes") or 0)
            if boxes and sid:
                per_sup = target_by_sup.setdefault(sid, {})
                per_sup[sku] = int(per_sup.get(sku, 0) + round(boxes))
            price = round(float(s.get("price_per_box") or 0), 4)
            if sku in sku_map:
                # ราคาต้องเท่ากันทุกทีมในงวดเดียวกัน — ถ้าไม่เท่า แปลว่าไฟล์เป้าของบางทีม
                # ถูกสร้างไว้ก่อนราคาเปลี่ยน (หรือก่อนแก้บั๊กราคา) แล้วไม่เคยสร้างใหม่
                # ตรงนี้บวกแต่หีบ ส่วนราคาใช้ของทีมแรกที่เจอ ผลรวมของก้อนรวมจึงไม่เท่ากับ
                # ผลบวกรายทีม → revenue_scale เพี้ยน → เป้าเงินรายคนถูกสเกลผิดไปทั้งภาค
                kept = round(float(sku_map[sku].get("price_per_box") or 0), 4)
                if price > 0 and kept > 0 and abs(price - kept) > 0.005:
                    price_conflicts.setdefault(sku, {})[sid or "?"] = price
                    price_conflicts[sku].setdefault("_kept", kept)
                sku_map[sku]["supervisor_target_boxes"] = (
                    float(sku_map[sku].get("supervisor_target_boxes") or 0) + boxes
                )
            else:
                sku_map[sku] = dict(s)
                sku_map[sku]["supervisor_target_boxes"] = boxes
        for w in p.get("sku_warnings") or []:
            row = dict(w)
            if sid and str(row.get("type") or "") != "aggregate_view":
                row["sup_id"] = sid
            warnings.append(row)
        for np in p.get("new_product_skus") or []:
            new_products.add(str(np).strip())

    employees.sort(key=lambda e: (str(e.get("supervisor_code") or ""), str(e.get("emp_id") or "")))
    employees = _enrich_employee_allocation_flags(employees)
    skus = sorted(sku_map.values(), key=lambda s: str(s.get("sku") or ""))

    if price_conflicts:
        # ถึงตรงนี้แปลว่า reconcile_prices_across_payloads ซ่อมไม่ได้ (ไม่มีราคาในแคช
        # หรือไม่มีแถวเป้าดิบให้คิดส่วนต่าง) — ต้องบอกตรง ๆ ว่ายอดจะเพี้ยน
        sample = []
        for sku in sorted(price_conflicts)[:5]:
            others = {k: v for k, v in price_conflicts[sku].items() if k != "_kept"}
            kept = price_conflicts[sku].get("_kept", 0)
            teams = ", ".join(f"{k} {v:,.2f}" for k, v in sorted(others.items()))
            sample.append(f"{sku} (ใช้ {kept:,.2f} · {teams})")
        logger.warning(
            "ราคาไม่ตรงกันระหว่างทีมในโหมดรวม (ซ่อมอัตโนมัติไม่ได้): %d SKU — %s",
            len(price_conflicts), "; ".join(sample),
        )
        warnings.insert(
            0,
            {
                "type": "aggregate_price_conflict",
                "sku": "",
                "brand": "",
                "message": (
                    f"⚠️ ราคาต่อหีบไม่ตรงกันระหว่างทีม {len(price_conflicts)} SKU และระบบ "
                    f"ปรับให้เองไม่ได้ — {'; '.join(sample)} · เป้าเงินรายคนจะถูกดันตามส่วนต่าง "
                    "แก้โดยเปิดหน้าทีมที่ราคาเก่าแล้วกดโหลดข้อมูลใหม่"
                ),
            },
        )

    fixed = [r for r in (price_report or []) if r.get("status") == "fixed"]
    if fixed:
        head = "; ".join(f"{r['sku']} → {r['detail']}" for r in fixed[:5])
        more = f" · และอีก {len(fixed) - 5} SKU" if len(fixed) > 5 else ""
        warnings.insert(
            0,
            {
                "type": "aggregate_price_reconciled",
                "sku": "",
                "brand": "",
                "message": (
                    f"ปรับราคาต่อหีบให้ตรงกันทั้งภาคแล้ว {len(fixed)} SKU — {head}{more} "
                    "· เป้าเงินรายคนของทีมที่ราคาเก่าถูกคิดใหม่ตามราคาที่ถูกต้อง"
                ),
            },
        )

    warnings.insert(
        0,
        {
            "type": "aggregate_view",
            "sku": "",
            "brand": "",
            "message": (
                f"โหมดดูรวม ({aggregate_label}) — {len(aggregate_sup_ids)} ซุป, "
                f"{len(employees)} พนักงาน · ผู้จัดการ/ซุปในกลุ่มเดียวกันกระจายหีบทั้งภาคได้"
            ),
        },
    )

    return {
        "employees": employees,
        "skus": skus,
        "sku_warnings": warnings,
        "tga_period_status": "ok",
        "supervisor_name": aggregate_label,
        "new_product_skus": sorted(new_products),
        "new_products_detection_mode": "aggregate",
        "aggregate_mode": True,
        "aggregate_sup_ids": aggregate_sup_ids,
        "skipped_supervisors": skipped,
        # {sup_id: {sku: เป้าหีบของทีมนั้น}} — ใช้แสดงแถวรวมรายทีมในตารางรวมภาค
        "target_boxes_by_sup": target_by_sup,
    }


def _aggregate_load_workers(n_teams: int) -> int:
    """
    จำนวน thread สำหรับโหลดรวมภาค — ไม่เกินจำนวนทีมจริง
    ตั้ง AGGREGATE_LOAD_WORKERS=1 = กลับไปโหลดทีละทีม (พฤติกรรมเดิม)
    เพดาน 8 กันไม่ให้ไปเบียด anyio threadpool ของ FastAPI (ค่าเริ่มต้น 40 threads)
    """
    raw = (os.environ.get("AGGREGATE_LOAD_WORKERS") or "6").strip()
    try:
        want = int(raw)
    except ValueError:
        want = 6
    return max(1, min(want, 8, max(1, int(n_teams))))


def load_employees_bulk(
    sup_ids: list[str],
    target_month: int,
    target_year: int,
    *,
    aggregate_label: str,
    refresh: bool = False,
    can_write: bool = False,
) -> dict[str, Any]:
    """
    can_write: มุมมองนี้กระจาย/บันทึกได้จริงไหม — ตัวเดียวที่เปิดให้ซ่อมราคาข้ามทีม

    การซ่อมเขียนทับไฟล์เป้าของทีมอื่น คนที่แค่ "เปิดดู" (เช่นผู้จัดการดูรวมทั้ง
    division) จึงไม่ควรทำให้เกิดขึ้น — เปิดดูแล้วไปแก้ข้อมูลของคนอื่นโดยไม่รู้ตัว
    ไฟล์จะถูกซ่อมตอนที่คนซึ่งกระจายได้จริงเปิดหน้ารวมภาค ซึ่งเป็นตอนที่ต้องใช้พอดี
    """
    ids = sorted({str(x).strip().upper() for x in sup_ids if str(x).strip()})
    if not ids:
        raise HTTPException(400, detail="ไม่มีรหัส Supervisor สำหรับโหลดแบบรวม")

    def _load_one(sid: str) -> tuple[str, dict[str, Any] | None, str]:
        """คืน (sup_id, payload, detail) — ไม่โยน exception ออกมา เพื่อให้ทีมอื่นโหลดต่อได้"""
        try:
            p = load_employees_payload(
                sid, target_month, target_year, refresh=refresh
            )
            p["_source_sup_id"] = sid
            return sid, p, ""
        except HTTPException as ex:
            return sid, None, str(ex.detail)
        except Exception as ex:
            return sid, None, str(ex)

    # โหลดขนานกัน: แต่ละซุปยิง DAX แยกกันและไม่แชร์ state
    # (FabricDAXConnector ถูกสร้างใหม่ต่อการเรียก และ path ไฟล์ cache แยกตามซุป)
    # ตั้ง AGGREGATE_LOAD_WORKERS=1 เพื่อกลับไปโหลดทีละทีมแบบเดิมได้ถ้าต้องไล่ปัญหา
    workers = _aggregate_load_workers(len(ids))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_load_one, ids))
    else:
        results = [_load_one(sid) for sid in ids]

    payloads: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for sid, payload, detail in results:
        if payload is None:
            skipped.append({"sup_id": sid, "detail": detail})
            logger.warning("bulk skip %s: %s", sid, detail)
        else:
            payloads.append(payload)

    if not payloads:
        raise HTTPException(
            404,
            detail=f"ไม่สามารถโหลดข้อมูลจาก Supervisor ที่เลือก ({len(skipped)} รายการล้มเหลว)",
        )

    # ราคาต้องตรงกันทุกทีมก่อนบวกรวม ไม่งั้นเป้าเงินรายคนถูกดันผิดทั้งภาค
    price_report: list[dict[str, str]] = []
    if can_write:
        try:
            price_report = reconcile_prices_across_payloads(
                payloads, target_month, target_year
            )
        except Exception as e:                  # ซ่อมไม่ได้ก็ยังต้องเปิดหน้าได้
            logger.warning("ปรับราคาให้ตรงกันก่อนรวมภาคไม่สำเร็จ: %s", e)
            price_report = []

    merged = merge_employees_payloads(
        payloads,
        aggregate_label=aggregate_label,
        aggregate_sup_ids=[p["_source_sup_id"] for p in payloads],
        price_report=price_report,
    )
    merged["skipped_supervisors"] = skipped
    return merged


def load_live_targets_payload(
    sup_id: str,
    target_month: int,
    target_year: int,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    """ดึงเฉพาะเป้าหีบล่าสุดจาก Target Sun — สำหรับ refresh ใน Step 3"""
    if not targetsun_read.is_enabled():
        raise HTTPException(
            status_code=400,
            detail="ยังไม่ได้เปิด Target Sun Read API (TARGETSUN_READ_ENABLED)",
        )

    sid = str(sup_id or "").strip().upper()
    if not refresh:
        cached = targetsun_read.read_live_cache(sid, target_month, target_year)
        if cached is not None:
            if not cached.get("allocations_preview"):
                emp_ids = [
                    str(e.get("emp_id") or "").strip()
                    for e in (cached.get("employees") or [])
                    if str(e.get("emp_id") or "").strip()
                ]
                cached = dict(cached)
                cached["allocations_preview"] = _preview_from_grain_cache(
                    sid,
                    target_month,
                    target_year,
                    cached.get("skus") or [],
                    emp_ids,
                )
            return cached

    cached_emp = read_cached_employee_payload(sid, target_month, target_year)
    emp_list: list[str] = []
    if cached_emp and cached_emp.get("employees"):
        emp_list = sorted(
            {
                str(e.get("emp_id") or "").strip()
                for e in cached_emp["employees"]
                if str(e.get("emp_id") or "").strip()
            }
        )

    if not emp_list:
        emp_path = emp_cache_path(sid, target_month, target_year)
        if os.path.exists(emp_path):
            try:
                df_cached = pd.read_csv(emp_path, dtype={"emp_id": str})
                emp_list = df_cached["emp_id"].astype(str).str.strip().tolist()
            except Exception as e:
                logger.warning("read emp cache for live targets: %s", e)

    if not emp_list:
        raise HTTPException(
            status_code=404,
            detail="ยังไม่มีรายชื่อพนักงาน — โหลด Dashboard ก่อน",
        )

    fabric = None
    try:
        fabric = FabricDAXConnector()
    except Exception as e:
        logger.warning("Fabric connector for live targets scope: %s", e)

    df_granular = targetsun_read.granular_df_for_team(
        emp_list,
        target_month,
        target_year,
        sup_id=sid,
        fabric=fabric,
    )

    grain_cols = [
        "emp_id",
        "sku",
        "qty",
        "salestype",
        "divisioncode",
        "areacode",
        "provincecode",
        "warehouse_code",
    ]
    try:
        p_grain = tga_grain_cache_path(sid, target_month, target_year)
        if df_granular is None or df_granular.empty:
            pd.DataFrame(columns=grain_cols).to_csv(p_grain, index=False)
        else:
            df_granular.to_csv(p_grain, index=False)
    except Exception as e:
        logger.warning("live targets grain cache write: %s", e)

    if df_granular is not None and not df_granular.empty:
        df_tga = (
            df_granular.groupby(["emp_id", "sku"], as_index=False)["qty"].sum()
        )
        df_tga = df_tga[df_tga["qty"] != 0]
    else:
        df_tga = pd.DataFrame(columns=["emp_id", "sku", "qty"])

    sku_union = (
        df_tga["sku"].dropna().astype(str).str.strip().unique().tolist()
        if not df_tga.empty
        else []
    )

    df_product = pd.DataFrame()
    price_latest: dict[str, float] = {}
    if cached_emp and cached_emp.get("skus"):
        df_product = pd.DataFrame(cached_emp["skus"])
        if not df_product.empty and "sku" in df_product.columns:
            df_product = df_product.copy()
            df_product["sku"] = df_product["sku"].astype(str).str.strip()
            price_latest = dict(
                zip(
                    df_product["sku"].astype(str),
                    pd.to_numeric(df_product["price_per_box"], errors="coerce").fillna(
                        0.0
                    ),
                )
            )
    if sku_union and df_product.empty:
        df_product = pd.DataFrame({"sku": sku_union})

    df_sku, df_sun, emp_with_tga = _build_sku_and_sun_from_tga(
        df_tga,
        df_product,
        emp_list,
        list(sku_union),
        price_latest_by_sku=price_latest,
    )

    sun_map = {
        str(r["emp_id"]).strip(): float(r.get("target_sun") or 0)
        for r in df_sun.to_dict(orient="records")
    }
    employees_out = [
        {
            "emp_id": eid,
            "target_sun": sun_map.get(eid, 0.0),
            "has_tga_rows": eid in emp_with_tga,
        }
        for eid in emp_list
    ]

    allocations_preview = _build_allocations_preview_from_grain(
        df_granular,
        df_sku,
        emp_list,
    )

    payload: dict[str, Any] = {
        "source": "targetsun",
        "sup_id": sid,
        "target_year": int(target_year),
        "target_month": int(target_month),
        "row_count": int(len(df_granular)),
        "tga_grain_rows": int(len(df_granular)),
        "skus": _clean(df_sku),
        "employees": employees_out,
        "allocations_preview": allocations_preview,
    }
    targetsun_read.write_live_cache(sid, target_month, target_year, payload)
    return payload

