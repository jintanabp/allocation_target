"""
โหมดรวมภาค: ราคาต้องถูกปรับให้ตรงกันทุกทีมก่อนบวกรวม

เจอของจริง (งวด 09/2026): SKU ปรุงทิพย์/เกลือทิพย์ 5 ตัวขึ้นราคาวันที่ 1 ก.ย.
ไฟล์เป้าของ SL346 สร้างไว้ก่อนหน้านั้นจึงยังถือราคาเดือน ส.ค. พอรวมกับทีมอื่น
merge จะบวกแต่หีบ ส่วนราคาใช้ของทีมแรกที่เจอ — ผลรวมก้อนรวมไม่เท่าผลบวกรายทีม
แล้ว revenue_scale ก็ดันเป้าเงินรายคนทั้งภาคตามส่วนต่างนั้น ผลกระจายเลยห่างจาก
เป้าเหลืองเป็นหลักแสนหลักล้าน ทั้งที่ควรห่างแค่หลักพันตามค่า revenue_tolerance

ทุกเทสรันในโฟลเดอร์ชั่วคราว — reconcile เขียนทับไฟล์เป้าจริงในโฟลเดอร์ data/
(ขั้นกระจายอ่านเป้าหีบจากไฟล์ ไม่ใช่จาก payload) จึงห้ามให้แตะของจริงเด็ดขาด
"""

import json
import os
import shutil
import tempfile
import unittest

import pandas as pd

from backend.services.employees import (
    merge_employees_payloads,
    reconcile_prices_across_payloads,
)

MONTH, YEAR = 9, 2026
NEW_PRICE, OLD_PRICE = 352.0, 312.0
SKU = "734046"


def _sku_rows(price: float) -> list[dict]:
    return [
        {"sku": SKU, "price_per_box": price, "price_missing": False,
         "price_from_sales_history": False, "supervisor_target_boxes": 30,
         "brand_name_thai": "ปรุงทิพย์", "brand_name_english": "", "section": "",
         "product_name_thai": "", "product_name_english": ""},
        {"sku": "111294", "price_per_box": 1050.0, "price_missing": False,
         "price_from_sales_history": False, "supervisor_target_boxes": 10,
         "brand_name_thai": "ฟลอเร่", "brand_name_english": "", "section": "",
         "product_name_thai": "", "product_name_english": ""},
    ]


def _totals(payloads: list[dict]) -> tuple[float, float]:
    """(มูลค่าหีบรวม, ผลรวมเป้าเงินรายคน) — สองยอดนี้ต้องเท่ากันเสมอ"""
    boxes = sum(
        float(s["price_per_box"]) * float(s["supervisor_target_boxes"])
        for p in payloads for s in p["skus"]
    )
    sun = sum(float(e["target_sun"]) for p in payloads for e in p["employees"])
    return boxes, sun


def _merged_scale(merged: dict) -> float:
    boxes = sum(
        float(s["price_per_box"]) * float(s["supervisor_target_boxes"])
        for s in merged["skus"]
    )
    sun = sum(float(e["target_sun"]) for e in merged["employees"])
    return boxes / sun if sun else 0.0


class _TempDataDir(unittest.TestCase):
    """โฟลเดอร์ data/ ชั่วคราว + แคชสินค้าปลอม — ไม่แตะไฟล์จริงของโปรเจกต์"""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="recon_test_")
        os.makedirs(os.path.join(self._tmpdir, "data"), exist_ok=True)
        self._cache = os.path.join(self._tmpdir, "cache")
        os.makedirs(self._cache, exist_ok=True)
        self._old_env = {
            k: os.environ.get(k)
            for k in ("FABRIC_CACHE_DIR", "FABRIC_STATIC_CACHE_TTL_SEC")
        }
        os.environ["FABRIC_CACHE_DIR"] = self._cache
        os.environ["FABRIC_STATIC_CACHE_TTL_SEC"] = "99999999"
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._cwd)
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def write_product_cache(self, price: float):
        """แคช Dim_Product ของงวด — แหล่งราคาที่ถือว่าถูกต้อง"""
        path = os.path.join(self._cache, f"dim_product_{YEAR}_{MONTH:02d}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "cached_at": "2026-08-25T00:00:00Z",
                "price_asof": f"{YEAR}-{MONTH:02d}-01",
                "rows": [{"sku": SKU, "credit_unit_price": price}],
                "row_count": 1,
            }, f, ensure_ascii=False)

    def make_team(self, sup: str, price: float, qty_by_emp: dict[str, int]) -> dict:
        """สร้างทีมหนึ่งพร้อมไฟล์แคชครบ — เป้าเงินรายคนคิดจากราคาที่ทีมนั้นถืออยู่"""
        rows = []
        emps = []
        for emp, qty in qty_by_emp.items():
            rows.append({"emp_id": emp, "sku": SKU, "qty": qty, "salestype": "S",
                         "divisioncode": "S", "areacode": "", "provincecode": "",
                         "warehouse_code": ""})
            rows.append({"emp_id": emp, "sku": "111294", "qty": 5, "salestype": "S",
                         "divisioncode": "S", "areacode": "", "provincecode": "",
                         "warehouse_code": ""})
            emps.append({"emp_id": emp, "supervisor_code": sup, "has_tga_rows": True,
                         "warehouse_code": "", "target_sun": round(qty * price + 5 * 1050.0, 2)})
        pd.DataFrame(rows).to_csv(
            f"data/tga_lines_{sup}_{YEAR}_{MONTH:02d}.csv", index=False
        )
        sku_rows = _sku_rows(price)
        sku_rows[0]["supervisor_target_boxes"] = sum(qty_by_emp.values())
        sku_rows[1]["supervisor_target_boxes"] = 5 * len(qty_by_emp)
        pd.DataFrame(sku_rows).to_csv(
            f"data/target_boxes_{sup}_{YEAR}_{MONTH:02d}.csv", index=False
        )
        pd.DataFrame([{"emp_id": e["emp_id"], "target_sun": e["target_sun"]} for e in emps]).to_csv(
            f"data/target_sun_{sup}_{YEAR}_{MONTH:02d}.csv", index=False
        )
        return {"_source_sup_id": sup, "employees": emps, "skus": sku_rows,
                "sku_warnings": [], "new_product_skus": []}


