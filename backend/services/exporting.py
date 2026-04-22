import logging
import os

import pandas as pd
from fastapi import HTTPException
from fastapi.responses import FileResponse

from ..core.paths import excel_export_path, excel_path, export_result_path, safe_id
from ..core.targets import load_target_csv
from ..generate_excel import create_target_excel
from ..schemas import ExportRequest

logger = logging.getLogger("target_allocation")


def export_excel_service(req: ExportRequest, sup_id: str) -> dict:
    os.makedirs("data", exist_ok=True)

    df_final = pd.DataFrame([a.model_dump() for a in req.allocations])

    # เติม price_per_box ถ้าไม่ครบ
    df_sku_tmp, _ = load_target_csv()
    if df_sku_tmp is not None:
        want_cols = [
            "price_per_box",
            "brand_name_thai",
            "brand_name_english",
            "product_name_thai",
            "product_name_english",
        ]
        missing_cols = []
        for c in want_cols:
            if c not in df_final.columns:
                missing_cols.append(c)
                continue
            if c in ("price_per_box",):
                try:
                    if (
                        pd.to_numeric(df_final[c], errors="coerce")
                        .fillna(0)
                        .eq(0)
                        .all()
                    ):
                        missing_cols.append(c)
                except Exception:
                    missing_cols.append(c)
                continue
            try:
                if df_final[c].fillna("").astype(str).str.strip().eq("").all():
                    missing_cols.append(c)
            except Exception:
                missing_cols.append(c)
        if missing_cols:
            merge_cols = ["sku"] + [c for c in missing_cols if c in df_sku_tmp.columns]
            df_final = pd.merge(
                df_final,
                df_sku_tmp[merge_cols],
                on="sku",
                how="left",
                suffixes=("", "_csv"),
            )
            for c in missing_cols:
                if f"{c}_csv" in df_final.columns:
                    if c in ("price_per_box",):
                        df_final[c] = pd.to_numeric(df_final[c], errors="coerce").fillna(0)
                        df_final[c] = df_final[c].where(
                            df_final[c] != 0, df_final[f"{c}_csv"]
                        )
                    else:
                        df_final[c] = df_final[c].fillna("").astype(str)
                        df_final[c] = df_final[c].where(
                            df_final[c].str.strip() != "", df_final[f"{c}_csv"]
                        )
                    df_final.drop(columns=[f"{c}_csv"], inplace=True)

    brand_filter = req.brand_filter
    df_export = df_final.copy()
    if brand_filter != "ALL":
        df_export = df_final[df_final["brand_name_thai"] == brand_filter].copy()
        if df_export.empty:
            raise HTTPException(404, detail=f"ไม่พบข้อมูลสำหรับแบรนด์ '{brand_filter}'")

    ep = export_result_path(sup_id, brand_filter)
    df_export.to_csv(ep, index=False)

    yellow_map = {y.emp_id: y.yellow_target for y in req.yellow_targets}

    create_target_excel(
        result_csv=ep,
        output_path=excel_export_path(sup_id, brand_filter),
        brand_filter=brand_filter,
        yellow_map=yellow_map,
        sup_id=sup_id,
        target_boxes_csv="data/target_boxes.csv",
    )

    logger.info("Export excel: sup=%s brand=%s rows=%d", sup_id, brand_filter, len(df_export))
    return {"status": "ok", "brand_filter": brand_filter, "rows": len(df_export)}


def download_excel_response(sup_id: str, brand: str) -> FileResponse:
    fpath = excel_export_path(sup_id, brand)
    if not os.path.exists(fpath):
        # backward compat: ถ้ายังไม่ได้ export ตามแบรนด์ ให้ลองไฟล์เดิม
        fpath = excel_path(sup_id)
        if not os.path.exists(fpath):
            raise HTTPException(404, detail="ไม่พบไฟล์ Excel กรุณา Optimize หรือ Export ก่อน")

    return FileResponse(
        fpath,
        filename=f"Target_{safe_id(sup_id)}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

