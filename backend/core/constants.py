import os

PRICE_FALLBACK = {
    "624007": 240.00,
    "624015": 212.00,
    "624049": 335.00,
    "624056": 290.00,
    "624114": 212.00,
    "624163": 232.71,
}

VALID_STRATEGIES = ("L3M", "L6M", "EVEN", "PUSH", "LP")


def debug_endpoints_enabled() -> bool:
    return os.environ.get("ENABLE_DEBUG_ENDPOINTS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

