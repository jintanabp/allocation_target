"""
เป้าตั้งต้นของงวด — สำเนาชุดแรกที่ระบบดึงมา ไว้กันเป้าหาย/ถูกทับ

ทำไมต้องมี: เป้าหีบของงวดมีอยู่ที่เดียวคือ `data/target_boxes_{SL}_{ปี}_{เดือน}.csv`
ซึ่งถูก **เขียนทับ** ทุกครั้งที่โหลดขั้นที่ 1 ใหม่ และไม่มีสำเนาเก่าเก็บไว้เลย
ถ้าเป้าต้นทางเปลี่ยนไป (หรือดึงมาได้ไม่ครบ) จะไม่มีอะไรให้เทียบว่าเดิมเป็นเท่าไร
และไม่มีทางกู้กลับ — ผู้ใช้ต้องไปไล่หาเอาเองใน Target Sun

หลักการ:
  - เก็บตอนงวดนั้นถูกเปิดครั้งแรก · **ก่อนระบบเราส่งงวดนั้นครั้งแรก** ถ้าเป้าใน Target Sun เปลี่ยน
    (HQ ลงเป้าทีละส่วนช่วงวันที่ 15) ระบบเก็บชุดใหม่เป็น "รุ่นถัดไป" และเก็บรุ่นเก่าไว้ใน history/
    (OPEN_ITEMS 10.4 — ผู้ใช้เลือก 12 ต.ค. 2026: เดิมจำชุดแรกที่อาจลงไม่ครบไว้ตลอด)
    ช่วงนั้นค่าใน Target Sun ยังเป็นของ HQ ล้วน ๆ จึงเชื่อได้
  - **ส่งครั้งแรกแล้ว = ล็อก ไม่แตะอีกเลย** (หลังส่ง ค่าใน Target Sun มีค่าที่เราส่งปนอยู่)
  - รุ่นเก่าไม่ลบ — ดูย้อนได้ว่า HQ ลงเป้ากี่รอบ แต่ละรอบเท่าไร
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
    เก็บเป้าตั้งต้น — คืน True เมื่อเพิ่งเขียนไฟล์ (ครั้งแรก หรือรุ่นใหม่ก่อนส่งครั้งแรก)

    ยังไม่มี = เก็บ · มีแล้วและยังไม่เคยส่งงวดนี้ + เป้าเปลี่ยน = เก็บรุ่นใหม่ (รุ่นเก่าไป history/)
    ส่งแล้ว = ห้ามเขียนทับเด็ดขาด นั่นคือคุณค่าทั้งหมดของไฟล์นี้
    ล้มเหลวแล้วไม่ throw — การเก็บหลักฐานต้องไม่ทำให้การโหลดหน้าจอพัง
    """
    try:
        path = target_baseline_path(sup_id, month, year)
        skus = _sku_rows(df_sku)
        emps = _emp_rows(df_sun)
        if not skus and not emps:
            return False   # ไม่มีอะไรให้เก็บ อย่าสร้างไฟล์เปล่าไว้กันการเก็บครั้งหน้า
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # "เช็ค → เขียน" ต้องอยู่ใต้ล็อกเดียวกัน — เปิดงวดใหม่พร้อมกันสองแท็บ ทั้งคู่ผ่านด่านได้
        # แล้วคนที่สองเขียนทับของคนแรกเงียบ ๆ · ล็อกเป็น RLock ต่อ path ตัวเดียวกับที่
        # atomic_write_json ใช้ จึงซ้อนกันได้ไม่ deadlock (ใช้ได้เฉพาะ 1 worker เหมือนทั้งโมดูล)
        with read_locked(path):
            if not os.path.isfile(path):
                return _write_baseline(path, sup_id, month, year, skus, emps, captured_by)
            return _maybe_revise_before_first_send(path, sup_id, month, year, skus, emps, captured_by)
    except Exception as e:
        logger.warning("เก็บเป้าตั้งต้นไม่สำเร็จ (%s %s-%02d): %s", sup_id, year, month, e)
        return False


def _target_signature(skus, emps) -> tuple:
    """
    สิ่งที่ถือว่า "เป้าเปลี่ยน" — หีบราย SKU + รายชื่อพนักงาน
    ไม่นับเป้าเงิน: target_sun = หีบ × ราคา จึงขยับตามแหล่งราคา (Fabric ล่ม → ราคาแคชเก่า/0) ไม่ใช่ HQ เปลี่ยนเป้า
    (ตรวจซ้ำ 12 ต.ค. 2026 — เดิมนับเป้าเงินด้วย ราคากระพริบแล้วเก็บรุ่นใหม่วนไปมา หรือเก็บเป้าเงิน 0 แล้วล็อก)
    """
    return (
        tuple((x["sku"], int(x["supervisor_target_boxes"])) for x in skus or []),
        tuple(sorted(str(x["emp_id"]) for x in emps or [])),
    )


