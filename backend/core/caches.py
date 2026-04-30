import logging
import os
import re
from datetime import datetime, timedelta

from .paths import safe_id

logger = logging.getLogger("target_allocation")


def cleanup_old_caches(max_age_days: int = 7) -> None:
    cutoff = datetime.now() - timedelta(days=max_age_days)
    try:
        for fname in os.listdir("data"):
            if fname.startswith(
                ("hist_cache_", "hist_lysm_", "hist_prev_", "hist_cy_", "emp_cache_")
            ):
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
    - Final_Dashboard_{sup}.xlsx (legacy)
    - final_allocation_{sup}.csv (legacy)

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
            ("Final_Dashboard", re.compile(r"^Final_Dashboard_(?P<sup>[^.]+)\.xlsx$", re.IGNORECASE)),
            ("final_allocation", re.compile(r"^final_allocation_(?P<sup>[^.]+)\.csv$", re.IGNORECASE)),
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

