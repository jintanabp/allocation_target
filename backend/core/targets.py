import os
import pandas as pd


def load_target_csv() -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    df_sku = df_sun = None
    if os.path.exists("data/target_boxes.csv"):
        df_sku = (
            pd.read_csv("data/target_boxes.csv", dtype={"sku": str})
            .dropna(subset=["sku"])
            .fillna(0)
        )
        df_sku["sku"] = df_sku["sku"].astype(str).str.strip()
    if os.path.exists("data/target_sun.csv"):
        df_sun = (
            pd.read_csv("data/target_sun.csv", dtype={"emp_id": str})
            .dropna(subset=["emp_id"])
            .fillna(0)
        )
        df_sun["emp_id"] = df_sun["emp_id"].astype(str).str.strip()
    return df_sku, df_sun

