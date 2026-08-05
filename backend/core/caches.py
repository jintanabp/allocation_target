import logging
import os
import re
from datetime import datetime, timedelta

from .paths import safe_id

logger = logging.getLogger("target_allocation")


_CACHE_PREFIXES = (
    "hist_cache_",
    "hist_lysm_",
    "hist_prev_",
    "hist_cy_",
    "emp_cache_",
    "target_boxes_",
    "target_sun_",
)

# ไฟล์เป้าแบบ global เดิม (ไม่มี sup_id) — เหลือไว้เป็น fallback ช่วงเปลี่ยนผ่านเท่านั้น
# ต้องลบทิ้งพร้อมกับไฟล์ราย sup ด้วย ไม่งั้นไฟล์ราย sup หมดอายุก่อน แล้ว load_target_csv_for
# จะตกกลับไปอ่านไฟล์ global ที่ค้างอยู่ = ได้เป้าของทีมอื่นเงียบ ๆ (บั๊กเดิมที่เพิ่งแก้ไป)
_LEGACY_TARGET_FILES = ("target_boxes.csv", "target_sun.csv")


def cleanup_old_caches(max_age_days: int = 7) -> None:
    cutoff = datetime.now() - timedelta(days=max_age_days)
    # โหมด dev ที่วางไฟล์ global เอง — อย่าไปลบของเขา
    keep_legacy = os.environ.get("USE_LEGACY_TARGET_CSV", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    try:
        for fname in os.listdir("data"):
            is_cache = fname.startswith(_CACHE_PREFIXES)
            is_legacy_target = fname in _LEGACY_TARGET_FILES and not keep_legacy
            if not (is_cache or is_legacy_target):
                continue
            fpath = os.path.join("data", fname)
            if datetime.fromtimestamp(os.path.getmtime(fpath)) < cutoff:
                os.remove(fpath)
                logger.info("Cleaned old cache: %s", fname)
    except Exception as e:
        logger.warning("Cache cleanup error: %s", e)


def cleanup_export_artifacts_keep_latest_per_sup(keep_n: int = 1, sup_id: str | None = None) -> None:
    """
    ล้างไฟล์ export ที่สะสมใน data/ ให้เหลือ "ล่าสุด" ต่อ sup_id

    ไฟล์ที่จัดการ:
    - Target_{sup}_{brand}.xlsx
    - export_{sup}_{brand}.csv
    - Final_Dashboard_{sup}[_{yyyy}_{mm}].xlsx
    - final_allocation_{sup}[_{yyyy}_{mm}].csv

    ส่วน _{yyyy}_{mm} เป็นของใหม่ (ผูกไฟล์ผลกระจายกับงวด) ต้อง match ด้วย
    ไม่งั้นตัวกรอง only_sup จะเทียบ "SL330_2026_08" กับ "SL330" ไม่ตรง
    แล้วไฟล์ใหม่จะไม่เคยถูกล้างเลย

    หมายเหตุ:
    - parse sup จากชื่อไฟล์โดย assume ว่า sup_id ไม่ประกอบด้วย '_' (เช่น SL330)
      (ในระบบจริงรหัสมักเป็นรูปนี้อยู่แล้ว)
    """
    if keep_n < 1:
        keep_n = 1

    try:
        os.makedirs("data", exist_ok=True)
        only_sup = safe_id(sup_id) if sup_id else None
        cutoff_ts = (datetime.now() - timedelta(days=1)).timestamp()

        patterns: list[tuple[str, re.Pattern[str]]] = [
            ("Target", re.compile(r"^Target_(?P<sup>[^_]+)_.+\.xlsx$", re.IGNORECASE)),
            ("export", re.compile(r"^export_(?P<sup>[^_]+)_.+\.csv$", re.IGNORECASE)),
            (
                "Final_Dashboard",
                re.compile(r"^Final_Dashboard_(?P<sup>[^_.]+)(?:_\d{4}_\d{2})?\.xlsx$", re.IGNORECASE),
            ),
            (
                "final_allocation",
                re.compile(r"^final_allocation_(?P<sup>[^_.]+)(?:_\d{4}_\d{2})?\.csv$", re.IGNORECASE),
            ),
        ]

        by_sup: dict[str, list[tuple[float, str]]] = {}

        for fname in os.listdir("data"):
            sup = None
            for _, rx in patterns:
                m = rx.match(fname)
                if m:
                    sup = (m.group("sup") or "").strip()
                    break
            if not sup:
                continue
            if only_sup and sup != only_sup:
                continue
            fpath = os.path.join("data", fname)
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                continue
            by_sup.setdefault(sup, []).append((mtime, fpath))

        removed = 0
        for sup, items in by_sup.items():
            items.sort(key=lambda x: x[0], reverse=True)
            keep = items[:keep_n]
            drop = items[keep_n:]
            for mtime, fpath in drop:
                # ลบเฉพาะไฟล์ที่ "เก่ากว่า 1 วัน" เพื่อลดโอกาสชนกับการดาวน์โหลด/การ export ซ้ำในวันเดียวกัน
                if mtime >= cutoff_ts:
                    continue
                try:
                    os.remove(fpath)
                    removed += 1
                except OSError as e:
                    logger.warning("Export cleanup remove failed (%s): %s", fpath, e)

            if drop:
                logger.info(
                    "Export cleanup: sup=%s kept=%d removed=%d",
                    sup,
                    len(keep),
                    len(drop),
                )

        if removed:
            logger.info("Export cleanup done: removed=%d", removed)
    except Exception as e:
        logger.warning("Export cleanup error: %s", e)

