"""
ตรึงตรรกะฝั่งหน้าเว็บของ "พนักงานแยกคลัง" ด้วยการอ่านซอร์ส

ใช้วิธีเดียวกับ tests/test_send_order_frontend.py: หน้าเว็บไม่มี build step และไม่มี
test runner ฝั่ง JS ที่เข้าถึง S/DOM ได้ จึงตรึง "โครงสร้างที่ความถูกต้องพึ่งพา" แทน

บั๊กที่กันไว้: แถวผลกระจายของพนักงานที่แยกคลังกลับมาโดยไม่มี warehouse_code
→ คีย์เป็น "C442" ขณะที่รายชื่อที่ใช้ได้เป็น "C442|R408" → ถูกกรองทิ้งทั้งคน
→ autoRebalance ยกหีบไปให้เพื่อน โดยยอดต่อ SKU ยังตรงเป้า จึงไม่มีด่านไหนเห็น
"""

from __future__ import annotations

import os
import re
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from tests.test_send_order_frontend import _function_source_any  # noqa: E402


def _app_js() -> str:
    with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as f:
        return f.read()


class TestWarehouseKeyRepair(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _app_js()

    def test_repair_runs_before_filtering(self):
        body = _function_source_any("_filterAllocationsEligibleOnly")
        self.assertIn("_repairAllocWarehouse", body, "ต้องซ่อมคีย์ก่อนกรอง")
        self.assertLess(
            body.index("_repairAllocWarehouse"), body.index(".filter("),
            "ซ่อมคีย์ต้องมาก่อน .filter( ไม่งั้นแถวถูกทิ้งไปก่อนจะได้ซ่อม",
        )

    def test_repair_refuses_to_guess_between_two_warehouses(self):
        body = _function_source_any("_repairAllocWarehouse")
        self.assertIn("length !== 1", body,
                      "มีคลังที่ใช้ได้มากกว่าหนึ่ง ต้องไม่เดา — เดาผิดแล้วส่งขึ้น Target Sun กู้ไม่ได้")

    def test_repair_has_a_duplicate_guard(self):
        body = _function_source_any("_repairAllocWarehouse")
        self.assertIn("sku", body, "ต้องเทียบ sku ด้วยเพื่อกันเติมคลังแล้วสองแถวชนคีย์เดียวกัน")
        self.assertIn("rowsInBatch", body)

    def test_repair_is_a_noop_when_warehouse_present(self):
        body = _function_source_any("_repairAllocWarehouse")
        head = body[: body.index("const emp")]
        self.assertIn("warehouse_code", head)
        self.assertIn("return a", head, "มีคลังอยู่แล้วต้องคืนทันที = ไม่แตะเส้นทางปกติ")

    def test_eligible_check_has_no_strict_dead_end(self):
        """สาขา 'ไม่มีคลัง' ต้องผ่อนเท่าสาขา 'มีคลัง' ไม่งั้นพนักงานแยกคลังหลุดหมด"""
        body = _function_source_any("_allocRowIsEligible")
        tail = body[body.rindex("eligibleKeys.has(emp)"):]
        self.assertIn("_isAllocEligible", tail,
                      "ท้ายฟังก์ชันต้องมีทางถอยที่ดูจากแถวพนักงานจริง ไม่ใช่จบที่คีย์เปล่า")

    def test_filter_warns_when_rows_are_dropped(self):
        body = _function_source_any("_filterAllocationsEligibleOnly")
        self.assertIn("console.warn", body, "ทิ้งแถวเงียบ ๆ คือเหตุที่บั๊กนี้ซ่อนอยู่นาน")

    def test_every_filter_call_site_goes_through_the_shared_helper(self):
        """กันมีทางใหม่ที่เรียก .filter(_allocRowIsEligible) ตรง ๆ แล้วข้ามตัวซ่อมคีย์"""
        direct = re.findall(r"\.filter\(\s*_allocRowIsEligible\s*\)", self.src)
        self.assertEqual(
            len(direct), 1,
            "ต้องมีที่เดียวคือใน _filterAllocationsEligibleOnly เท่านั้น",
        )


class TestLiveTargetsKeepTheSplit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _app_js()

    def test_live_merge_does_not_overwrite_every_warehouse_row(self):
        body = _function_source_any("loadLiveTargetsFromTargetSun")
        self.assertIn("rowsByEmp", body,
                      "ต้องจัดกลุ่มตามพนักงานก่อน ไม่งั้นเป้ารวมจะถูกเขียนทับลงทุกแถวคลัง")
        self.assertNotIn(
            "emp.target_sun = Number(fresh.target_sun) || 0", body,
            "ห้ามเขียนเป้ารวมทับแถวรายคลังตรง ๆ — เป้าจะถูกนับซ้ำเป็นสองเท่า",
        )

    def test_single_warehouse_path_is_explicit(self):
        body = _function_source_any("loadLiveTargetsFromTargetSun")
        self.assertIn("rows.length === 1", body, "พนักงานคลังเดียวต้องทำงานเหมือนเดิมเป๊ะ")


class TestDraftMergeCarriesWarehouse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _app_js()

    def test_new_rows_carry_the_warehouse(self):
        body = _function_source_any("mergeDraftIncreasedOfficialTargets")
        self.assertIn("warehouse_code:", body,
                      "แถวที่สร้างใหม่ต้องพกคลัง ไม่งั้นกลายเป็นแถวคีย์กำพร้าที่ถูกกรองทิ้ง")
        self.assertIn("_allocResultKey", body, "ต้องคีย์ด้วย emp+คลัง ไม่ใช่ emp เปล่า")


class TestViewOnlyBannerNamesTheWarehouse(unittest.TestCase):
    def test_banner_appends_warehouse_for_split_rows(self):
        src = _app_js()
        i = src.index("empStep1ViewOnlyNotice")
        window = src[i: i + 1200]
        self.assertIn("wh_split", window,
                      "แบนเนอร์ต้องบอกคลังด้วย ไม่งั้นจะดูเหมือนพนักงานทั้งคนถูกตัดออก")


if __name__ == "__main__":
    unittest.main()
