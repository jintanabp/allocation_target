"""
ผูกรหัส SKU (canonical ↔ alias) สำหรับรวมประวัติขายข้ามรหัสเก่า
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from typing import Any

import pandas as pd

logger = logging.getLogger("target_allocation")

_STORE_LOCK = threading.Lock()
_HIST_COLUMNS = ("emp_id", "sku", "hist_boxes", "hist_amount")


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def sku_links_json_path() -> str:
    raw = (os.environ.get("SKU_LINKS_JSON_PATH") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "config", "sku_links.json")


def normalize_sku(s: str | None) -> str:
    return str(s or "").strip()


def _empty_hist_df() -> pd.DataFrame:
    return pd.DataFrame(columns=list(_HIST_COLUMNS))


def _normalize_link(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    canonical = normalize_sku(row.get("canonical_sku"))
    if not canonical:
        return None
    aliases_raw = row.get("alias_skus")
    aliases: list[str] = []
    if isinstance(aliases_raw, list):
        for a in aliases_raw:
            aa = normalize_sku(a)
            if aa and aa not in aliases:
                aliases.append(aa)
    if canonical not in aliases:
        aliases.insert(0, canonical)
    out: dict[str, Any] = {
        "canonical_sku": canonical,
        "alias_skus": aliases,
        "product_name": str(row.get("product_name") or "").strip(),
        "note": str(row.get("note") or "").strip(),
    }
    updated_by = str(row.get("updated_by") or "").strip()
    if updated_by:
        out["updated_by"] = updated_by
    return out


def validate_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ตรวจรูปแบบ + ห้าม alias ซ้ำข้ามกลุ่ม / ห้าม canonical ชนกัน"""
    normalized: list[dict[str, Any]] = []
    seen_canonical: set[str] = set()
    alias_owner: dict[str, str] = {}

    for raw in links:
        row = _normalize_link(raw)
        if not row:
            raise ValueError("แถวผูกรหัส SKU ไม่ถูกต้อง (ต้องมี canonical_sku)")
        canon = row["canonical_sku"]
        if canon in seen_canonical:
            raise ValueError(f"canonical_sku ซ้ำ: {canon}")
        seen_canonical.add(canon)
        for alias in row["alias_skus"]:
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

    normalized.sort(key=lambda r: r["canonical_sku"])
    return normalized


def read_links_unlocked() -> list[dict[str, Any]]:
    path = sku_links_json_path()
    if not os.path.isfile(path):
        logger.warning("sku_links JSON ไม่พบ: %s", path)
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("อ่าน sku_links JSON ไม่ได้ %s: %s", path, e)
        raise PermissionError(f"ไม่สามารถโหลดตารางผูกรหัส SKU ({path})") from e
    if isinstance(data, dict):
        links = data.get("links")
    elif isinstance(data, list):
        links = data
    else:
        raise PermissionError(f"รูปแบบ sku_links JSON ไม่ถูกต้อง: {path}")
    if not isinstance(links, list):
        raise PermissionError(f"รูปแบบ sku_links JSON ไม่ถูกต้อง (links ต้องเป็น array): {path}")
    return validate_links(links)


def read_links() -> list[dict[str, Any]]:
    with _STORE_LOCK:
        return read_links_unlocked()


def write_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = validate_links(links)
    path = sku_links_json_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = json.dumps({"links": normalized}, ensure_ascii=False, indent=2) + "\n"
    dir_name = os.path.dirname(path) or "."
    with _STORE_LOCK:
        fd, tmp = tempfile.mkstemp(prefix=".sku_links_", suffix=".json", dir=dir_name)
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
    logger.info("บันทึก sku_links %d กลุ่ม → %s", len(normalized), path)
    return normalized


def alias_to_canonical_map(links: list[dict[str, Any]] | None = None) -> dict[str, str]:
    data = links if links is not None else read_links()
    out: dict[str, str] = {}
    for row in data:
        canon = row["canonical_sku"]
        for alias in row.get("alias_skus") or []:
            out[normalize_sku(alias)] = canon
        out[canon] = canon
    return out


