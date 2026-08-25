"""Global Fabric cache ต่องวด — ลด DAX ซ้ำเมื่อสลับ SL"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger("target_allocation")

_LOCK = threading.Lock()


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def cache_dir() -> str:
    raw = (os.environ.get("FABRIC_CACHE_DIR") or "").strip()
    if raw:
        return os.path.normpath(os.path.abspath(raw))
    return os.path.join(_repo_root(), "data", "cache")


def fabric_static_cache_ttl_sec() -> int:
    raw = (os.environ.get("FABRIC_STATIC_CACHE_TTL_SEC") or "86400").strip()
    try:
        return int(raw)
    except ValueError:
        return 86400


def _cache_enabled() -> bool:
    return fabric_static_cache_ttl_sec() > 0


def _product_path(year: int, month: int) -> str:
    return os.path.join(cache_dir(), f"dim_product_{int(year)}_{int(month):02d}.json")


def _price_path(year: int, month: int) -> str:
    return os.path.join(cache_dir(), f"price_per_box_{int(year)}_{int(month):02d}.json")


def _tga_skus_path(year: int, month: int) -> str:
    return os.path.join(cache_dir(), f"tga_skus_{int(year)}_{int(month):02d}.csv")


def seed_cache_from_repo() -> int:
    """
    เติมไฟล์แคชตั้งต้นจาก seed/cache/ ถ้า data/cache/ ยังไม่มีไฟล์นั้น

    ใช้ตอนเซิร์ฟเวอร์ยังไม่เคยดึงข้อมูลงวดนั้นสำเร็จเลย และ Fabric ก็ดึงไม่ได้
    (เช่น capacity เต็ม) — ถ้าไม่มีราคาเลย เป้ารวมจะเป็น 0 แล้วทุกทีมเปิดงวด
    ไม่ได้พร้อมกัน · คัดลอกเฉพาะไฟล์ที่ยังไม่มี **ไม่เคยเขียนทับของจริง**
    ของที่ดึงสดมาได้จึงชนะเสมอ และพอ Fabric กลับมาปกติไฟล์นี้ก็ถูกแทนที่ไปเอง

    วางไว้ที่ seed/ แทนที่จะ track ไฟล์ใน data/cache/ ตรง ๆ เพราะไฟล์แคชจริง
    เขียนทับตัวเองทุกครั้งที่ดึงสำเร็จ ถ้า track ไว้ pull ครั้งหน้าจะชนกันเอง
    (บทเรียนเดียวกับ config/app_runtime.json ที่โดน pull ทับจนค่าหาย)
    """
    src_dir = os.path.join(_repo_root(), "seed", "cache")
    if not os.path.isdir(src_dir):
        return 0
    dst_dir = cache_dir()
    copied = 0
    try:
        os.makedirs(dst_dir, exist_ok=True)
        names = sorted(os.listdir(src_dir))
    except OSError as e:
        logger.warning("อ่านโฟลเดอร์ seed ไม่ได้: %s", e)
        return 0
    for name in names:
        if not name.endswith(".json"):
            continue
        dst = os.path.join(dst_dir, name)
        if os.path.exists(dst):
            continue                      # ของจริงมีอยู่แล้ว ห้ามแตะ
        try:
            shutil.copyfile(os.path.join(src_dir, name), dst)
            copied += 1
            logger.info("เติมแคชตั้งต้นจาก seed: %s", name)
        except OSError as e:
            logger.warning("คัดลอก seed %s ไม่สำเร็จ: %s", name, e)
    return copied


def _read_meta(path: str, *, allow_stale: bool = False) -> dict[str, Any] | None:
    """
    allow_stale: ยอมใช้แคชที่หมดอายุแล้ว — สำหรับตอนดึงของใหม่ไม่ได้เท่านั้น

    ราคาสินค้าแทบไม่ขยับระหว่างงวด ราคาเมื่อวานจึงใกล้เคียงความจริงกว่า 0 มาก
    ตอน Fabric ล่ม (เช่น capacity เต็ม) ถ้าทิ้งแคชเก่าไปด้วย ทุก SKU จะได้ราคา 0
    แล้ว "เป้ารวม (บาท)" ของทุกทีมกลายเป็น 0 ทั้งที่จำนวนหีบมาจาก Target Sun
    ครบถ้วน — หน้าเว็บอ่านค่า 0 นั้นแล้วสรุปว่า "ไม่มีเป้าในงวดนี้" ทั้งระบบ
    """
    if not _cache_enabled() or not os.path.isfile(path):
        return None
    try:
        if path.endswith(".json"):
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        else:
            return None
        cached_at_raw = str(doc.get("cached_at") or "").strip()
        if not cached_at_raw:
            return None
        cached_at = datetime.fromisoformat(cached_at_raw.replace("Z", "+00:00"))
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - cached_at.astimezone(timezone.utc)).total_seconds()
        if age > fabric_static_cache_ttl_sec():
            if not allow_stale:
                return None
            logger.warning(
                "ใช้แคชที่หมดอายุแล้ว %s (เก่า %.1f ชม.) — ดึงของใหม่ไม่ได้",
                os.path.basename(path), age / 3600.0,
            )
        return doc
    except Exception as e:
        logger.warning("fabric cache read %s: %s", path, e)
        return None


def _write_json_cache(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    doc = {
        "cached_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        **payload,
    }
    with _LOCK:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def product_price_asof(year: int, month: int) -> str:
    """วันที่ที่ราคาในแคชก้อนนี้ถูกคิด — วันที่ 1 ของงวดเป้า"""
    return f"{int(year):04d}-{int(month):02d}-01"


def read_product_info_df(
    year: int, month: int, *, allow_stale: bool = False
) -> pd.DataFrame | None:
    doc = _read_meta(_product_path(year, month), allow_stale=allow_stale)
    if not doc:
        return None
    # แคชที่เขียนไว้ก่อนแก้บั๊กราคา (ไม่มี price_asof) ถือราคา ณ "วันที่ดึง"
    # ซึ่งเป็นราคาเก่าเมื่อทำเป้าล่วงหน้า — ต้องทิ้งแล้วดึงใหม่ ไม่ใช่รอ TTL หมดอายุ
    if str(doc.get("price_asof") or "") != product_price_asof(year, month):
        logger.info(
            "ทิ้งแคชสินค้า %d-%02d: ราคาคิดจากวันที่ไม่ตรงงวด (%r)",
            year, month, doc.get("price_asof"),
        )
        return None
    rows = doc.get("rows")
    if not isinstance(rows, list):
        return None
    if not rows:
        return pd.DataFrame()
    # แคชที่เขียนไว้ก่อนเพิ่มราคารถเงินสด มีแต่ราคาเครดิต — ทีมรถเงินสดจะได้ราคาผิด
    # ทิ้งแล้วดึงใหม่ ไม่ใช่รอ TTL (หลักเดียวกับ price_asof)
    if "cash_unit_price" not in rows[0]:
        logger.info(
            "ทิ้งแคชสินค้า %d-%02d: ยังไม่มีราคารถเงินสด (แคชรุ่นเก่า)", year, month
        )
        return None
    return pd.DataFrame(rows)


def write_product_info_df(year: int, month: int, df: pd.DataFrame) -> None:
    if not _cache_enabled() or df is None:
        return
    rows = df.to_dict(orient="records")
    _write_json_cache(
        _product_path(year, month),
        {
            "rows": rows,
            "row_count": len(rows),
            "price_asof": product_price_asof(year, month),
        },
    )


def read_price_map(
    year: int, month: int, *, allow_stale: bool = False
) -> dict[str, float] | None:
    doc = _read_meta(_price_path(year, month), allow_stale=allow_stale)
    if not doc:
        return None
    prices = doc.get("prices")
    if not isinstance(prices, dict):
        return None
    return {str(k): float(v) for k, v in prices.items()}


def write_price_map(year: int, month: int, price_map: dict[str, float]) -> None:
    if not _cache_enabled():
        return
    _write_json_cache(
        _price_path(year, month),
        {"prices": price_map, "row_count": len(price_map)},
    )


def read_tga_skus_csv(year: int, month: int) -> pd.DataFrame | None:
    path = _tga_skus_path(year, month)
    meta_path = path + ".meta.json"
    if not _cache_enabled() or not os.path.isfile(path):
        return None
    doc = _read_meta(meta_path)
    if not doc:
        return None
    try:
        return pd.read_csv(path, dtype=str)
    except Exception as e:
        logger.warning("fabric tga skus read %s: %s", path, e)
        return None


def write_tga_skus_csv(year: int, month: int, df: pd.DataFrame) -> None:
    if not _cache_enabled() or df is None:
        return
    path = _tga_skus_path(year, month)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with _LOCK:
        df.to_csv(path, index=False)
        _write_json_cache(path + ".meta.json", {"row_count": len(df)})


def cache_status(year: int, month: int) -> list[dict[str, Any]]:
    """สถานะแต่ละ layer สำหรับ Admin UI"""
    layers = [
        ("product", _product_path(year, month), "ข้อมูลสินค้า (Dim_Product)"),
        ("price", _price_path(year, month), "ราคาต่อหีบจากประวัติขาย"),
        ("tga_skus", _tga_skus_path(year, month), "รายการ SKU มีเป้า TGA"),
    ]
    out: list[dict[str, Any]] = []
    ttl = fabric_static_cache_ttl_sec()
    for layer_id, path, label in layers:
        meta_path = path if path.endswith(".json") else path + ".meta.json"
        exists = os.path.isfile(path)
        doc = _read_meta(meta_path) if exists else None
        cached_at = doc.get("cached_at") if doc else None
        row_count = doc.get("row_count") if doc else None
        if row_count is None and exists and layer_id == "tga_skus":
            try:
                row_count = max(0, sum(1 for _ in open(path, encoding="utf-8")) - 1)
            except OSError:
                row_count = None
        out.append(
            {
                "layer": layer_id,
                "label": label,
                "path": path,
                "exists": exists,
                "fresh": doc is not None,
                "cached_at": cached_at,
                "row_count": row_count,
                "ttl_sec": ttl,
            }
        )
    return out


def invalidate_period_cache(
    year: int | None = None,
    month: int | None = None,
    *,
    layers: set[str] | None = None,
) -> int:
    """ลบไฟล์ cache ตามงวด/layer — คืนจำนวนไฟล์ที่ลบ"""
    root = cache_dir()
    if not os.path.isdir(root):
        return 0
    want = layers or {"product", "price", "tga_skus", "payload"}
    removed = 0
    suffix = ""
    if year is not None and month is not None:
        suffix = f"_{int(year)}_{int(month):02d}"
    for name in os.listdir(root):
        path = os.path.join(root, name)
        hit = False
        if "product" in want and name.startswith("dim_product") and (not suffix or name.endswith(suffix + ".json")):
            hit = True
        elif "price" in want and name.startswith("price_per_box") and (not suffix or name.endswith(suffix + ".json")):
            hit = True
        elif "tga_skus" in want and (name.startswith("tga_skus") or name.startswith("tga_skus")):
            if not suffix or suffix in name:
                hit = True
        if hit:
            try:
                os.unlink(path)
                removed += 1
            except OSError as e:
                logger.warning("fabric cache delete %s: %s", path, e)
    return removed
