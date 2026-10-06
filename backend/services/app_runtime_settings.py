"""
การตั้งค่าระหว่างรัน — แอดมินเปลี่ยนได้โดยไม่แก้ .env
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Literal

from ..core.atomic_io import atomic_write_text

logger = logging.getLogger("target_allocation")

_LOCK = threading.Lock()
_VALID_SOURCES = frozenset({"targetsun", "fabric"})
_VALID_PRESETS = frozenset({"test", "uat", "prod", "code"})
TargetReadSource = Literal["targetsun", "fabric"]
TargetEndpointPreset = Literal["test", "uat", "prod", "code"]


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def settings_json_path() -> str:
    raw = (os.environ.get("APP_RUNTIME_SETTINGS_PATH") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "config", "app_runtime.json")


def _default_settings() -> dict[str, Any]:
    return {
        "target_read_source": "targetsun",
        "target_endpoint_preset": "test",
        "target_read_api_base": None,
        "target_import_api_base": None,
    }


def _normalize_optional_url(raw: Any) -> str | None:
    s = str(raw or "").strip().rstrip("/")
    return s if s else None


_WARNED: set[str] = set()


def _log_default_once(kind: str, msg: str, *args: Any) -> None:
    """ถูกเรียกทุกคำขอ — log ERROR ครั้งเดียวต่อสภาพ ไม่ท่วม log"""
    if kind in _WARNED:
        return
    _WARNED.add(kind)
    logger.error(msg, *args)


def settings_file_status() -> str:
    """
    "ok" | "missing" | "corrupt" — ไฟล์ตั้งค่าอยู่ในสภาพไหน

    app_runtime.json ไม่อยู่ใน git แล้ว ไฟล์หาย/เสีย/ขึ้นเครื่องใหม่ = ระบบกลับไปใช้
    ค่าตั้งต้น (preset "test": อ่าน Prod ส่ง UAT) โดยไม่มีใครรู้ (ผลตรวจ §2.2)
    หน้าแอดมินและหน้าส่งใช้ค่านี้ขึ้นคำเตือน
    """
    path = settings_json_path()
    if not os.path.isfile(path):
        return "missing"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return "corrupt"
    return "ok" if isinstance(data, dict) else "corrupt"


def read_settings_unlocked() -> dict[str, Any]:
    path = settings_json_path()
    if not os.path.isfile(path):
        _log_default_once(
            "missing", "ไม่พบไฟล์ตั้งค่า %s — ใช้ค่าตั้งต้น (ปลายทาง preset 'test': อ่าน Prod ส่ง UAT)", path
        )
        return _default_settings()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            _log_default_once("corrupt", "ไฟล์ตั้งค่า %s ผิดรูปแบบ — ใช้ค่าตั้งต้น (preset 'test')", path)
            return _default_settings()
        out = _default_settings()
        src = str(data.get("target_read_source") or "").strip().lower()
        if src in _VALID_SOURCES:
            out["target_read_source"] = src
        preset = str(data.get("target_endpoint_preset") or "").strip().lower()
        if preset in _VALID_PRESETS:
            out["target_endpoint_preset"] = preset
        out["target_read_api_base"] = _normalize_optional_url(data.get("target_read_api_base"))
        out["target_import_api_base"] = _normalize_optional_url(data.get("target_import_api_base"))
        return out
    except (json.JSONDecodeError, OSError) as e:
        _log_default_once("unreadable", "อ่านไฟล์ตั้งค่า app_runtime ไม่ได้ (%s) — ใช้ค่าตั้งต้น (preset 'test')", e)
        return _default_settings()


def read_settings() -> dict[str, Any]:
    with _LOCK:
        return read_settings_unlocked()


def _write_settings_unlocked(data: dict[str, Any]) -> dict[str, Any]:
    path = settings_json_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # ผลตรวจ §5.1-4: เดิมเขียน tempfile + os.replace เองโดยไม่มี retry — บน Windows
    # antivirus/ตัวทำ index ถือไฟล์ค้างชั่วขณะแล้วบันทึกพังเป็น PermissionError
    # เนื้อไฟล์เหมือน json.dump(indent=2) + "\n" แบบ text mode เดิมทุกไบต์
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, payload.replace("\n", os.linesep))
    return dict(data)


def get_target_read_source() -> TargetReadSource:
    """
    เป้าอ่านจาก Target Sun อย่างเดียว (ผู้ใช้ตัดสิน 6 ต.ค. 2026 — ไม่ใช้ Fabric อ่านเป้าแล้ว)

    ไฟล์ app_runtime.json บน server อาจยังจด "fabric" ไว้จากการสลับทดสอบครั้งก่อน — เมินค่านั้น
    (ไม่ต้องไปแก้ไฟล์บน server) · ยอดขายย้อนหลัง/ราคายังดึงจาก Fabric ตามเดิม ไม่เกี่ยวกับค่านี้
    """
    return "targetsun"


def get_target_endpoint_config() -> dict[str, Any]:
    data = read_settings()
    preset = str(data.get("target_endpoint_preset") or "").strip().lower() or None
    if preset == "code":
        preset = None
    return {
        "preset": preset,
        "read_base": _normalize_optional_url(data.get("target_read_api_base")),
        "import_base": _normalize_optional_url(data.get("target_import_api_base")),
        "preset_stored": str(data.get("target_endpoint_preset") or "test"),
    }


def set_target_read_source(source: str) -> dict[str, Any]:
    src = str(source or "").strip().lower()
    if src != "targetsun":
        # ตัวเลือก Fabric ถูกเอาออกแล้ว (6 ต.ค. 2026)
        raise ValueError("อ่านเป้าได้จาก Target Sun เท่านั้น — เลิกใช้ Fabric อ่านเป้าแล้ว")
    with _LOCK:
        data = read_settings_unlocked()
        data["target_read_source"] = src
        return _write_settings_unlocked(data)


def set_target_endpoint_preset(preset: str) -> dict[str, Any]:
    key = str(preset or "").strip().lower()
    if key not in _VALID_PRESETS:
        raise ValueError("preset ต้องเป็น test, uat, prod หรือ code")
    with _LOCK:
        data = read_settings_unlocked()
        data["target_endpoint_preset"] = key
        if key != "code":
            data["target_read_api_base"] = None
            data["target_import_api_base"] = None
        return _write_settings_unlocked(data)
