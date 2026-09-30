"""
รวมทีม/รวมภาค: กระจายเฉพาะบางแบรนด์ / เฉพาะสินค้าที่เป้าเปลี่ยนได้ (ผู้ใช้ขอ 29 ก.ย. 2026)

เดิมปิดทุกทางในมุมมองรวมภาค ทั้งที่ /optimize รับ only_skus ได้ทั้งสองแบบของรวมภาค
และการบันทึกแยกทีมรองรับอยู่แล้ว · มุมมองที่แก้ไม่ได้ต้องยังปิดอยู่
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def _src() -> str:
    with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
        return fh.read().replace("\r\n", "\n")


def _fn(src: str, name: str) -> str:
    i = src.index(f"function {name}(")
    return src[i:src.index("\n}\n", i)]


class TestRegionalPartialRealloc(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "ไม่มี node ในเครื่องนี้")
    def test_merge_keeps_rows_of_teams_not_in_the_result(self):
        r = subprocess.run(
            ["node", os.path.join(REPO, "tests", "js", "partial_merge.test.js")],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_guard_allows_writable_regional_view_only(self):
        body = _fn(_src(), "_canPartialRealloc")
        self.assertIn("_isAllocReadOnlyView()", body)
        self.assertIn("_regionalAggregateWritable()", body)

    def test_entry_points_use_the_new_guard(self):
        src = _src()
        for name in ("openAllocPickModal", "runReAllocationForSkus", "reloadThenReallocChanged",
                     "syncAllocExtraButtons", "syncTargetDriftNotice"):
            with self.subTest(fn=name):
                body = _fn(src, name)
                self.assertIn("_canPartialRealloc()", body)
                self.assertNotIn("S.compositeAllocView ||", body.split("_canPartialRealloc()")[0][-200:])

    def test_regional_reload_uses_the_region_loader(self):
        body = _fn(_src(), "reloadThenReallocChanged")
        self.assertIn("refreshManagerDashboardData({ refresh: true })", body)


if __name__ == "__main__":
    unittest.main()