def baseline_history_dir() -> str:
    return os.path.join("data", "baselines", "history")


def _maybe_revise_before_first_send(path, sup_id, month, year, skus, emps, captured_by) -> bool:
    """
    มีเป้าตั้งต้นอยู่แล้ว — เก็บรุ่นใหม่เฉพาะเมื่อ (1) ระบบเรายังไม่เคยส่งงวดนี้ และ (2) เป้าเปลี่ยนจริง
    รุ่นเดิมย้ายไป history/ ไม่ลบ · คืน True เมื่อเขียนรุ่นใหม่
    """
    import json

    with open(path, encoding="utf-8") as f:
        cur = json.load(f)
    if cur.get("captured_after_send") or _period_maybe_sent(sup_id, month, year):
        return False  # ส่งไปแล้ว (หรืออาจส่งแล้ว) = ล็อก
    old_skus = cur.get("skus") or []
    old_emps = cur.get("employees") or []
    if _target_signature(old_skus, old_emps) == _target_signature(skus, emps):
        return False
    # ราคาดึงไม่ได้ตอนนี้ (เป้าเงินรวม 0 ทั้งที่มีหีบ) — อย่าเอาชุดนี้มาแทน รอโหลดรอบหน้าที่ราคาครบ
    if sum(x["supervisor_target_boxes"] for x in skus) > 0 and sum(float(x["target_sun"]) for x in emps) <= 0:
        logger.warning("ไม่ปรับเป้าตั้งต้น %s %s-%02d: เป้าเงินรวมเป็น 0 (ราคายังดึงไม่ได้)", sup_id, year, month)
        return False
    rev = int(cur.get("revision") or 1)
    hist_dir = baseline_history_dir()
    os.makedirs(hist_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(path))[0]
    hist_path = os.path.join(hist_dir, f"{base_name}_r{rev}.json")
    n = 1
    while os.path.exists(hist_path):  # ห้ามทับประวัติ
        n += 1
        hist_path = os.path.join(hist_dir, f"{base_name}_r{rev}_{n}.json")
    atomic_write_json(hist_path, cur, indent=2)
    previous = list(cur.get("previous_revisions") or []) + [{
        "revision": rev,
        "captured_at": cur.get("captured_at"),
        "captured_by": cur.get("captured_by"),
        "total_target_boxes": cur.get("total_target_boxes"),
        "total_target_sun": cur.get("total_target_sun"),
        "sku_count": len(old_skus),
        "file": os.path.basename(hist_path),
    }]
    logger.info(
        "ปรับเป้าตั้งต้น %s %s-%02d เป็นรุ่น %d (ยังไม่เคยส่ง · เป้าใน Target Sun เปลี่ยน): หีบ %s -> %d",
        sup_id, year, month, rev + 1, cur.get("total_target_boxes"), sum(x["supervisor_target_boxes"] for x in skus),
    )
    wrote = _write_baseline(path, sup_id, month, year, skus, emps, captured_by,
                            revision=rev + 1, previous_revisions=previous)
    # หน้า log ของแอดมินต้องเห็นว่าเป้าใน Target Sun เปลี่ยนก่อนส่ง (เดิมขึ้นเป็น "เป้าต่างจากตั้งต้น")
    try:
        from .usage_log_store import append_log

        append_log(
            level="info", email="", role="system", sup_id=str(sup_id or "").strip().upper(),
            action="target_baseline_revised",
            message=(
                f"เป้าใน Target Sun เปลี่ยนก่อนส่งครั้งแรก — เก็บเป้าตั้งต้นรุ่น {rev + 1} "
                f"(หีบ {cur.get('total_target_boxes')} → {sum(x['supervisor_target_boxes'] for x in skus)})"
            ),
            detail=f"รุ่นเดิมเก็บไว้ที่ history/{os.path.basename(hist_path)}",
            target_month=int(month), target_year=int(year),
        )
    except Exception as e:
        logger.warning("จด log การปรับเป้าตั้งต้นไม่สำเร็จ: %s", e)
    return wrote


def _period_maybe_sent(sup_id, month, year) -> bool:
    """
    ทีม×งวดนี้ "อาจส่งแล้ว" ไหม — ใช้ล็อกเป้าตั้งต้น ต้องเอียงไปทางล็อก (ตรวจซ้ำ 12 ต.ค. 2026):
    ledger มีการส่ง · ไฟล์ ledger มีแต่อ่านไม่ได้ (antivirus ถือไฟล์) · มีสำเนาแถวก่อนส่ง (เขียนตอนกดส่งเสมอ)
    """
    if _ledger_has_sends(sup_id, month, year):
        return True
    try:
        from . import sent_ledger
        from .ts_row_snapshots import first_snapshot_path, presend_dir

        lp = sent_ledger.ledger_path(sup_id, month, year)
        if os.path.isfile(lp) and sent_ledger.read_ledger(sup_id, month, year) is None:
            return True
        if os.path.isfile(first_snapshot_path(sup_id, month, year)):
            return True
        d = presend_dir(sup_id, month, year)
        if os.path.isdir(d) and any(n.endswith(".json") for n in os.listdir(d)):
            return True
    except Exception as e:
        logger.warning("ตรวจว่าเคยส่งงวดนี้ไหมไม่ได้ (%s %s-%02d) — ถือว่าอาจส่งแล้ว: %s", sup_id, year, month, e)
        return True
    return False


