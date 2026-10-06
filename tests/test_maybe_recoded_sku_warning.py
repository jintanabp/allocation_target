"""
ขั้นที่ 1 เตือนสินค้าที่มีเป้าแต่ทีมไม่เคยขายเลย 2 ปี — อาจเป็นรหัสใหม่แทนรหัสเก่า (แบบสำรวจ D5 / OPEN_ITEMS 6.4)
ผู้ใช้เลือก 6 ต.ค. 2026: แค่เตือน + บอกทางแก้ (แอดมินผูกรหัส) ไม่เพิ่มขั้นตอน
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services.employees import maybe_recoded_sku_warning  # noqa: E402

SKU = pd.DataFrame({"sku": ["N1", "N2", "OLDLINK"], "product_name_thai": ["น้ำปลา 700", None, "ข้าว"]})


class TestMaybeRecodedSkuWarning(unittest.TestCase):
    def test_lists_unlinked_new_skus_with_names_and_fix(self):
        w = maybe_recoded_sku_warning(["N1", "N2", "OLDLINK"], "cy_ly", SKU, {"OLDLINK"})
        self.assertEqual(w["type"], "new_sku_maybe_recoded")
        self.assertEqual(w["skus"], ["N1", "N2"])
        self.assertIn("N1 น้ำปลา 700", w["message"])
        self.assertIn("ผูกรหัส SKU", w["message"])
        self.assertNotIn("OLDLINK", w["message"])

    def test_only_trusts_yearly_files(self):
        self.assertIsNone(maybe_recoded_sku_warning(["N1"], "fallback_hist_window", SKU, set()))
        self.assertIsNone(maybe_recoded_sku_warning(["N1"], "off", SKU, set()))

    def test_nothing_to_say(self):
        self.assertIsNone(maybe_recoded_sku_warning([], "cy_ly", SKU, set()))
        self.assertIsNone(maybe_recoded_sku_warning(["OLDLINK"], "cy_ly", SKU, {"OLDLINK"}))

    def test_long_list_is_capped(self):
        skus = [f"S{i}" for i in range(20)]
        w = maybe_recoded_sku_warning(skus, "cy_ly", pd.DataFrame({"sku": skus}), set(), max_list=5)
        self.assertEqual(len(w["skus"]), 20)
        self.assertIn("และอีก 15 รายการ", w["message"])

    def test_payload_builder_emits_it(self):
        with open(os.path.join(REPO, "backend", "services", "employees.py"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("new_product_skus, new_products_detection_mode = detect_new_product_skus(")
        self.assertIn("maybe_recoded_sku_warning(", src[i:i + 400])

    @unittest.skipUnless(shutil.which("node"), "ไม่มี node ในเครื่องนี้")
    def test_banner_shows_it_under_its_own_heading(self):
        r = subprocess.run(["node", os.path.join(REPO, "tests", "js", "sku_warning_recoded.test.js")],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
