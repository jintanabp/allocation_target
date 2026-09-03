"""
ตารางสิทธิ์หน้าแอดมิน — บทบาทไหนเข้าหน้าไหนได้ (dev แก้เองได้จากหน้าแอดมิน)

โครงลอกจาก sku_link_store.py: ล็อกตอนอ่าน/เขียน, เขียนแบบ atomic,
และ **ไฟล์เพี้ยน = raise ไม่ใช่คืนค่าว่าง** เพราะการคืนค่าว่างเงียบ ๆ ในไฟล์สิทธิ์
แปลว่า "ไม่มีใครได้สิทธิ์อะไรเลย" หรือแย่กว่านั้นคือถูกตีความเป็น "เปิดหมด"
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from typing import Any

from .admin_capabilities import (
    CONFIGURABLE_ROLES,
    DEFAULT_ROLE_CAPABILITIES,
    all_capability_keys,
    can_grant,
    is_grantable,
    is_known_capability,
)

logger = logging.getLogger("target_allocation")

_STORE_LOCK = threading.Lock()

ROLE_DEV = "dev"


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def admin_permissions_json_path() -> str:
    raw = (os.environ.get("ADMIN_PERMISSIONS_JSON_PATH") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "config", "admin_permissions.json")


def default_roles() -> dict[str, list[str]]:
    return {role: list(caps) for role, caps in DEFAULT_ROLE_CAPABILITIES.items()}


def validate_roles(roles: Any) -> dict[str, list[str]]:
    """
    ตรวจว่าตารางสิทธิ์ที่ส่งมาถูกกติกา แล้วคืนรูปแบบมาตรฐาน

    กติกาที่บังคับ (ทั้งตอนอ่านไฟล์และตอนบันทึก):
      - บทบาทต้องอยู่ใน CONFIGURABLE_ROLES — ห้ามมี "dev" (dev ได้ทุกอย่างเสมอ ถอดไม่ได้)
      - capability ต้องมีจริงในทะเบียน
      - capability ที่ล็อกไว้ (allowed_roles ว่าง) มอบให้ใครไม่ได้
      - capability ที่มอบได้ ต้องมอบให้บทบาทที่อนุญาตเท่านั้น
        (เช่น "roles" ให้ได้เฉพาะหัวหน้าแอดมิน — ไม่งั้นแอดมินยกระดับตัวเองได้)
    """
    if not isinstance(roles, dict):
        raise ValueError("ตารางสิทธิ์ต้องเป็น object ของ บทบาท → รายการสิทธิ์")

    out: dict[str, list[str]] = {}
    for role_raw, caps_raw in roles.items():
        role = str(role_raw or "").strip()
        if role == ROLE_DEV:
            raise ValueError("ห้ามตั้งสิทธิ์ให้ dev — dev มีทุกสิทธิ์เสมอและถอดไม่ได้")
        if role not in CONFIGURABLE_ROLES:
            raise ValueError(f"ไม่รู้จักบทบาท '{role}' (ตั้งได้เฉพาะ {', '.join(CONFIGURABLE_ROLES)})")
        if not isinstance(caps_raw, list):
            raise ValueError(f"สิทธิ์ของบทบาท '{role}' ต้องเป็น array")

        caps: list[str] = []
        for cap_raw in caps_raw:
            cap = str(cap_raw or "").strip()
            if not cap:
                continue
            if not is_known_capability(cap):
                raise ValueError(f"ไม่รู้จักสิทธิ์ '{cap}'")
            if not is_grantable(cap):
                raise ValueError(f"สิทธิ์ '{cap}' มอบให้บทบาทอื่นไม่ได้ — เป็นของ dev เท่านั้น")
            if not can_grant(cap, role):
                raise ValueError(f"สิทธิ์ '{cap}' มอบให้บทบาท '{role}' ไม่ได้")
            if cap not in caps:
                caps.append(cap)
        out[role] = caps

    for role in CONFIGURABLE_ROLES:
        out.setdefault(role, [])
    return out


def read_roles_unlocked() -> dict[str, list[str]]:
    path = admin_permissions_json_path()
    if not os.path.isfile(path):
        # ยังไม่เคยตั้งค่า = ใช้ค่าตั้งต้นซึ่งเท่ากับพฤติกรรมเดิมทุกประการ
        logger.info("admin_permissions JSON ไม่พบ (%s) — ใช้ค่าตั้งต้น", path)
        return default_roles()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("อ่าน admin_permissions JSON ไม่ได้ %s: %s", path, e)
        raise PermissionError(f"ไม่สามารถโหลดตารางสิทธิ์หน้าแอดมิน ({path})") from e
    if not isinstance(data, dict):
        raise PermissionError(f"รูปแบบ admin_permissions JSON ไม่ถูกต้อง: {path}")
    roles = data.get("roles")
    if not isinstance(roles, dict):
        raise PermissionError(f"รูปแบบ admin_permissions JSON ไม่ถูกต้อง (roles ต้องเป็น object): {path}")
    try:
        return validate_roles(roles)
    except ValueError as e:
        raise PermissionError(f"ตารางสิทธิ์ในไฟล์ไม่ถูกกติกา ({path}): {e}") from e


def read_roles() -> dict[str, list[str]]:
    with _STORE_LOCK:
        return read_roles_unlocked()


def write_roles(roles: Any, updated_by: str = "", updated_at: str = "") -> dict[str, list[str]]:
    normalized = validate_roles(roles)
    path = admin_permissions_json_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = json.dumps(
        {
            "version": 1,
            "roles": normalized,
            "updated_by": str(updated_by or "").strip(),
            "updated_at": str(updated_at or "").strip(),
        },
        ensure_ascii=False,
        indent=2,
    )
    payload += "\n"
    dir_name = os.path.dirname(path) or "."
    with _STORE_LOCK:
        fd, tmp = tempfile.mkstemp(prefix=".admin_permissions_", suffix=".json", dir=dir_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    logger.info("บันทึกตารางสิทธิ์หน้าแอดมิน → %s", path)
    return normalized


def capabilities_for_role(role: str | None) -> list[str]:
    """
    สิทธิ์ทั้งหมดของบทบาทนั้น

    dev ได้ทุกสิทธิ์เสมอ รวมของที่ล็อกไว้ — ตั้งใจให้ถอดไม่ได้ ไม่งั้นตั้งค่าพลาดครั้งเดียว
    แล้วไม่มีใครเข้าหน้าตั้งสิทธิ์ได้อีกเลย
    """
    r = str(role or "").strip()
    if r == ROLE_DEV:
        return list(all_capability_keys())
    if r not in CONFIGURABLE_ROLES:
        return []
    return list(read_roles().get(r, []))


def role_has_capability(role: str | None, cap: str) -> bool:
    return str(cap or "").strip() in capabilities_for_role(role)