class TestReconcilePrices(_TempDataDir):
    def _two_teams(self):
        """ทีมแรกราคาใหม่ · ทีมที่สองราคาเก่าค้าง (สถานการณ์ที่เจอจริง)"""
        self.write_product_cache(NEW_PRICE)
        fresh = self.make_team("SL397", NEW_PRICE, {"C413": 20})
        stale = self.make_team("SL346", OLD_PRICE, {"C501": 10})
        return [fresh, stale]

    def test_without_reconcile_merged_totals_disagree(self):
        """บันทึกอาการของบั๊ก — ไม่ซ่อมแล้วสองยอดไม่เท่ากัน scale จึงไม่ใช่ 1"""
        payloads = self._two_teams()
        merged = merge_employees_payloads(
            payloads, aggregate_label="รวม", aggregate_sup_ids=["SL397", "SL346"]
        )
        self.assertNotAlmostEqual(_merged_scale(merged), 1.0, places=6)

    def test_reconcile_makes_merged_totals_match(self):
        payloads = self._two_teams()
        report = reconcile_prices_across_payloads(payloads, MONTH, YEAR)
        self.assertEqual([r["status"] for r in report], ["fixed"])
        merged = merge_employees_payloads(
            payloads, aggregate_label="รวม", aggregate_sup_ids=["SL397", "SL346"],
            price_report=report,
        )
        self.assertAlmostEqual(_merged_scale(merged), 1.0, places=9)

    def test_stale_team_target_sun_moves_by_price_delta(self):
        """เป้าเงินรายคนต้องขยับเท่ากับ หีบของคนนั้น × ส่วนต่างราคา ไม่ใช่เกลี่ยมั่ว"""
        payloads = self._two_teams()
        before = next(e["target_sun"] for e in payloads[1]["employees"] if e["emp_id"] == "C501")
        reconcile_prices_across_payloads(payloads, MONTH, YEAR)
        after = next(e["target_sun"] for e in payloads[1]["employees"] if e["emp_id"] == "C501")
        self.assertAlmostEqual(after - before, 10 * (NEW_PRICE - OLD_PRICE), places=2)

    def test_team_totals_stay_self_consistent(self):
        payloads = self._two_teams()
        reconcile_prices_across_payloads(payloads, MONTH, YEAR)
        boxes, sun = _totals(payloads)
        self.assertAlmostEqual(boxes, sun, places=2)

    def test_files_on_disk_are_corrected(self):
        """ขั้นกระจายอ่านเป้าหีบจากไฟล์ — แก้แต่ในหน่วยความจำไม่พอ"""
        payloads = self._two_teams()
        reconcile_prices_across_payloads(payloads, MONTH, YEAR)
        df = pd.read_csv(f"data/target_boxes_SL346_{YEAR}_{MONTH:02d}.csv", dtype={"sku": str})
        price = float(df.loc[df["sku"] == SKU, "price_per_box"].iloc[0])
        self.assertAlmostEqual(price, NEW_PRICE, places=2)
        sun = pd.read_csv(f"data/target_sun_SL346_{YEAR}_{MONTH:02d}.csv", dtype={"emp_id": str})
        self.assertAlmostEqual(
            float(sun.loc[sun["emp_id"] == "C501", "target_sun"].iloc[0]),
            10 * NEW_PRICE + 5 * 1050.0,
            places=2,
        )

    def test_no_conflict_changes_nothing(self):
        self.write_product_cache(NEW_PRICE)
        payloads = [
            self.make_team("SL397", NEW_PRICE, {"C413": 20}),
            self.make_team("SL460", NEW_PRICE, {"C601": 10}),
        ]
        before = _totals(payloads)
        self.assertEqual(reconcile_prices_across_payloads(payloads, MONTH, YEAR), [])
        self.assertEqual(_totals(payloads), before)

    def test_single_team_is_never_touched(self):
        """กระจายทีมเดียวไม่มีอะไรให้เทียบ — ต้องไม่แตะไฟล์ของทีมนั้น"""
        self.write_product_cache(NEW_PRICE)
        payloads = [self.make_team("SL397", OLD_PRICE, {"C413": 20})]
        self.assertEqual(reconcile_prices_across_payloads(payloads, MONTH, YEAR), [])
        df = pd.read_csv(f"data/target_boxes_SL397_{YEAR}_{MONTH:02d}.csv", dtype={"sku": str})
        self.assertAlmostEqual(
            float(df.loc[df["sku"] == SKU, "price_per_box"].iloc[0]), OLD_PRICE, places=2
        )

    def test_falls_back_to_newest_file_when_cache_missing(self):
        """แคชราคาหมดอายุ — ยังตัดสินได้จากไฟล์เป้าที่ใหม่กว่า"""
        fresh = self.make_team("SL397", NEW_PRICE, {"C413": 20})
        stale = self.make_team("SL346", OLD_PRICE, {"C501": 10})
        # ให้ไฟล์ของทีมราคาใหม่ใหม่กว่าชัดเจน
        newer = os.path.getmtime(f"data/target_boxes_SL346_{YEAR}_{MONTH:02d}.csv") + 60
        os.utime(f"data/target_boxes_SL397_{YEAR}_{MONTH:02d}.csv", (newer, newer))
        report = reconcile_prices_across_payloads([fresh, stale], MONTH, YEAR)
        self.assertEqual([r["status"] for r in report], ["fixed"])
        self.assertAlmostEqual(float(stale["skus"][0]["price_per_box"]), NEW_PRICE, places=2)

    def test_missing_tga_rows_leaves_team_alone(self):
        """ไม่มีแถวเป้าดิบ = คิดส่วนต่างรายคนไม่ได้ ต้องไม่แก้ครึ่ง ๆ กลาง ๆ"""
        payloads = self._two_teams()
        os.remove(f"data/tga_lines_SL346_{YEAR}_{MONTH:02d}.csv")
        reconcile_prices_across_payloads(payloads, MONTH, YEAR)
        self.assertAlmostEqual(float(payloads[1]["skus"][0]["price_per_box"]), OLD_PRICE, places=2)
        boxes, sun = _totals(payloads)
        self.assertAlmostEqual(boxes, sun, places=2)