def _ledger_has_sends(sup_id, month, year) -> bool:
    """ระบบเราเคยส่งงวดนี้ของทีมนี้แล้วหรือยัง — ถ้าเคย ค่าใน Target Sun ตอนนี้มีค่าที่เราส่งปนอยู่"""
    try:
        from . import sent_ledger

        return bool((sent_ledger.read_ledger(sup_id, month, year) or {}).get("sends"))
    except Exception:
        return False


def _write_baseline(path, sup_id, month, year, skus, emps, captured_by, *, revision: int = 1,
                    previous_revisions: list | None = None) -> bool:
    after_send = _ledger_has_sends(sup_id, month, year)
    if after_send:
        # ผลตรวจ 7 ต.ค. 2026 ก5/A1: เปิดงวดครั้งแรกหลังเราส่งไปแล้ว (เช่น data/ หายตอน deploy หรือ
        # ทีมนี้ไม่เคยเปิดผ่านแอปก่อนส่งรวมภาค) — ยังเก็บไว้ แต่ติดธงให้แอดมินรู้ว่าไม่ใช่ของตั้งต้นจริง
        logger.warning("เป้าตั้งต้น %s %s-%02d ถูกเก็บหลังเคยส่งแล้ว — ติดธง captured_after_send", sup_id, year, month)
    doc = {
        "sup_id": str(sup_id or "").strip().upper(),
        "target_month": int(month),
        "target_year": int(year),
        "captured_at": _now_iso(),
        "captured_by": str(captured_by or "").strip(),
        "captured_after_send": after_send,
        "total_target_boxes": sum(s["supervisor_target_boxes"] for s in skus),
        "total_target_sun": round(sum(e["target_sun"] for e in emps), 2),
        "skus": skus,
        "employees": emps,
        # รุ่นของเป้าตั้งต้น — ปรับได้จนกว่าจะส่งงวดนี้ครั้งแรก (OPEN_ITEMS 10.4) · รุ่นเก่าอยู่ใน history/
        "revision": int(revision),
        "previous_revisions": list(previous_revisions or []),
    }
    atomic_write_json(path, doc, indent=2)
    logger.info(
        "เก็บเป้าตั้งต้น %s %s-%02d: %d SKU (%d หีบ), %d คน",
        sup_id, year, month, len(skus), doc["total_target_boxes"], len(emps),
    )
    return True


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

    if not emps:
        # เป้าหีบกับเป้าเงินต้องมาจากรอบเดียวกัน (ดู docstring) — เดิมเขียนแค่ target_boxes_ แล้วข้าม
        # target_sun_ เงียบ ๆ ได้เป้าคนละรอบ (ผลตรวจ §4.3) · ไม่กู้ครึ่งเดียว
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail="เป้าตั้งต้นที่เก็บไว้ไม่มีเป้าเงินรายคน — กู้คืนไม่ได้ (เป้าหีบกับเป้าเงินต้องมาจากรอบเดียวกัน)",
        )

    # ชื่อแบรนด์/สินค้าไม่ได้เก็บใน baseline — เอาจากไฟล์เป้าปัจจุบัน (ผลตรวจ §4.3: เดิมเขียนเป็นว่าง
    # แท็บแบรนด์และชื่อสินค้าหายหลังกู้คืน)
    from ..core.targets import _read_sku_csv

    names: dict[str, dict] = {}
    cur = _read_sku_csv(target_boxes_cache_path(sup_id, month, year))
    if cur is not None and not cur.empty:
        name_cols = [c for c in ("brand_name_thai", "brand_name_english", "section",
                                 "product_name_thai", "product_name_english") if c in cur.columns]
        for _, r in cur.iterrows():
            names[str(r.get("sku") or "").strip()] = {c: str(r.get(c) or "") for c in name_cols}

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
            **names.get(str(s["sku"]).strip(), {}),
        }
        for s in skus
    ])
    df_sun = pd.DataFrame([
        {"emp_id": e["emp_id"], "target_sun": float(e.get("target_sun") or 0.0)}
        for e in emps
    ])

    atomic_write_csv(target_boxes_cache_path(sup_id, month, year), df_sku, index=False)
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
