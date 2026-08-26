"""
ปรับราคาให้ตรงกันก่อนรวมภาค: ทำซ้ำกี่รอบ เป้าเงินต้องอยู่ที่เดิม

วิธีแก้ราคาใช้ "บวกส่วนต่าง" ลงไฟล์เป้าเงิน (เพราะคิดใหม่ทั้งก้อนจะทำให้การแยก
ตามคลังของ wh_split หายไป) ซึ่งโดยธรรมชาติแล้วทำซ้ำไม่ได้ · แคช payload มีอายุ
1 ชั่วโมงและยังถือราคาเก่า พอผู้ใช้กดเปิดหน้ารวมภาคซ้ำในชั่วโมงเดียวกัน ระบบก็
ตรวจเจอ "ราคาไม่ตรง" ชุดเดิมแล้วบวกส่วนต่างลงไฟล์อีกรอบ — เปิด 8 ครั้ง เป้าเงิน
รายคนบวม 8 เท่าของส่วนต่างโดยไม่มีใครเห็น (อาการเดียวกับ "เป้าเงินเพี้ยนหลักล้าน"
ที่เคยแก้ไปแล้วรอบหนึ่ง แต่คราวนี้เกิดกับไฟล์ ไม่ใช่ payload)

และด่านกั้น: กระจายรวมภาคที่ปนทั้งทีมเครดิตและทีมรถเงินสดไม่ได้ ต้องหยุดก่อนคำนวณ
"""

import json
import os
import shutil
import tempfile
import unittest

import pandas as pd
from fastapi import HTTPException

from backend.services.employees import (
    reconcile_prices_across_payloads,
    sales_units_of_sups,
)
from backend.services.optimize import reject_mixed_sales_units

MONTH, YEAR = 9, 2026
NEW_PRICE, OLD_PRICE = 352.0, 312.0
SKU = "734046"


class _TempDataDir(unittest.TestCase):
    """โฟลเดอร์ data/ + แคชชั่วคราว — ไม่แตะไฟล์จริงและไม่ยิง Fabric"""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="idem_test_")
        os.makedirs(os.path.join(self._tmpdir, "data"), exist_ok=True)
        self._cache = os.path.join(self._tmpdir, "cache")
        os.makedirs(self._cache, exist_ok=True)
        self._old_env = {
            k: os.environ.get(k)
            for k in (
                "FABRIC_CACHE_DIR",
                "FABRIC_STATIC_CACHE_TTL_SEC",
                "USER_ACCESS_JSON_PATH",
                "EMPLOYEE_PAYLOAD_CACHE_TTL_SEC",
            )
        }
        os.environ["FABRIC_CACHE_DIR"] = self._cache
        os.environ["FABRIC_STATIC_CACHE_TTL_SEC"] = "99999999"
        os.environ["EMPLOYEE_PAYLOAD_CACHE_TTL_SEC"] = "3600"
        self._access = os.path.join(self._tmpdir, "user_access.json")
        with open(self._access, "w", encoding="utf-8") as fh:
            json.dump([], fh)
        os.environ["USER_ACCESS_JSON_PATH"] = self._access
        path = os.path.join(self._cache, f"dim_product_{YEAR}_{MONTH:02d}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "cached_at": "2026-08-25T00:00:00Z",
                "price_asof": f"{YEAR}-{MONTH:02d}-01",
                "rows": [{"sku": SKU, "credit_unit_price": NEW_PRICE,
                          "cash_unit_price": NEW_PRICE}],
                "row_count": 1,
            }, f, ensure_ascii=False)
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._cwd)
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def write_access(self, rows: list[dict]) -> None:
        with open(self._access, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False)

    def make_team(self, sup: str, price: float, qty: int = 20) -> dict:
        pd.DataFrame([
            {"emp_id": "E1", "sku": SKU, "qty": qty, "salestype": "S",
             "divisioncode": "S", "areacode": "", "provincecode": "",
             "warehouse_code": ""},
        ]).to_csv(f"data/tga_lines_{sup}_{YEAR}_{MONTH:02d}.csv", index=False)
        rows = [{
            "sku": SKU, "price_per_box": price, "price_missing": False,
            "price_from_sales_history": False, "supervisor_target_boxes": qty,
            "brand_name_thai": "ปรุงทิพย์", "brand_name_english": "", "section": "",
            "product_name_thai": "", "product_name_english": "",
        }]
        pd.DataFrame(rows).to_csv(
            f"data/target_boxes_{sup}_{YEAR}_{MONTH:02d}.csv", index=False
        )
        pd.DataFrame([{"emp_id": "E1", "target_sun": qty * price}]).to_csv(
            f"data/target_sun_{sup}_{YEAR}_{MONTH:02d}.csv", index=False
        )
        return {
            "_source_sup_id": sup,
            "employees": [{"emp_id": "E1", "supervisor_code": sup,
                           "has_tga_rows": True, "warehouse_code": "",
                           "target_sun": qty * price}],
            "skus": rows,
            "sku_warnings": [],
            "new_product_skus": [],
        }

    def _sun_of(self, sup: str) -> float:
        df = pd.read_csv(
            f"data/target_sun_{sup}_{YEAR}_{MONTH:02d}.csv", dtype={"emp_id": str}
        )
        return float(df["target_sun"].sum())