class TestOnlyWritableViewsReconcile(_TempDataDir):
    """
    การซ่อมเขียนทับไฟล์เป้าของทีมอื่น — คนที่แค่เปิดดูต้องไม่ทำให้เกิดขึ้น

    ผู้จัดการที่เปิด「รวมทั้ง division」เป็นมุมมองดูอย่างเดียว (ดู
    _managerAggregateWritable ฝั่งหน้าเว็บ) ถ้าปล่อยให้ซ่อมได้ แค่เปิดดูก็ไปแก้
    ข้อมูลของทีมที่ตัวเองไม่ได้ดูแลโดยไม่รู้ตัว
    """

    def _bulk(self, can_write: bool):
        from unittest.mock import patch

        self.write_product_cache(NEW_PRICE)
        made = {
            "SL397": self.make_team("SL397", NEW_PRICE, {"C413": 20}),
            "SL346": self.make_team("SL346", OLD_PRICE, {"C501": 10}),
        }

        def _fake_payload(sid, month, year, **kw):
            return json.loads(json.dumps(made[sid]))   # สำเนา ไม่ให้แก้ของกลาง

        with patch(
            "backend.services.employees.load_employees_payload", side_effect=_fake_payload
        ):
            from backend.services.employees import load_employees_bulk

            return load_employees_bulk(
                ["SL397", "SL346"], MONTH, YEAR,
                aggregate_label="รวม", can_write=can_write,
            )

    def _stale_price_on_disk(self) -> float:
        df = pd.read_csv(
            f"data/target_boxes_SL346_{YEAR}_{MONTH:02d}.csv", dtype={"sku": str}
        )
        return float(df.loc[df["sku"] == SKU, "price_per_box"].iloc[0])

    def test_read_only_view_leaves_files_untouched(self):
        merged = self._bulk(can_write=False)
        self.assertAlmostEqual(self._stale_price_on_disk(), OLD_PRICE, places=2)
        self.assertEqual(
            [w for w in merged["sku_warnings"] if w["type"] == "aggregate_price_reconciled"],
            [],
        )

    def test_writable_view_fixes_files(self):
        merged = self._bulk(can_write=True)
        self.assertAlmostEqual(self._stale_price_on_disk(), NEW_PRICE, places=2)
        self.assertTrue(
            any(w["type"] == "aggregate_price_reconciled" for w in merged["sku_warnings"])
        )

    def test_read_only_view_still_warns_about_the_mismatch(self):
        """ซ่อมไม่ได้ก็ยังต้องบอกว่ายอดจะเพี้ยน ไม่ใช่เงียบ"""
        merged = self._bulk(can_write=False)
        self.assertTrue(
            any(w["type"] == "aggregate_price_conflict" for w in merged["sku_warnings"])
        )


if __name__ == "__main__":
    unittest.main()
