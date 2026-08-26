"""
รวมภาคที่มีทั้งทีมเครดิตและรถเงินสด: ราคาต่างกันเป็นเรื่องถูกต้อง ห้ามไป "ซ่อม"

SKU เดียวกันมีสองราคาจริง ๆ — รถเงินสดใช้ CASHUNITPRICE เครดิตใช้ CREDITUNITPRICE
และ "กระจายข้ามหน่วยขายไม่ได้" อยู่แล้วตามงานจริง ภาคที่ปนสองหน่วยจึงเป็นสภาพที่ผิด
ไม่ใช่สภาพที่ต้องเกลี่ยให้ลงตัว · เดิมโค้ดฟ้องว่า "ราคาไม่ตรงกัน ให้ไปกดโหลดใหม่"
ซึ่งเป็นคำแนะนำที่ผิด (โหลดกี่ครั้งก็ไม่หาย เพราะราคาสองชุดนั้นถูกทั้งคู่) ตอนนี้
ต้องฟ้องด้วยข้อความของมันเอง แล้วให้ด่านกระจายกั้นไว้ก่อนคำนวณ

อีกด้านหนึ่ง: ตัวปรับราคาข้ามทีมเคยอ่านหน่วยขายจาก acc_unit ใน user_access อย่างเดียว
ซึ่งของจริงว่างเกือบครึ่ง — ทีมที่ไม่รู้หน่วยถูกเหมารวมเป็นกลุ่มเดียวกันแล้วโดนบังคับ
ใช้ราคาเครดิต ทั้งที่บางทีมเป็นรถเงินสดและถือราคาที่ถูกอยู่แล้ว

ทุกเทสอ่าน/เขียนแต่โฟลเดอร์ชั่วคราว — ไม่ยิง Fabric และไม่แตะไฟล์จริงของโปรเจกต์
"""

import contextlib
import json
import os
import shutil
import tempfile
import unittest

import pandas as pd

from backend.services.employees import (
    _group_sku_rows_by_unit,
    _infer_sales_units_from_prices,
    _unit_by_sup_from_payloads,
    merge_employees_payloads,
    reconcile_prices_across_payloads,
)

MONTH, YEAR = 9, 2026
SKU = "734046"
CREDIT_PRICE, CASH_PRICE = 352.0, 300.0
OLD_CREDIT = 312.0

CONFLICT = "aggregate_price_conflict"


def _sku_row(price: float, boxes: int, sku: str = SKU) -> dict:
    return {
        "sku": sku,
        "price_per_box": price,
        "price_missing": False,
        "price_from_sales_history": False,
        "supervisor_target_boxes": boxes,
        "brand_name_thai": "ปรุงทิพย์",
        "brand_name_english": "",
        "section": "",
        "product_name_thai": "",
        "product_name_english": "",
    }


def _payload(sup: str, rows: list[dict], *, unit: str | None = None) -> dict:
    p = {
        "_source_sup_id": sup,
        "employees": [],
        "skus": rows,
        "sku_warnings": [],
        "new_product_skus": [],
    }
    if unit is not None:
        p["sales_unit"] = unit
    return p


def _merge(payloads: list[dict]) -> dict:
    return merge_employees_payloads(
        payloads,
        aggregate_label="ทดสอบรวมภาค",
        aggregate_sup_ids=[p["_source_sup_id"] for p in payloads],
    )


def _revenue(rows: list[dict]) -> float:
    return sum(
        float(r["price_per_box"]) * float(r["supervisor_target_boxes"]) for r in rows
    )


def _warnings_of(out: dict, kind: str) -> list[dict]:
    return [w for w in out["sku_warnings"] if w.get("type") == kind]


