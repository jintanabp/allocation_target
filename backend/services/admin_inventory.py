"""Admin — สรุปแหล่งข้อมูล / cache / outbound (ไม่ส่ง secret)"""

from __future__ import annotations

import glob
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from .access_hierarchy import access_hierarchy_json_path, load_hierarchy_payload, parse_hierarchy_metadata
from .user_access_store import read_rows

logger = logging.getLogger("target_allocation")

FABRIC_TABLES_RUNTIME = [
    "Dim_Salesman",
    "Dim_Super",
    "Dim_Product",
    "DimDate",
    "cross_sold_history_2y_qu",
    "cfm_produc_master",
    "cfm_product_characteristic",
    "tga_target_salesman_next",
]

FABRIC_TABLES_DEPRECATED = [
    "trf_select_supervisor",
    "ACC_USER_CONTROL",
    "acc_extra_user",
]

API_MAP = [
    {"endpoint": "GET /managers", "fabric": False, "sources": ["access_hierarchy.json", "managers_cache.json"]},
    {"endpoint": "GET /data/employees", "fabric": True, "sources": ["Dim_Salesman", "tga_target_salesman_next", "Dim_Product", "cross_sold_history_2y_qu", "cfm_*"]},
    {"endpoint": "GET /data/employees/aggregate", "fabric": True, "sources": ["เหมือน /data/employees"]},
    {"endpoint": "POST /optimize", "fabric": True, "sources": ["tga_target_salesman_next (period check)"]},
    {"endpoint": "POST /lakehouse/export-csv", "fabric": True, "sources": ["tga_target_salesman_next", "cache tga_lines_*"]},
    {"endpoint": "POST /lakehouse/import-targetsun", "fabric": True, "sources": ["tga grain + dims"]},
    {"endpoint": "POST /lakehouse/upload", "fabric": True, "sources": ["OneLake ADLS"]},
    {"endpoint": "GET /admin/supervisor-team", "fabric": True, "sources": ["Dim_Salesman", "emp_cache_*"]},
    {"endpoint": "GET /admin/user-access", "fabric": False, "sources": ["user_access.json"]},
]

_CACHE_PATTERNS = [
    "emp_cache_*.csv",
    "payload_cache_*.json",
    "tga_lines_*.csv",
    "hist_cache_*.csv",
    "hist_lysm_*.csv",
    "hist_prev_*.csv",
    "hist_cy_*.csv",
    "final_allocation_*.csv",
    "export_*.csv",
]


def _file_summary(pattern: str) -> dict[str, Any]:
    paths = sorted(glob.glob(os.path.join("data", pattern)))
    latest_mtime: float | None = None
    latest_name = ""
    for p in paths:
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        if latest_mtime is None or mt > latest_mtime:
            latest_mtime = mt
            latest_name = os.path.basename(p)
    latest_iso = (
        datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat()
        if latest_mtime is not None
        else None
    )
    return {"pattern": pattern, "count": len(paths), "latest_file": latest_name or None, "latest_mtime": latest_iso}


def _fabric_connection_status() -> dict[str, Any]:
    import requests

    from ..fabric_dax_connector import FabricDAXConnector

    out: dict[str, Any] = {
        "dataset_id": (os.environ.get("FABRIC_DATASET_ID") or "").strip(),
        "workspace_id": (os.environ.get("FABRIC_WORKSPACE_ID") or "").strip(),
        "client_id_prefix": "",
        "service_principal": bool((os.environ.get("FABRIC_CLIENT_SECRET") or "").strip()),
        "ok": False,
        "http_status": None,
        "content_provider_type": None,
        "error": None,
    }
    cid = (os.environ.get("FABRIC_CLIENT_ID") or "").strip()
    if cid:
        out["client_id_prefix"] = cid[:8] + "…" if len(cid) > 8 else cid
    if not out["dataset_id"]:
        out["error"] = "ไม่ได้ตั้ง FABRIC_DATASET_ID"
        return out
    try:
        fabric = FabricDAXConnector()
        token = fabric._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        base = "https://api.powerbi.com/v1.0/myorg"
        if fabric.workspace_id:
            url = f"{base}/groups/{fabric.workspace_id}/datasets/{fabric.dataset_id}"
        else:
            url = f"{base}/datasets/{fabric.dataset_id}"
        r = requests.get(url, headers=headers, timeout=30)
        out["http_status"] = r.status_code
        out["ok"] = r.status_code == 200
        if r.status_code == 200:
            meta = r.json()
            out["content_provider_type"] = meta.get("contentProviderType")
            out["upstream_datasets_count"] = len(meta.get("upstreamDatasets") or [])
        else:
            out["error"] = (r.text or "")[:300]
    except Exception as e:
        out["error"] = str(e)
    return out


