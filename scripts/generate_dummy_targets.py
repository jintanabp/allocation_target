"""
generate_dummy_targets.py
─────────────────────────────────────────────────────────────────────
สร้างไฟล์ dummy CSV ที่ supervisor ต้องมีก่อนเริ่มใช้ระบบ:

  data/target_boxes.csv  — sku | price_per_box | supervisor_target_boxes | brand_name_thai | ...
  data/target_sun.csv    — emp_id | target_sun

รัน (offline):
  python generate_dummy_targets.py --manager SL330 --month 4 --year 2026 --offline

รัน (online จาก Fabric):
  python generate_dummy_targets.py --manager SL330 --month 4 --year 2026
"""

import argparse
import random
import os
import logging
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

os.makedirs("data", exist_ok=True)

PRICE_FALLBACK = {
    "624007": 240.00,
    "624015": 212.00,
    "624049": 335.00,
    "624056": 290.00,
    "624114": 212.00,
    "624163": 232.71,
}
FALLBACK_SKUS = list(PRICE_FALLBACK.keys())


# ─────────────────────────────────────────────────────
def generate_offline(manager_code: str, month: int, year: int, seed: int = 42):
    """Mode: offline — สร้าง dummy ล้วนๆ โดยไม่ต่อ Fabric"""
    random.seed(seed)
    logger.info("[OFFLINE] สร้าง dummy สำหรับ %s %d/%d", manager_code, month, year)

    emp_prefix = manager_code[:2]
    emp_list   = [f"{emp_prefix}{i:03d}" for i in range(1, 5)]
    logger.info("emp_list (dummy): %s", emp_list)

    sku_records = []
    for sku, price in PRICE_FALLBACK.items():
        target_boxes = random.randint(30, 600)
        sku_records.append({
            "sku":                     sku,
            "price_per_box":           price,
            "supervisor_target_boxes": target_boxes,
            "brand_name_thai":         "อ.ส.ร.",
            "brand_name_english":      "OSR",
            "product_name_thai":       f"สินค้า {sku}",
        })

    df_sku = pd.DataFrame(sku_records)
    total_box_value = (df_sku["price_per_box"] * df_sku["supervisor_target_boxes"]).sum()
    df_sku.to_csv("data/target_boxes.csv", index=False)
    logger.info("target_boxes.csv: %d SKUs, มูลค่ารวม %.2f บาท", len(df_sku), total_box_value)

    _write_target_sun(emp_list, total_box_value, seed=seed)
    return df_sku


def generate_from_fabric(manager_code: str, month: int, year: int, seed: int = 42):
    """Mode: online — ดึงข้อมูลจาก Fabric แล้วสร้าง dummy target"""
    from backend.fabric_dax_connector import FabricDAXConnector
    random.seed(seed)
    fabric = FabricDAXConnector()

    # ── Step 1: พนักงาน ──────────────────────────────────
    df_emp = fabric.get_employees_by_manager(manager_code)
    if df_emp.empty:
        logger.warning("ไม่พบพนักงานใต้ '%s' → fallback offline", manager_code)
        return generate_offline(manager_code, month, year, seed)

    emp_list = df_emp["emp_id"].tolist()
    logger.info("emp_list: %s", emp_list)

    # ── Step 2: SKU ที่ทีมเคยขาย ─────────────────────────
    try:
        sku_list = fabric.get_skus_sold_by_team(emp_list, month, year, n_months=6)
    except Exception as e:
        logger.warning("get_skus_sold_by_team: %s → fallback SKUs", e)
        sku_list = []

    if not sku_list:
        logger.warning("ไม่พบ SKU ในประวัติ → ใช้ fallback SKUs")
        sku_list = FALLBACK_SKUS

    logger.info("sku_list: %d รายการ: %s%s", len(sku_list), sku_list[:10], "..." if len(sku_list) > 10 else "")

    # ── Step 3: product info ──────────────────────────────
    try:
        df_prod = fabric.get_product_info(sku_list=sku_list)
    except Exception as e:
        logger.warning("get_product_info: %s", e)
        df_prod = pd.DataFrame()

    # ── Step 4: historical 3M ────────────────────────────
    try:
        df_hist = fabric.get_historical_sales(month, year, sku_list=sku_list, emp_list=emp_list)
        logger.info("historical: %d รายการ (emp×sku)", len(df_hist))
    except Exception as e:
        logger.warning("historical: %s", e)
        df_hist = pd.DataFrame()

    # ── Step 5: LY sales ─────────────────────────────────
    try:
        df_ly   = fabric.get_ly_sales(month, year, sku_list=sku_list, emp_list=emp_list)
        ly_map  = dict(zip(df_ly["emp_id"], df_ly["ly_sales"])) if not df_ly.empty else {}
    except Exception as e:
        logger.warning("LY: %s", e)
        ly_map = {}

    # ── Step 6: สร้าง target_boxes ───────────────────────
    sku_records = []
    for sku in sku_list:
        prod_row  = df_prod[df_prod["sku"] == sku] if not df_prod.empty else pd.DataFrame()
        unit_cost = float(prod_row["unit_cost"].iloc[0]) if not prod_row.empty else 0.0
        price     = unit_cost if unit_cost > 0 else PRICE_FALLBACK.get(str(sku), round(random.uniform(200, 400), 2))

        hist_total   = df_hist[df_hist["sku"] == sku]["hist_boxes"].sum() if not df_hist.empty else 0
        avg_3m       = hist_total / 3.0 if hist_total > 0 else random.randint(10, 80)
        target_boxes = max(5, int(avg_3m * random.uniform(1.05, 1.20)))

        sku_records.append({
            "sku":                     str(sku),
            "price_per_box":           round(price, 2),
            "supervisor_target_boxes": target_boxes,
            "brand_name_thai":         str(prod_row["brand_name_thai"].iloc[0])    if not prod_row.empty else "",
            "brand_name_english":      str(prod_row["brand_name_english"].iloc[0]) if not prod_row.empty else "",
            "product_name_thai":       str(prod_row["product_name_thai"].iloc[0])  if not prod_row.empty else "",
        })

    df_sku = pd.DataFrame(sku_records)
    total_box_value = (df_sku["price_per_box"] * df_sku["supervisor_target_boxes"]).sum()
    df_sku.to_csv("data/target_boxes.csv", index=False)
    logger.info("target_boxes.csv: %d SKUs, มูลค่ารวม %.2f บาท", len(df_sku), total_box_value)

    # ── Step 7: target_sun ตามสัดส่วน LY ────────────────
    weights  = [max(1.0, ly_map.get(e, random.uniform(50_000, 200_000))) for e in emp_list]
    _write_target_sun_weighted(emp_list, weights, total_box_value)
    return df_sku


