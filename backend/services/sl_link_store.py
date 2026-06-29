"""
ผูกรหัส SL (canonical ↔ alias) — รหัสใหม่สืบทอดสิทธิและทีมจากรหัสเก่า
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from typing import Any

logger = logging.getLogger("target_allocation")

_STORE_LOCK = threading.Lock()


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def sl_links_json_path() -> str:
    raw = (os.environ.get("SL_LINKS_JSON_PATH") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "config", "sl_links.json")


def normalize_sl(s: str | None) -> str:
    return str(s or "").strip().upper()


def _normalize_link(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    canonical = normalize_sl(row.get("canonical_sl"))
    if not canonical:
        return None
    aliases_raw = row.get("alias_sls")
    aliases: list[str] = []
    if isinstance(aliases_raw, list):
        for a in aliases_raw:
            aa = normalize_sl(a)
            if aa and aa not in aliases:
                aliases.append(aa)
    if canonical not in aliases:
        aliases.insert(0, canonical)
    out: dict[str, Any] = {
        "canonical_sl": canonical,
        "alias_sls": aliases,
        "note": str(row.get("note") or "").strip(),
    }
    updated_by = str(row.get("updated_by") or "").strip()
    if updated_by:
        out["updated_by"] = updated_by
    return out


def validate_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen_canonical: set[str] = set()
    alias_owner: dict[str, str] = {}

    for raw in links:
        row = _normalize_link(raw)
        if not row:
            raise ValueError("แถวผูกรหัส SL ไม่ถูกต้อง (ต้องมี canonical_sl)")
        canon = row["canonical_sl"]
        if canon in seen_canonical:
            raise ValueError(f"canonical_sl ซ้ำ: {canon}")
        seen_canonical.add(canon)
        for alias in row["alias_sls"]:
            owner = alias_owner.get(alias)
            if owner and owner != canon:
                raise ValueError(
                    f"รหัส {alias} ถูกผูกกับ {owner} แล้ว — ไม่สามารถผูกกับ {canon} ซ้ำ"
                )
            if alias in seen_canonical and alias != canon:
                raise ValueError(
                    f"รหัส {alias} เป็น canonical ของกลุ่มอื่น — ไม่สามารถเป็น alias ของ {canon}"
                )
            alias_owner[alias] = canon
        normalized.append(row)

    normalized.sort(key=lambda r: r["canonical_sl"])
    return normalized


def read_links_unlocked() -> list[dict[str, Any]]:
    path = sl_links_json_path()
    if not os.path.isfile(path):
        logger.warning("sl_links JSON ไม่พบ: %s", path)
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("อ่าน sl_links JSON ไม่ได้ %s: %s", path, e)
        raise PermissionError(f"ไม่สามารถโหลดตารางผูกรหัส SL ({path})") from e
    if isinstance(data, dict):
        links = data.get("links")
    elif isinstance(data, list):
        links = data
    else:
        raise PermissionError(f"รูปแบบ sl_links JSON ไม่ถูกต้อง: {path}")
    if not isinstance(links, list):
        raise PermissionError("รูปแบบ sl_links JSON ไม่ถูกต้อง (links ต้องเป็น array)")
    return validate_links(links)


def read_links() -> list[dict[str, Any]]:
    with _STORE_LOCK:
        return read_links_unlocked()


def write_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = validate_links(links)
    path = sl_links_json_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = json.dumps({"links": normalized}, ensure_ascii=False, indent=2) + "\n"
    dir_name = os.path.dirname(path) or "."
    with _STORE_LOCK:
        fd, tmp = tempfile.mkstemp(prefix=".sl_links_", suffix=".json", dir=dir_name)
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
    logger.info("บันทึก sl_links %d กลุ่ม → %s", len(normalized), path)
    return normalized


def alias_to_canonical_map(links: list[dict[str, Any]] | None = None) -> dict[str, str]:
    data = links if links is not None else read_links()
    out: dict[str, str] = {}
    for row in data:
        canon = row["canonical_sl"]
        for alias in row.get("alias_sls") or []:
            out[normalize_sl(alias)] = canon
        out[canon] = canon
    return out


def resolve_to_canonical(code: str, links: list[dict[str, Any]] | None = None) -> str:
    c = normalize_sl(code)
    if not c:
        return c
    return alias_to_canonical_map(links).get(c, c)


def expand_sl_codes(codes: set[str] | list[str], links: list[dict[str, Any]] | None = None) -> set[str]:
    """ขยายชุดรหัส SL ให้รวม canonical ของ alias"""
    m = alias_to_canonical_map(links)
    out: set[str] = set()
    for raw in codes:
        c = normalize_sl(raw)
        if not c:
            continue
        out.add(c)
        canon = m.get(c, c)
        if canon != c:
            out.add(canon)
    return out


def hierarchy_manager_code(code: str, links: list[dict[str, Any]] | None = None) -> str:
    """รหัสสำหรับค้น by_manager — alias ชี้ไป canonical"""
    return resolve_to_canonical(code, links)


def find_link(canonical_sl: str, links: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    canon = normalize_sl(canonical_sl)
    for row in links or read_links():
        if row["canonical_sl"] == canon:
            return row
    return None


def upsert_link(
    links: list[dict[str, Any]],
    *,
    canonical_sl: str,
    alias_sls: list[str] | None = None,
    note: str | None = None,
    updated_by: str | None = None,
) -> list[dict[str, Any]]:
    canon = normalize_sl(canonical_sl)
    if not canon:
        raise ValueError("canonical_sl ว่าง")
    out: list[dict[str, Any]] = []
    found = False
    for row in links:
        if row["canonical_sl"] == canon:
            found = True
            nr = dict(row)
            if alias_sls is not None:
                nr["alias_sls"] = alias_sls
            if note is not None:
                nr["note"] = str(note).strip()
            if updated_by:
                nr["updated_by"] = str(updated_by).strip()
            out.append(nr)
        else:
            out.append(dict(row))
    if not found:
        out.append(
            {
                "canonical_sl": canon,
                "alias_sls": alias_sls or [canon],
                "note": str(note or "").strip(),
                **({"updated_by": str(updated_by).strip()} if updated_by else {}),
            }
        )
    return write_links(out)


def delete_link(links: list[dict[str, Any]], canonical_sl: str) -> list[dict[str, Any]]:
    canon = normalize_sl(canonical_sl)
    kept = [r for r in links if r["canonical_sl"] != canon]
    if len(kept) == len(links):
        raise ValueError(f"ไม่พบกลุ่มผูกรหัส SL: {canon}")
    return write_links(kept)