class TestReconcileIsIdempotent(_TempDataDir):
    def test_running_twice_does_not_move_target_sun_again(self):
        """เปิดหน้ารวมภาคซ้ำต้องไม่บวกส่วนต่างซ้ำลงไฟล์"""
        payloads = [self.make_team("SL397", NEW_PRICE),
                    self.make_team("SL346", OLD_PRICE)]
        report = reconcile_prices_across_payloads(payloads, MONTH, YEAR)
        self.assertTrue(report, "รอบแรกต้องมีการแก้จริง")
        after_first = self._sun_of("SL346")
        self.assertAlmostEqual(after_first, 20 * NEW_PRICE, places=2)

        # รอบสอง: ผู้ใช้กดเปิดอีกครั้ง แต่แคชยังถือราคาเก่า → payload ชุดเดิม
        payloads2 = [self.make_team_from_stale_cache("SL397", NEW_PRICE),
                     self.make_team_from_stale_cache("SL346", OLD_PRICE)]
        reconcile_prices_across_payloads(payloads2, MONTH, YEAR)
        self.assertAlmostEqual(
            self._sun_of("SL346"), after_first, places=2,
            msg="รอบที่สองต้องไม่ขยับเป้าเงินอีก — ไฟล์ถือราคาใหม่อยู่แล้ว",
        )

    def make_team_from_stale_cache(self, sup: str, price: float, qty: int = 20) -> dict:
        """payload ชุดเดิมจากแคช (ราคาเก่า) โดยไม่เขียนไฟล์เป้าทับ"""
        rows = [{
            "sku": SKU, "price_per_box": price, "price_missing": False,
            "price_from_sales_history": False, "supervisor_target_boxes": qty,
            "brand_name_thai": "ปรุงทิพย์", "brand_name_english": "", "section": "",
            "product_name_thai": "", "product_name_english": "",
        }]
        return {
            "_source_sup_id": sup,
            "employees": [{"emp_id": "E1", "supervisor_code": sup,
                           "has_tga_rows": True, "warehouse_code": "",
                           "target_sun": qty * price}],
            "skus": rows,
            "sku_warnings": [],
            "new_product_skus": [],
        }

    def test_fixed_payload_is_written_back_to_cache(self):
        """แคชต้องถือราคาใหม่ ไม่งั้นรอบหน้าหยิบราคาเก่ามาตรวจแล้วบวกซ้ำ"""
        from backend.services.employee_payload_cache import (
            read_cached_employee_payload,
        )

        payloads = [self.make_team("SL397", NEW_PRICE),
                    self.make_team("SL346", OLD_PRICE)]
        reconcile_prices_across_payloads(payloads, MONTH, YEAR)
        cached = read_cached_employee_payload("SL346", MONTH, YEAR)
        self.assertIsNotNone(cached, "ต้องเขียนแคชที่แก้แล้วกลับไป")
        self.assertAlmostEqual(
            float(cached["skus"][0]["price_per_box"]), NEW_PRICE, places=2
        )
        self.assertNotIn("_source_sup_id", cached, "ฟิลด์ภายในไม่ควรตกลงแคช")


class TestMixedSalesUnitGuard(_TempDataDir):
    def test_region_mixing_credit_and_van_is_rejected(self):
        self.write_access([
            {"email": "a@x.co.th", "userpl": "SL397", "acc_unit": "credit"},
            {"email": "b@x.co.th", "userpl": "SL460", "acc_unit": "van"},
        ])
        with self.assertRaises(HTTPException) as cm:
            reject_mixed_sales_units(["SL397", "SL460"], MONTH, YEAR)
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("รถเงินสด", cm.exception.detail)
        self.assertIn("เครดิต", cm.exception.detail)

    def test_single_unit_region_passes(self):
        self.write_access([
            {"email": "a@x.co.th", "userpl": "SL397", "acc_unit": "credit"},
            {"email": "d@x.co.th", "userpl": "SL540", "acc_unit": "credit"},
        ])
        reject_mixed_sales_units(["SL397", "SL540"], MONTH, YEAR)

    def test_unknown_unit_never_blocks(self):
        """ข้อมูลไม่ครบต้องไม่กลายเป็นตัวบล็อกงาน — ของจริง acc_unit ว่างเกือบครึ่ง"""
        self.write_access([
            {"email": "a@x.co.th", "userpl": "SL397", "acc_unit": "credit"},
            {"email": "c@x.co.th", "userpl": "SL346", "acc_unit": None},
        ])
        reject_mixed_sales_units(["SL397", "SL346"], MONTH, YEAR)
        units = sales_units_of_sups(["SL397", "SL346"], MONTH, YEAR)
        self.assertEqual(units["SL346"], "")
        self.assertEqual(units["SL397"], "S")


if __name__ == "__main__":
    unittest.main()