def _write_target_sun(emp_list: list, total_value: float, seed: int = 42):
    """กระจาย total_value ให้พนักงานแบบ random proportional"""
    random.seed(seed + 1)
    weights = [random.uniform(0.5, 1.5) for _ in emp_list]
    _write_target_sun_weighted(emp_list, weights, total_value)


def _write_target_sun_weighted(emp_list: list, weights: list, total_value: float):
    """กระจาย total_value ตาม weights รับประกัน sum == total_value"""
    total_w = sum(weights)
    records, acc = [], 0.0
    for i, (emp, w) in enumerate(zip(emp_list, weights)):
        if i == len(emp_list) - 1:
            ts = round(total_value - acc, 2)
        else:
            ts = round(total_value * w / total_w, 2)
            acc += ts
        records.append({"emp_id": emp, "target_sun": ts})

    df = pd.DataFrame(records)

    # ตรวจสอบ sum
    actual_sum = df["target_sun"].sum()
    deviation  = abs(actual_sum - total_value)
    if deviation > 1.0:
        logger.warning("target_sun sum deviation: %.2f (expected %.2f, diff=%.2f)",
                       actual_sum, total_value, deviation)
    else:
        logger.info("target_sun sum OK: %.2f", actual_sum)

    df.to_csv("data/target_sun.csv", index=False)
    logger.info("target_sun.csv: %d คน, รวม %.2f บาท", len(df), df["target_sun"].sum())
    for _, r in df.iterrows():
        logger.info("  %s: %.2f บาท", r["emp_id"], r["target_sun"])


# ─────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="สร้าง dummy target CSVs สำหรับ Target Allocation")
    parser.add_argument("--manager", default="SL330", help="SuperCode เช่น SL330")
    parser.add_argument("--month",   type=int, default=4,    help="เดือนเป้า (1-12)")
    parser.add_argument("--year",    type=int, default=2026, help="ปีเป้า (ค.ศ.)")
    parser.add_argument("--offline", action="store_true",    help="ไม่ต่อ Fabric")
    parser.add_argument("--seed",    type=int, default=42,   help="Random seed สำหรับ reproducibility")
    args = parser.parse_args()

    if args.offline:
        generate_offline(args.manager, args.month, args.year, seed=args.seed)
    else:
        try:
            generate_from_fabric(args.manager, args.month, args.year, seed=args.seed)
        except Exception as e:
            logger.error("Fabric error: %s → fallback offline", e)
            generate_offline(args.manager, args.month, args.year, seed=args.seed)

    print("\n📁 ไฟล์ที่สร้าง:")
    for f in ["data/target_boxes.csv", "data/target_sun.csv"]:
        if os.path.exists(f):
            df = pd.read_csv(f)
            print(f"  {f}: {len(df)} แถว")