def build_data_inventory(*, check_fabric: bool = True) -> dict[str, Any]:
    rows = read_rows()
    mdata = load_hierarchy_payload()
    supervisors, manager_codes, _by_m = parse_hierarchy_metadata(mdata)

    managers_cache = "data/managers_cache.json"
    managers_mtime = None
    if os.path.isfile(managers_cache):
        try:
            managers_mtime = datetime.fromtimestamp(
                os.path.getmtime(managers_cache), tz=timezone.utc
            ).isoformat()
        except OSError:
            pass

    hierarchy_path = access_hierarchy_json_path()
    hierarchy_mtime = None
    if os.path.isfile(hierarchy_path):
        try:
            hierarchy_mtime = datetime.fromtimestamp(
                os.path.getmtime(hierarchy_path), tz=timezone.utc
            ).isoformat()
        except OSError:
            pass

    targetsun_url = (
        os.environ.get("TARGETSUN_IMPORT_EXCEL_URL") or ""
    ).strip() or "https://spcuatws.sahapat.com/spc/targetsun/importTargetSalesmanNextFromExcel"

    onelake_ok = bool(
        (os.environ.get("ONELAKE_WORKSPACE_ID") or "").strip()
        and (os.environ.get("ONELAKE_LAKEHOUSE_ID") or "").strip()
    )

    fabric_block: dict[str, Any] = {
        "tables_runtime": FABRIC_TABLES_RUNTIME,
        "tables_deprecated": FABRIC_TABLES_DEPRECATED,
    }
    if check_fabric:
        fabric_block["connection"] = _fabric_connection_status()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fabric": fabric_block,
        "local_config": {
            "user_access_rows": len(rows),
            "user_access_path": os.environ.get("USER_ACCESS_JSON_PATH") or "config/user_access.json",
            "access_hierarchy_supervisors": len(supervisors),
            "access_hierarchy_managers": len(manager_codes),
            "access_hierarchy_path": hierarchy_path,
            "access_hierarchy_mtime": hierarchy_mtime,
            "managers_cache_mtime": managers_mtime,
        },
        "data_dir": {
            "patterns": [_file_summary(p) for p in _CACHE_PATTERNS],
            "app_log": os.path.isfile("data/app.log"),
        },
        "outbound": {
            "targetsun_url": targetsun_url,
            "targetsun_configured": bool(targetsun_url),
            "onelake_configured": onelake_ok,
            "onelake_upload_dir": (os.environ.get("ONELAKE_UPLOAD_DIR") or "").strip() or None,
        },
        "api_map": API_MAP,
        "docs_path": "docs/DATA_FLOW.md",
    }


def inventory_json_safe(payload: dict[str, Any]) -> str:
    """สำหรับทดสอบ — ยืนยันว่าไม่มี secret ใน JSON"""
    text = json.dumps(payload, ensure_ascii=False)
    forbidden = ["FABRIC_CLIENT_SECRET", "access_token", "client_credential"]
    for token in forbidden:
        if token in text:
            raise ValueError(f"inventory leaked forbidden token: {token}")
    return text