class _NoRealUserAccess(unittest.TestCase):
    """กัน _sales_unit_by_sup ไปอ่าน config/user_access.json ตัวจริง"""

    ACCESS_ROWS: list[dict] = []

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self._tmp.name, "user_access.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.ACCESS_ROWS, fh, ensure_ascii=False)
        self._old = os.environ.get("USER_ACCESS_JSON_PATH")
        os.environ["USER_ACCESS_JSON_PATH"] = path

    def tearDown(self):
        if self._old is None:
            os.environ.pop("USER_ACCESS_JSON_PATH", None)
        else:
            os.environ["USER_ACCESS_JSON_PATH"] = self._old
        self._tmp.cleanup()


class TestUnitResolution(_NoRealUserAccess):
    ACCESS_ROWS = [
        {"email": "a@x.co.th", "userpl": "SL397", "acc_unit": "credit"},
        {"email": "b@x.co.th", "userpl": "SL460", "acc_unit": "van"},
        {"email": "c@x.co.th", "userpl": "SL346", "acc_unit": None},
    ]

    def test_stamp_in_payload_wins_over_user_access(self):
        """payload ประทับหน่วยขายที่ resolve ตอนสร้างเป้า (มีตัวถอย Fabric dim ด้วย)"""
        out = _unit_by_sup_from_payloads([
            _payload("SL397", [], unit="C"),      # ไฟล์บอกเครดิต แต่ของจริงรถเงินสด
            _payload("SL460", []),
        ])
        self.assertEqual(out["SL397"], "C", "ตัวประทับต้องชนะ acc_unit ในไฟล์")
        self.assertEqual(out["SL460"], "C", "ไม่มีตัวประทับ → ถอยไปอ่าน user_access")

    def test_missing_acc_unit_is_unknown_not_credit(self):
        out = _unit_by_sup_from_payloads([_payload("SL346", [])])
        self.assertEqual(out["SL346"], "", "acc_unit ว่าง = ไม่รู้ ไม่ใช่เหมาว่าเครดิต")

    def _product_cache(self, tmpdir: str) -> None:
        """แคช Dim_Product ปลอมของงวด — ราคาเครดิตกับเงินสดต่างกันจริงเพื่อให้แยกออก"""
        with open(
            os.path.join(tmpdir, f"dim_product_{YEAR}_{MONTH:02d}.json"),
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump({
                "cached_at": "2026-08-25T00:00:00Z",
                "price_asof": f"{YEAR}-{MONTH:02d}-01",
                "rows": [{"sku": SKU, "credit_unit_price": CREDIT_PRICE,
                          "cash_unit_price": CASH_PRICE}],
                "row_count": 1,
            }, fh, ensure_ascii=False)

    @contextlib.contextmanager
    def _cache_dir(self):
        old = {k: os.environ.get(k)
               for k in ("FABRIC_CACHE_DIR", "FABRIC_STATIC_CACHE_TTL_SEC")}
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["FABRIC_CACHE_DIR"] = tmpdir
            os.environ["FABRIC_STATIC_CACHE_TTL_SEC"] = "99999999"
            self._product_cache(tmpdir)
            try:
                yield tmpdir
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_unknown_unit_is_inferred_from_the_prices_it_holds(self):
        """ทีมรถเงินสดที่ acc_unit ว่าง ต้องเดาได้จากราคาที่ถืออยู่ ไม่ใช่เหมาเป็นเครดิต"""
        with self._cache_dir():
            out = _infer_sales_units_from_prices(
                [_payload("SL346", [_sku_row(CASH_PRICE, 10)])], {"SL346": ""},
                MONTH, YEAR,
            )
            self.assertEqual(out["SL346"], "C", "ราคาตรงคอลัมน์เงินสด = รถเงินสด")

            out2 = _infer_sales_units_from_prices(
                [_payload("SL346", [_sku_row(CREDIT_PRICE, 10)])], {"SL346": ""},
                MONTH, YEAR,
            )
            self.assertEqual(out2["SL346"], "S", "ราคาตรงคอลัมน์เครดิต = เครดิต")

    def test_stale_price_matches_neither_column_stays_unknown(self):
        """ทีมที่ราคาเก่าค้าง เดาหน่วยไม่ได้ ต้องยอมรับว่าไม่รู้ ไม่ใช่เดามั่ว"""
        with self._cache_dir():
            out = _infer_sales_units_from_prices(
                [_payload("SL346", [_sku_row(OLD_CREDIT, 10)])], {"SL346": ""},
                MONTH, YEAR,
            )
            self.assertEqual(out["SL346"], "")


class TestGroupSkuRowsByUnit(unittest.TestCase):
    """ตัวรวมแถว SKU — ต้องแยก "ของเก่าค้าง" ออกจาก "คนละหน่วยขาย" ให้ได้"""

    def test_same_unit_different_price_is_reported_as_stale(self):
        row, conflicts, units = _group_sku_rows_by_unit([
            ("SL397", "S", _sku_row(CREDIT_PRICE, 100)),
            ("SL346", "S", _sku_row(OLD_CREDIT, 50)),
        ])
        self.assertEqual(float(row["supervisor_target_boxes"]), 150.0)
        self.assertIn("SL346", conflicts)
        self.assertEqual({u for u in units.values()}, {"S"})

    def test_cross_unit_difference_is_not_a_stale_price(self):
        """ราคาต่างเพราะคนละหน่วยขาย ห้ามฟ้องว่าไฟล์เก่าค้าง — โหลดใหม่ก็ไม่หาย"""
        row, conflicts, units = _group_sku_rows_by_unit([
            ("SL397", "S", _sku_row(CREDIT_PRICE, 100)),
            ("SL460", "C", _sku_row(CASH_PRICE, 50)),
        ])
        self.assertEqual(conflicts, {}, "ความต่างข้ามหน่วยขายไม่ใช่ conflict")
        self.assertEqual({u for u in units.values()}, {"S", "C"})
        self.assertTrue(row.get("sales_unit_mixed"), "ต้องติดธงว่าเป็นก้อนที่ปนหน่วย")

    def test_result_is_always_one_row_per_sku(self):
        """ทั้งระบบอ้าง SKU ด้วยรหัสเปล่า — คืนสองแถวรหัสซ้ำ เป้าจะหายไปครึ่งเงียบ ๆ"""
        row, _, _ = _group_sku_rows_by_unit([
            ("SL397", "S", _sku_row(CREDIT_PRICE, 100)),
            ("SL460", "C", _sku_row(CASH_PRICE, 50)),
        ])
        self.assertIsInstance(row, dict)
        self.assertEqual(float(row["supervisor_target_boxes"]), 150.0)

    def test_same_price_across_units_is_not_flagged(self):
        row, conflicts, _ = _group_sku_rows_by_unit([
            ("SL397", "S", _sku_row(CREDIT_PRICE, 100)),
            ("SL460", "C", _sku_row(CREDIT_PRICE, 50)),
        ])
        self.assertEqual(conflicts, {})
        self.assertEqual(float(row["supervisor_target_boxes"]), 150.0)

    def test_unknown_unit_joins_the_group_whose_price_matches(self):
        row, conflicts, units = _group_sku_rows_by_unit([
            ("SL397", "S", _sku_row(CREDIT_PRICE, 100)),
            ("SL346", "", _sku_row(CREDIT_PRICE, 30)),
        ])
        self.assertEqual(conflicts, {}, "ราคาตรงกันอยู่แล้ว ไม่มีอะไรต้องเตือน")
        self.assertEqual(float(row["supervisor_target_boxes"]), 130.0)
        self.assertNotIn("SL346", units, "ทีมที่ยังไม่รู้หน่วย ไม่นับเป็นอีกหน่วยหนึ่ง")

    def test_unknown_unit_with_only_one_group_is_treated_as_stale(self):
        """เคสจริงของ SL346 — มีกลุ่มเดียวให้เทียบ แปลว่าราคาเก่าค้าง ต้องเตือน"""
        row, conflicts, _ = _group_sku_rows_by_unit([
            ("SL397", "S", _sku_row(CREDIT_PRICE, 100)),
            ("SL346", "", _sku_row(OLD_CREDIT, 50)),
        ])
        self.assertEqual(float(row["supervisor_target_boxes"]), 150.0)
        self.assertIn("SL346", conflicts)

    def test_zero_price_never_wins_the_row(self):
        """ราคา 0 ของทีมแรกเคยชนะทั้งภาคเงียบ ๆ แล้วมูลค่าหายไปทั้ง SKU"""
        row, _, _ = _group_sku_rows_by_unit([
            ("SL100", "S", _sku_row(0.0, 40)),
            ("SL397", "S", _sku_row(CREDIT_PRICE, 60)),
        ])
        self.assertEqual(float(row["price_per_box"]), CREDIT_PRICE)
        self.assertEqual(float(row["supervisor_target_boxes"]), 100.0)


class TestMergedRegionRevenue(_NoRealUserAccess):
    ACCESS_ROWS = [
        {"email": "a@x.co.th", "userpl": "SL397", "acc_unit": "credit"},
        {"email": "b@x.co.th", "userpl": "SL460", "acc_unit": "van"},
        {"email": "d@x.co.th", "userpl": "SL540", "acc_unit": "credit"},
    ]

    def test_mixed_unit_region_is_flagged_with_its_own_message(self):
        out = _merge([
            _payload("SL397", [_sku_row(CREDIT_PRICE, 100)], unit="S"),
            _payload("SL460", [_sku_row(CASH_PRICE, 50)], unit="C"),
        ])
        self.assertEqual(
            _warnings_of(out, CONFLICT), [],
            "ราคาต่างข้ามหน่วยขายไม่ใช่ของเก่าค้าง ห้ามบอกให้ไปกดโหลดใหม่",
        )
        mixed = _warnings_of(out, "aggregate_mixed_sales_unit")
        self.assertEqual(len(mixed), 1)
        self.assertIn("SL397", mixed[0]["message"])
        self.assertIn("SL460", mixed[0]["message"])

    def test_single_unit_region_looks_exactly_as_before(self):
        out = _merge([
            _payload("SL397", [_sku_row(CREDIT_PRICE, 100)], unit="S"),
            _payload("SL540", [_sku_row(CREDIT_PRICE, 50)], unit="S"),
        ])
        self.assertEqual(len(out["skus"]), 1, "ภาคหน่วยเดียวต้องได้แถวเดียวเหมือนเดิม")
        self.assertEqual(_warnings_of(out, "aggregate_mixed_sales_unit"), [])
        self.assertEqual(float(out["skus"][0]["supervisor_target_boxes"]), 150.0)
        self.assertEqual(
            _revenue(out["skus"]), CREDIT_PRICE * 150,
            "ภาคหน่วยเดียว มูลค่ารวมต้องเท่าผลบวกรายทีมเป๊ะ",
        )

    def test_stale_team_in_same_unit_still_warns(self):
        out = _merge([
            _payload("SL397", [_sku_row(CREDIT_PRICE, 100)], unit="S"),
            _payload("SL540", [_sku_row(OLD_CREDIT, 50)], unit="S"),
        ])
        self.assertEqual(len(_warnings_of(out, CONFLICT)), 1)
        self.assertEqual(_warnings_of(out, "aggregate_mixed_sales_unit"), [])


class TestReconcileRespectsSalesUnit(unittest.TestCase):
    """ตัวปรับราคาข้ามทีมต้องไม่เอาราคาเครดิตไปทับทีมรถเงินสดที่ถูกอยู่แล้ว"""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="unit_recon_")
        os.makedirs(os.path.join(self._tmpdir, "data"), exist_ok=True)
        self._cache = os.path.join(self._tmpdir, "cache")
        os.makedirs(self._cache, exist_ok=True)
        self._old_env = {
            k: os.environ.get(k)
            for k in ("FABRIC_CACHE_DIR", "FABRIC_STATIC_CACHE_TTL_SEC",
                      "USER_ACCESS_JSON_PATH")
        }
        os.environ["FABRIC_CACHE_DIR"] = self._cache
        os.environ["FABRIC_STATIC_CACHE_TTL_SEC"] = "99999999"
        os.environ["USER_ACCESS_JSON_PATH"] = os.path.join(
            self._tmpdir, "user_access.json"
        )
        with open(os.environ["USER_ACCESS_JSON_PATH"], "w", encoding="utf-8") as fh:
            json.dump([], fh)
        with open(
            os.path.join(self._cache, f"dim_product_{YEAR}_{MONTH:02d}.json"),
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump({
                "cached_at": "2026-08-25T00:00:00Z",
                "price_asof": f"{YEAR}-{MONTH:02d}-01",
                "rows": [{"sku": SKU, "credit_unit_price": CREDIT_PRICE,
                          "cash_unit_price": CASH_PRICE}],
                "row_count": 1,
            }, fh, ensure_ascii=False)
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._cwd)
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _team(self, sup: str, price: float, unit: str, boxes: int = 20) -> dict:
        pd.DataFrame([
            {"emp_id": "E1", "sku": SKU, "qty": boxes, "salestype": unit or "S",
             "divisioncode": "S", "areacode": "", "provincecode": "",
             "warehouse_code": ""},
        ]).to_csv(f"data/tga_lines_{sup}_{YEAR}_{MONTH:02d}.csv", index=False)
        rows = [_sku_row(price, boxes)]
        pd.DataFrame(rows).to_csv(
            f"data/target_boxes_{sup}_{YEAR}_{MONTH:02d}.csv", index=False
        )
        pd.DataFrame([{"emp_id": "E1", "target_sun": boxes * price}]).to_csv(
            f"data/target_sun_{sup}_{YEAR}_{MONTH:02d}.csv", index=False
        )
        return {
            "_source_sup_id": sup,
            "sales_unit": unit,
            "employees": [{"emp_id": "E1", "supervisor_code": sup,
                           "has_tga_rows": True, "warehouse_code": "",
                           "target_sun": boxes * price}],
            "skus": rows,
            "sku_warnings": [],
            "new_product_skus": [],
        }

    def test_van_holding_cash_price_is_left_alone(self):
        """ก่อนแก้: SL460 ถูกบังคับใช้ราคาเครดิต ทั้งที่ราคาเงินสดของมันถูกอยู่แล้ว"""
        payloads = [
            self._team("SL397", CREDIT_PRICE, "S"),
            self._team("SL460", CASH_PRICE, "C"),
        ]
        report = reconcile_prices_across_payloads(payloads, MONTH, YEAR)
        self.assertEqual(report, [], "คนละหน่วยขาย = ไม่มีอะไรต้องซ่อม")
        van = next(p for p in payloads if p["_source_sup_id"] == "SL460")
        self.assertEqual(float(van["skus"][0]["price_per_box"]), CASH_PRICE)

    def test_each_unit_group_is_fixed_with_its_own_price_column(self):
        """สองกลุ่มขัดกันพร้อมกัน — เดิมตกไปใช้เครดิตชุดเดียวทับทั้งสองฝั่ง"""
        payloads = [
            self._team("SL397", CREDIT_PRICE, "S"),
            self._team("SL540", OLD_CREDIT, "S"),      # เครดิตราคาเก่าค้าง
            self._team("SL460", CASH_PRICE, "C"),
            self._team("SL509", OLD_CREDIT, "C"),      # เงินสดราคาเก่าค้าง
        ]
        reconcile_prices_across_payloads(payloads, MONTH, YEAR)
        by_sup = {p["_source_sup_id"]: p for p in payloads}
        self.assertEqual(
            float(by_sup["SL540"]["skus"][0]["price_per_box"]), CREDIT_PRICE,
            "ทีมเครดิตที่ค้าง ต้องถูกแก้ด้วยราคาเครดิต",
        )
        self.assertEqual(
            float(by_sup["SL509"]["skus"][0]["price_per_box"]), CASH_PRICE,
            "ทีมรถเงินสดที่ค้าง ต้องถูกแก้ด้วยราคาเงินสด ไม่ใช่ราคาเครดิต",
        )


if __name__ == "__main__":
    unittest.main()
