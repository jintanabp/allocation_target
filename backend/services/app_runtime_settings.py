"""
การตั้งค่าระหว่างรัน — แอดมินเปลี่ยนได้โดยไม่แก้ .env
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from typing import Any, Literal

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


def read_settings_unlocked() -> dict[str, Any]:
    path = settings_json_path()
    if not os.path.isfile(path):
        return _default_settings()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
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
        logger.warning("read app_runtime settings failed: %s", e)
        return _default_settings()


def read_settings() -> dict[str, Any]:
    with _LOCK:
        return read_settings_unlocked()


def _write_settings_unlocked(data: dict[str, Any]) -> dict[str, Any]:
    path = settings_json_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(path) or ".",
        prefix=".app_runtime.",
        suffix=".json",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return dict(data)


def get_target_read_source() -> TargetReadSource:
    src = str(read_settings().get("target_read_source") or "targetsun").strip().lower()
    if src == "fabric":
        return "fabric"
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
    if src not in _VALID_SOURCES:
        raise ValueError("target_read_source ต้องเป็น targetsun หรือ fabric")
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
