"""
ปุ่ม "อัปเดตลำดับสิทธิ์" ต้องไม่ตัดทีมของผู้จัดการเงียบ ๆ

เกิดขึ้นจริงระหว่างพัฒนา: กดปุ่มครั้งเดียว ผู้จัดการ 8 คนเหลือทีมใต้สังกัดจาก 12 → 1
เพราะแถวของเขาใน user_access.json ไม่มี acc_division/acc_region ระบบจึงคำนวณทีม
กลับมาไม่ได้ — ข้อมูลชุดเดิมมาจาก roster Excel ซึ่ง rebuild ในแอปสร้างขึ้นใหม่ไม่ได้
หน้าจอตอนนั้นขึ้นแค่ "อัปเดตแล้ว" ไม่มีอะไรบอกว่าเพิ่งตัดสิทธิ์ใครไป
"""

from __future__ import annotations

import logging
import os
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers.admin import _shrinking_manager_teams  # noqa: E402

logging.disable(logging.CRITICAL)


def _payload(by_manager):
    return {"by_manager": by_manager}


class TestShrinkDetection(unittest.TestCase):
    def test_no_change_is_not_a_shrink(self):
        p = _payload({"M1": ["A", "B"], "M2": ["C"]})
        self.assertEqual(_shrinking_manager_teams(p, p), [])

    def test_growth_is_not_a_shrink(self):
        before = _payload({"M1": ["A"]})
        after = _payload({"M1": ["A", "B"]})
        self.assertEqual(_shrinking_manager_teams(before, after), [])

    def test_shrink_is_reported_with_numbers(self):
        before = _payload({"M1": list("ABCDEFGHIJKL")})   # 12 ทีม
        after = _payload({"M1": ["A"]})
        out = _shrinking_manager_teams(before, after)
        self.assertEqual(out, [{"manager_code": "M1", "before": 12, "after": 1}])

    def test_manager_disappearing_entirely_counts_as_shrink(self):
        before = _payload({"M1": ["A", "B"]})
        after = _payload({})
        self.assertEqual(
            _shrinking_manager_teams(before, after),
            [{"manager_code": "M1", "before": 2, "after": 0}],
        )

    def test_worst_loss_is_listed_first(self):
        before = _payload({"M1": list("AB"), "M2": list("ABCDEFGHIJKL")})
        after = _payload({"M1": ["A"], "M2": ["A"]})
        out = _shrinking_manager_teams(before, after)
        self.assertEqual(out[0]["manager_code"], "M2", "คนที่เสียเยอะสุดต้องอยู่บนสุด")

    def test_missing_previous_file_does_not_crash(self):
        self.assertEqual(_shrinking_manager_teams({}, _payload({"M1": ["A"]})), [])


class TestRebuildIsGuarded(unittest.TestCase):
    def test_route_blocks_before_writing_anything(self):
        import inspect

        from backend.routers import admin as admin_router

        src = inspect.getsource(admin_router.admin_rebuild_access_hierarchy)
        self.assertIn("_shrinking_manager_teams", src)
        self.assertLess(
            src.index("_shrinking_manager_teams"),
            src.index("write_rows(enriched)"),
            "ต้องตรวจก่อนเขียนไฟล์ ไม่ใช่เขียนทับแล้วค่อยบอก",
        )
        self.assertIn("confirm_shrink", src)

    def test_route_is_dev_only(self):
        import inspect

        from backend.routers import admin as admin_router

        src = inspect.getsource(admin_router.admin_rebuild_access_hierarchy)
        self.assertIn("require_admin_user", src)
        self.assertNotIn("require_admin_scoped", src)


if __name__ == "__main__":
    unittest.main()
