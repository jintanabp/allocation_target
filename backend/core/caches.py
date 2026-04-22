import logging
import os
from datetime import datetime, timedelta

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

