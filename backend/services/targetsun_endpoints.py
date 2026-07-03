"""
Target Sun / SPC API — เปลี่ยน URL default ที่ไฟล์นี้ (ไม่ใช้ config/.env)

Read กับ Send แยก host ได้ — สลับ preset จากแอดมิน (app_runtime.json) ได้
"""

from __future__ import annotations

from urllib.parse import urlparse

# Production / UAT hosts
TARGETSUN_PROD_BASE = "https://spcws.sahapat.com/spc/targetsun"
TARGETSUN_UAT_BASE = "https://spcuatws.sahapat.com/spc/targetsun"

# Default: UAT ทั้งคู่ (อ่านเป้า + ส่งผล)
TARGETSUN_READ_API_BASE = TARGETSUN_UAT_BASE
TARGETSUN_IMPORT_API_BASE = TARGETSUN_UAT_BASE

_IMPORT_PATH = "/importTargetSalesmanNextFromExcel"

_ENDPOINT_PRESETS: dict[str, tuple[str, str]] = {
    "test": (TARGETSUN_PROD_BASE, TARGETSUN_UAT_BASE),
    "uat": (TARGETSUN_UAT_BASE, TARGETSUN_UAT_BASE),
    "prod": (TARGETSUN_PROD_BASE, TARGETSUN_PROD_BASE),
}


def _strip_base(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def _code_default_bases() -> tuple[str, str]:
    return _strip_base(TARGETSUN_READ_API_BASE), _strip_base(TARGETSUN_IMPORT_API_BASE)


def _host_label(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        host = ""
    if "spcuatws" in host:
        return "UAT"
    if "spcws" in host:
        return "Prod"
    return host or url


def resolve_endpoint_bases() -> tuple[str, str]:
    """คืน (read_base, import_base) หลังใช้ preset/runtime override"""
    from .app_runtime_settings import get_target_endpoint_config

    cfg = get_target_endpoint_config()
    preset = cfg.get("preset")
    if preset and preset in _ENDPOINT_PRESETS:
        read_b, import_b = _ENDPOINT_PRESETS[preset]
    else:
        read_b, import_b = _code_default_bases()
    if cfg.get("read_base"):
        read_b = cfg["read_base"]
    if cfg.get("import_base"):
        import_b = cfg["import_base"]
    return _strip_base(read_b), _strip_base(import_b)


def targetsun_read_api_base() -> str:
    """Base สำหรับ Read API (GET targetSalesmanNext ฯลฯ)"""
    return resolve_endpoint_bases()[0]


def targetsun_import_api_base() -> str:
    """Base สำหรับ Import API (ไม่รวม path)"""
    return resolve_endpoint_bases()[1]


def targetsun_import_excel_url() -> str:
    """POST multipart Excel — importTargetSalesmanNextFromExcel"""
    return f"{targetsun_import_api_base()}{_IMPORT_PATH}"


def targetsun_endpoints_summary() -> dict[str, str]:
    read_b, import_b = resolve_endpoint_bases()
    cross = _host_label(read_b) != _host_label(import_b) and read_b != import_b
    return {
        "read_base": read_b,
        "import_base": import_b,
        "import_url": targetsun_import_excel_url(),
        "read_host_label": _host_label(read_b),
        "import_host_label": _host_label(import_b),
        "cross_env": "1" if cross else "0",
    }


def list_endpoint_presets() -> list[dict[str, str]]:
    return [
        {"id": "test", "label": "ทดสอบ (อ่าน Prod · ส่ง UAT)", "read": "Prod", "send": "UAT"},
        {"id": "uat", "label": "UAT ทั้งคู่", "read": "UAT", "send": "UAT"},
        {"id": "prod", "label": "Production ทั้งคู่", "read": "Prod", "send": "Prod"},
        {"id": "code", "label": "ตามค่าในโค้ด (default ไฟล์)", "read": _host_label(_code_default_bases()[0]), "send": _host_label(_code_default_bases()[1])},
    ]
