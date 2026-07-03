#!/usr/bin/env python3
"""
ตรวจและซ่อม config/user_access.json ตามกฎ Excel roster:
- อนุมาน manager_level / acc_scope
- จัดฟิลด์ครบทุกแถว (ว่าง = none)
- คำนวณ visible_supervisor_codes ใหม่
- rebuild access_hierarchy.json
"""

from __future__ import annotations

import os
import sys
from typing import Any

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

from backend.services.access_hierarchy import (  # noqa: E402
    compute_visible_supervisors_for_row,
    enrich_rows_with_visibility,
    persist_hierarchy,
    build_hierarchy_payload,
)
from backend.services.user_access_store import (  # noqa: E402
    canonicalize_user_access_rows,
    read_rows,
)

USER_ACCESS_PATH = os.path.join(REPO, "config", "user_access.json")


def audit_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for r in rows:
        upl = str(r.get("userpl") or "").strip().upper()
        lk = str(r.get("login_kind") or "")
        vis = list(r.get("visible_supervisor_codes") or [])
        if not vis:
            vis = compute_visible_supervisors_for_row(r, all_rows=rows)
        ok = bool(vis)
        if lk == "supervisor_acc":
            ok = upl in vis
        elif lk == "manager_acc":
            ok = bool(vis)
        elif lk == "standard" and upl:
            ok = upl in vis or bool(vis)
        report.append(
            {
                "email": r.get("email"),
                "userpl": upl,
                "login_kind": lk,
                "visible": vis,
                "ok": ok,
            }
        )
    return report


def main() -> int:
    rows = read_rows()
    with_vis = enrich_rows_with_visibility(rows)
    enriched = canonicalize_user_access_rows(with_vis)
    report = audit_rows(enriched)
    bad = [x for x in report if not x["ok"]]
    legacy = [
        x
        for x in report
        if str(x.get("login_kind") or "") == "standard"
        and not str(x.get("visible") or [])
    ]

    import json

    with open(USER_ACCESS_PATH, "w", encoding="utf-8") as f:
        json.dump(enriched, f, ensure_ascii=False, indent=2)
        f.write("\n")

    payload = build_hierarchy_payload(enriched)
    path = persist_hierarchy(payload)

    print(f"rows: {len(enriched)}")
    ok_count = sum(1 for x in report if x["ok"])
    print(f"visible audit: {ok_count}/{len(report)} OK")
    print(f"hierarchy -> {path}")

    if legacy:
        print(f"legacy/standard ไม่มีกฎชัด: {len(legacy)} แถว")
        for x in legacy[:10]:
            print(f"  {x['email']} / {x['userpl']}")

    if bad:
        print("FAILED:")
        for x in bad:
            print(f"  {x['email']} / {x['userpl']} kind={x['login_kind']} visible={x['visible']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