def canonical_to_aliases_map(links: list[dict[str, Any]] | None = None) -> dict[str, list[str]]:
    data = links if links is not None else read_links()
    return {row["canonical_sku"]: list(row.get("alias_skus") or []) for row in data}


def extra_aliases_for_canonical(canonical_sku: str, links: list[dict[str, Any]] | None = None) -> list[str]:
    """รหัส alias อื่น (ไม่รวม canonical) — ใช้แสดง badge"""
    canon = normalize_sku(canonical_sku)
    for row in links or read_links():
        if row["canonical_sku"] == canon:
            return [a for a in row.get("alias_skus") or [] if a != canon]
    return []


def has_linked_history(canonical_sku: str, links: list[dict[str, Any]] | None = None) -> bool:
    return bool(extra_aliases_for_canonical(canonical_sku, links))


def expand_skus_for_dax(sku_list: list[str], links: list[dict[str, Any]] | None = None) -> list[str]:
    """ขยายรายการ SKU สำหรับ TREATAS ใน DAX (canonical + alias)"""
    alias_map = alias_to_canonical_map(links)
    canon_map = canonical_to_aliases_map(links)
    out: set[str] = set()
    for raw in sku_list or []:
        sku = normalize_sku(raw)
        if not sku:
            continue
        canon = alias_map.get(sku, sku)
        out.add(sku)
        out.add(canon)
        for alias in canon_map.get(canon, [canon]):
            aa = normalize_sku(alias)
            if aa:
                out.add(aa)
    return sorted(out)


def collapse_hist_to_canonical(
    df: pd.DataFrame | None,
    links: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """รวมแถวประวัติตาม canonical_sku"""
    if df is None or df.empty:
        return _empty_hist_df()
    alias_map = alias_to_canonical_map(links)
    if not alias_map:
        return df.copy()

    work = df.copy()
    work["emp_id"] = work["emp_id"].astype(str).str.strip()
    work["sku"] = work["sku"].astype(str).str.strip()
    work["canonical_sku"] = work["sku"].map(lambda s: alias_map.get(s, s))

    for col in ("hist_boxes", "hist_amount"):
        if col not in work.columns:
            work[col] = 0.0
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0.0)

    grouped = (
        work.groupby(["emp_id", "canonical_sku"], as_index=False)[["hist_boxes", "hist_amount"]]
        .sum()
        .rename(columns={"canonical_sku": "sku"})
    )
    return grouped


def find_link(canonical_sku: str, links: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    canon = normalize_sku(canonical_sku)
    for row in links or read_links():
        if row["canonical_sku"] == canon:
            return dict(row)
    return None


def upsert_link(
    links: list[dict[str, Any]],
    *,
    canonical_sku: str,
    alias_skus: list[str] | None = None,
    product_name: str | None = None,
    note: str | None = None,
    updated_by: str | None = None,
) -> list[dict[str, Any]]:
    canon = normalize_sku(canonical_sku)
    if not canon:
        raise ValueError("canonical_sku ว่าง")
    out: list[dict[str, Any]] = []
    found = False
    for row in links:
        if row["canonical_sku"] == canon:
            found = True
            nr = dict(row)
            if alias_skus is not None:
                nr["alias_skus"] = alias_skus
            if product_name is not None:
                nr["product_name"] = str(product_name).strip()
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
                "canonical_sku": canon,
                "alias_skus": alias_skus or [canon],
                "product_name": str(product_name or "").strip(),
                "note": str(note or "").strip(),
                **({"updated_by": str(updated_by).strip()} if updated_by else {}),
            }
        )
    return write_links(out)


def delete_link(links: list[dict[str, Any]], canonical_sku: str) -> list[dict[str, Any]]:
    canon = normalize_sku(canonical_sku)
    out = [r for r in links if r.get("canonical_sku") != canon]
    if len(out) == len(links):
        raise ValueError("ไม่พบกลุ่มผูกรหัสที่จะลบ")
    return write_links(out)
