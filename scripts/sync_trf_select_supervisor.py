#!/usr/bin/env python3
"""
ดึง trf_select_supervisor จาก Fabric semantic → data/managers_cache.json + config/trf_select_supervisor.json

รัน: python scripts/sync_trf_select_supervisor.py
"""

from __future__ import annotations

import os
import sys

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services.managers import (  # noqa: E402
    persist_managers_payload,
    try_fetch_managers_from_fabric,
)


def main() -> int:
    payload = try_fetch_managers_from_fabric()
    if not payload or not payload.get("rows"):
        print("ไม่พบข้อมูล trf_select_supervisor จาก Fabric")
        return 1
    persist_managers_payload(payload)
    n = len(payload.get("rows") or [])
    m = len(payload.get("manager_codes") or [])
    print(f"OK: {n} แถว, {m} manager (DEPENDON) → data/managers_cache.json + config/trf_select_supervisor.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
