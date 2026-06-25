#!/usr/bin/env python3
"""อ่าน user_access.json → เขียน access_hierarchy.json + data/managers_cache.json"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services.access_hierarchy import (  # noqa: E402
    build_hierarchy_payload,
    enrich_rows_with_visibility,
    persist_hierarchy,
)
from backend.services.user_access_store import user_access_json_path  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild access hierarchy from user_access.json")
    ap.add_argument("--input", default=user_access_json_path(), help="user_access.json path")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        print(f"ไม่พบ: {args.input}", file=sys.stderr)
        return 1

    with open(args.input, encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        print("user_access.json ต้องเป็น array", file=sys.stderr)
        return 1

    enriched = enrich_rows_with_visibility(rows)
    payload = build_hierarchy_payload(enriched)
    path = persist_hierarchy(payload)

    mgr = len(payload.get("manager_codes") or [])
    sup = len(payload.get("supervisors") or [])
    print(f"rebuild OK: {mgr} managers, {sup} supervisors -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
