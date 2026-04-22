import json
import logging
import os

from ..fabric_dax_connector import FabricDAXConnector

logger = logging.getLogger("target_allocation")

MANAGERS_CACHE_FILE = "data/managers_cache.json"


def build_managers_payload_from_trf_rows(rows: list[dict]) -> dict:
    """สร้าง payload สำหรับหน้า login: Supervisor + Manager (DEPENDON) และ map manager → supervisors"""
    by_manager: dict[str, list[str]] = {}
    supervisors: set[str] = set()
    managers_set: set[str] = set()
    for r in rows:
        sc = str(r.get("supervisor_code") or "").strip().upper()
        dep = str(r.get("depend_on") or "").strip().upper()
        if sc:
            supervisors.add(sc)
        if dep and dep not in ("NONE", "0", "(BLANK)"):
            managers_set.add(dep)
            by_manager.setdefault(dep, [])
            if sc and sc not in by_manager[dep]:
                by_manager[dep].append(sc)
    for k in list(by_manager.keys()):
        by_manager[k] = sorted(by_manager[k])
    pick_labels: list[str] = []
    for c in sorted(supervisors):
        pick_labels.append(f"{c} (Supervisor)")
    for c in sorted(managers_set):
        pick_labels.append(f"{c} (Manager)")
    return {
        "rows": rows,
        "by_manager": by_manager,
        "supervisors": sorted(supervisors),
        "manager_codes": sorted(managers_set),
        "managers": pick_labels,
        "source": "trf_select_supervisor",
    }


def try_fetch_managers_from_fabric() -> dict | None:
    """ดึง trf_select_supervisor จาก Fabric — ไม่เขียนไฟล์ cache"""
    try:
        fabric = FabricDAXConnector()
        rows = fabric.get_trf_select_supervisor_rows()
        if rows:
            return build_managers_payload_from_trf_rows(rows)
    except Exception as e:
        logger.warning("get_trf_select_supervisor_rows error: %s", e)
    return None


def persist_managers_payload(payload: dict) -> None:
    os.makedirs("data", exist_ok=True)
    with open(MANAGERS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def warm_managers_cache_at_startup() -> None:
    """
    Preload รายชื่อ Supervisor/Manager ตอน uvicorn startup (ใช้ Service Principal กับ Fabric)
    เพื่อให้หลังล็อกอิน Microsoft แล้ว GET /managers อ่านจาก cache ได้ทันที (เมื่อยังอยู่ใน TTL)
    """
    payload = try_fetch_managers_from_fabric()
    if not payload:
        return
    try:
        persist_managers_payload(payload)
        logger.info(
            "managers cache warmed at startup: %d rows (trf_select_supervisor)",
            len(payload.get("rows") or []),
        )
    except Exception as e:
        logger.warning("managers cache persist at startup failed: %s", e)

