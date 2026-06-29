#!/usr/bin/env python3
"""Diagnose why a supervisor has no TGA targets for a period."""

from __future__ import annotations

import argparse
import os
import sys

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

from backend.fabric_dax_connector import FabricDAXConnector  # noqa: E402
from backend.load_env import load_project_dotenv  # noqa: E402


def main() -> int:
    load_project_dotenv()
    ap = argparse.ArgumentParser(description="Diagnose supervisor target load")
    ap.add_argument("--sup", required=True, help="Supervisor code e.g. SL386")
    ap.add_argument("--month", type=int, required=True, ge=1, le=12)
    ap.add_argument("--year", type=int, required=True, ge=2020, le=2100)
    args = ap.parse_args()
    sup = str(args.sup).strip().upper()

    fabric = FabricDAXConnector()
    print(f"=== Supervisor {sup} | period {args.month:02d}/{args.year} ===")

    try:
        raw_eff = fabric.get_tga_max_effective_raw()
        print(f"MAX(EFFECTIVEDATE) or fallback: {raw_eff!r}")
    except Exception as e:
        print(f"MAX(EFFECTIVEDATE) query failed: {e}")

    df_emp = fabric.get_employees_by_manager(sup)
    print(f"Dim_Salesman team under SuperCode: {len(df_emp)} คน")
    if not df_emp.empty:
        print("  sample emp_id:", df_emp["emp_id"].head(5).tolist())

    emp_list = df_emp["emp_id"].astype(str).str.strip().tolist() if not df_emp.empty else []
    if not emp_list:
        print("RESULT: ไม่มีพนักงานใต้ SuperCode — จะได้ 404")
        return 1

    try:
        df_tga = fabric.get_tga_target_salesman_granular(emp_list, args.month, args.year)
    except Exception as e:
        print(f"TGA granular query failed: {e}")
        df_tga = None

    n_gran = 0 if df_tga is None else len(df_tga)
    print(f"TGA granular rows (EFFECTIVEDATE month/year): {n_gran}")
    if df_tga is not None and not df_tga.empty:
        pos = df_tga[df_tga["qty"].astype(float) > 0]
        emps_with = pos["emp_id"].nunique() if not pos.empty else 0
        skus_with = pos["sku"].nunique() if not pos.empty else 0
        print(f"  rows qty>0: {len(pos)} | emps: {emps_with} | skus: {skus_with}")
        if "warehouse_code" in df_tga.columns:
            wh_counts = (
                df_tga.groupby("emp_id")["warehouse_code"]
                .nunique()
                .sort_values(ascending=False)
            )
            multi = wh_counts[wh_counts >= 2]
            if not multi.empty:
                print(f"  multi-WH employees: {len(multi)} (e.g. {multi.head(3).to_dict()})")
    else:
        print("RESULT: ไม่มีแถว TGA งวดนี้ — จะได้ 409 TGA_PERIOD_EMPTY")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
