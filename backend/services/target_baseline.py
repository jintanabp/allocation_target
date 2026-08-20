"""
เป้าตั้งต้นของงวด — สำเนาชุดแรกที่ระบบดึงมา ไว้กันเป้าหาย/ถูกทับ

ทำไมต้องมี: เป้าหีบของงวดมีอยู่ที่เดียวคือ `data/target_boxes_{SL}_{ปี}_{เดือน}.csv`
ซึ่งถูก **เขียนทับ** ทุกครั้งที่โหลดขั้นที่ 1 ใหม่ และไม่มีสำเนาเก่าเก็บไว้เลย
ถ้าเป้าต้นทางเปลี่ยนไป (หรือดึงมาได้ไม่ครบ) จะไม่มีอะไรให้เทียบว่าเดิมเป็นเท่าไร
และไม่มีทางกู้กลับ — ผู้ใช้ต้องไปไล่หาเอาเองใน Target Sun

หลักการ:
  - **เขียนครั้งเดียว** ตอนงวดนั้นถูกเปิดครั้งแรก แล้วไม่แตะอีกเลย
    (ถ้าเขียนทับเรื่อย ๆ ก็จะกลายเป็นสำเนาของค่าล่าสุด = ไม่มีประโยชน์)
  - เก็บทั้งเป้าหีบราย SKU และเป้าเงินราย emp เพราะทั้งคู่หายได้พอกัน
  - ไม่มีอะไรในระบบลบไฟล์นี้อัตโนมัติ — ตัวล้าง cache ตามอายุวนเฉพาะไฟล์ชั้นบนใน data/
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from ..core.atomic_io import atomic_write_json, read_locked
from ..core.paths import target_baseline_path

logger = logging.getLogger("target_allocation")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sku_rows(df_sku: pd.DataFrame | None) -> list[dict[str, Any]]:
    if df_sku is None or df_sku.empty:
        return []
    out: list[dict[str, Any]] = []
    for _, r in df_sku.iterrows():
        sku = str(r.get("sku") or "").strip()
        if not sku:
            continue
        try:
            boxes = int(round(float(r.get("supervisor_target_boxes") or 0)))
        except (TypeError, ValueError):
            boxes = 0
        try:
            price = round(float(r.get("price_per_box") or 0.0), 2)
        except (TypeError, ValueError):
            price = 0.0
        out.append({"sku": sku, "supervisor_target_boxes": boxes, "price_per_box": price})
    out.sort(key=lambda x: x["sku"])
    return out


def _emp_rows(df_sun: pd.DataFrame | None) -> list[dict[str, Any]]:
    if df_sun is None or df_sun.empty:
        return []
    out: list[dict[str, Any]] = []
    for _, r in df_sun.iterrows():
        emp = str(r.get("emp_id") or "").strip()
        if not emp:
            continue
        try:
            ts = round(float(r.get("target_sun") or 0.0), 2)
        except (TypeError, ValueError):
            ts = 0.0
        out.append({"emp_id": emp, "target_sun": ts})
    out.sort(key=lambda x: x["emp_id"])
    return out


def baseline_exists(sup_id: str, month: int, year: int) -> bool:
    return os.path.isfile(target_baseline_path(sup_id, month, year))


def read_baseline(sup_id: str, month: int, year: int) -> dict[str, Any] | None:
    path = target_baseline_path(sup_id, month, year)
    if not os.path.isfile(path):
        return None
    try:
        import json

        # read_locked เป็นแค่ตัวกันชนเวลาอ่าน/เขียนชนกัน (ไม่ได้ yield ตัวไฟล์)
        with read_locked(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning("อ่าน baseline %s ไม่ได้: %s", path, e)
        return None


def capture_baseline_once(
    sup_id: str,
    month: int,
    year: int,
    df_sku: pd.DataFrame | None,
    df_sun: pd.DataFrame | None,
    *,
    captured_by: str | None = None,
) -> bool:
    """
    เก็บเป้าตั้งต้นถ้ายังไม่เคยเก็บ — คืน True เมื่อเพิ่งเขียนไฟล์

    ห้ามเขียนทับของเดิมเด็ดขาด นั่นคือคุณค่าทั้งหมดของไฟล์นี้
    ล้มเหลวแล้วไม่ throw — การเก็บหลักฐานต้องไม่ทำให้การโหลดหน้าจอพัง
    """
    try:
        path = target_baseline_path(sup_id, month, year)
        if os.path.isfile(path):
            return False
        skus = _sku_rows(df_sku)
        emps = _emp_rows(df_sun)
        if not skus and not emps:
            return False   # ไม่มีอะไรให้เก็บ อย่าสร้างไฟล์เปล่าไว้กันการเก็บครั้งหน้า
        os.makedirs(os.path.dirname(path), exist_ok=True)
        doc = {
            "sup_id": str(sup_id or "").strip().upper(),
            "target_month": int(month),
            "target_year": int(year),
            "captured_at": _now_iso(),
            "captured_by": str(captured_by or "").strip(),
            "total_target_boxes": sum(s["supervisor_target_boxes"] for s in skus),
            "total_target_sun": round(sum(e["target_sun"] for e in emps), 2),
            "skus": skus,
            "employees": emps,
        }
        atomic_write_json(path, doc, indent=2)
        logger.info(
            "เก็บเป้าตั้งต้น %s %s-%02d: %d SKU (%d หีบ), %d คน",
            sup_id, year, month, len(skus), doc["total_target_boxes"], len(emps),
        )
        return True
    except Exception as e:
        logger.warning("เก็บเป้าตั้งต้นไม่สำเร็จ (%s %s-%02d): %s", sup_id, year, month, e)
        return False


def restore_baseline_to_target_files(sup_id: str, month: int, year: int) -> dict[str, Any]:
    """
    เขียนเป้าตั้งต้นกลับทับไฟล์เป้าปัจจุบัน — ใช้ตอนเป้าถูกทับจนใช้ไม่ได้

    เขียนทั้ง target_boxes_ และ target_sun_ พร้อมกัน เพราะสองไฟล์ต้องมาจากรอบเดียวกัน
    (คนละรอบ = เป้าหีบกับเป้าเงินคนละสเกล แล้วตัวปรับสเกลรายได้จะเพี้ยนทั้งชุด)

    **ไม่แตะไฟล์ baseline เอง** และไม่แตะ snapshot ผลกระจาย — คืนแค่เป้า
    ผู้ใช้ต้องกดกระจายใหม่เองถ้าต้องการผลที่ตรงกับเป้าที่กู้มา
    """
    from ..core.atomic_io import atomic_write_csv
    from ..core.paths import target_boxes_cache_path, target_sun_cache_path

    base = read_baseline(sup_id, month, year)
    if not base:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="ยังไม่มีเป้าตั้งต้นของงวดนี้ให้กู้คืน")

    skus = base.get("skus") or []
    emps = base.get("employees") or []
    if not skus:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail="เป้าตั้งต้นที่เก็บไว้ไม่มีรายการ SKU — กู้คืนไม่ได้",
        )

    # คงคอลัมน์เดิมของไฟล์เป้าไว้ให้ครบ ตัวอ่านปลายทางคาดหวังคอลัมน์เหล่านี้
    df_sku = pd.DataFrame([
        {
            "sku": s["sku"],
            "price_per_box": float(s.get("price_per_box") or 0.0),
            "price_missing": bool(not s.get("price_per_box")),
            "price_from_sales_history": False,
            "supervisor_target_boxes": int(s.get("supervisor_target_boxes") or 0),
            "brand_name_thai": "",
            "brand_name_english": "",
            "section": "",
            "product_name_thai": "",
            "product_name_english": "",
        }
        for s in skus
    ])
    df_sun = pd.DataFrame([
        {"emp_id": e["emp_id"], "target_sun": float(e.get("target_sun") or 0.0)}
        for e in emps
    ])

    atomic_write_csv(target_boxes_cache_path(sup_id, month, year), df_sku, index=False)
    if not df_sun.empty:
        atomic_write_csv(target_sun_cache_path(sup_id, month, year), df_sun, index=False)

    total_boxes = int(df_sku["supervisor_target_boxes"].sum())
    logger.warning(
        "กู้คืนเป้าตั้งต้น %s %s-%02d: %d SKU (%d หีบ), %d คน",
        sup_id, year, month, len(df_sku), total_boxes, len(df_sun),
    )
    return {
        "sup_id": str(sup_id or "").strip().upper(),
        "target_month": int(month),
        "target_year": int(year),
        "skus": len(df_sku),
        "employees": len(df_sun),
        "total_boxes": total_boxes,
        "captured_at": base.get("captured_at"),
    }


def diff_against_baseline(
    sup_id: str,
    month: int,
    year: int,
    df_sku: pd.DataFrame | None,
    df_sun: pd.DataFrame | None,
) -> dict[str, Any] | None:
    """
    เทียบเป้าปัจจุบันกับเป้าตั้งต้น — คืน None ถ้าไม่มี baseline หรือเหมือนกันทุกอย่าง

    ใช้ตอนเป้าถูกดึงใหม่ทับของเดิม เพื่อบันทึกว่าอะไรเปลี่ยนไปเท่าไร
    (ก่อนหน้านี้ระบบไม่เคยบันทึกเรื่องนี้เลย เป้าหายแล้วไม่มีร่องรอย)
    """
    base = read_baseline(sup_id, month, year)
    if not base:
        return None

    old_sku = {s["sku"]: s for s in base.get("skus") or []}
    new_sku = {s["sku"]: s for s in _sku_rows(df_sku)}
    changed: list[dict[str, Any]] = []
    for sku in sorted(set(old_sku) | set(new_sku)):
        o = int((old_sku.get(sku) or {}).get("supervisor_target_boxes") or 0)
        n = int((new_sku.get(sku) or {}).get("supervisor_target_boxes") or 0)
        if o != n:
            changed.append({"sku": sku, "before": o, "after": n, "delta": n - o})

    old_emp = {e["emp_id"]: float(e["target_sun"]) for e in base.get("employees") or []}
    new_emp = {e["emp_id"]: float(e["target_sun"]) for e in _emp_rows(df_sun)}
    emp_changed = 0
    for emp in set(old_emp) | set(new_emp):
        if abs(old_emp.get(emp, 0.0) - new_emp.get(emp, 0.0)) > 0.01:
            emp_changed += 1

    if not changed and not emp_changed:
        return None

    old_total = int(base.get("total_target_boxes") or 0)
    new_total = sum(s["supervisor_target_boxes"] for s in new_sku.values())
    return {
        "sup_id": str(sup_id or "").strip().upper(),
        "target_month": int(month),
        "target_year": int(year),
        "captured_at": base.get("captured_at"),
        "boxes_before": old_total,
        "boxes_after": new_total,
        "boxes_delta": new_total - old_total,
        "sku_changed": len(changed),
        "emp_target_changed": emp_changed,
        "changes": changed[:50],
    }
