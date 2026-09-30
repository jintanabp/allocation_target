"""
เป้าต้นทางเปลี่ยน → โหลดเป้าใหม่ แล้วกระจายใหม่เฉพาะสินค้าที่เปลี่ยน (ผู้ใช้ถาม 29 ก.ย. 2026)

บั๊กที่เจอ: ปุ่ม ⚡ ในกล่องตรวจเป้าล่าสุดกระจายใหม่ด้วยเป้าชุดเดิม เพราะ /optimize อ่านเป้า
จากแคชขั้นที่ 1 และการตรวจเป้าไม่ได้อัปเดตแคชนั้น — ตัวเลขไม่ขยับ แล้วส่งก็ถูกบล็อกซ้ำ
"""

from __future__ import annotations

import os
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def _src() -> str:
    with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
        return fh.read().replace("\r\n", "\n")


def _fn(src: str, name: str) -> str:
    i = src.index(f"function {name}(")
    return src[i:src.index("\n}\n", i)]


class TestReloadThenRealloc(unittest.TestCase):
    def test_reload_happens_before_the_partial_run(self):
        body = _fn(_src(), "reloadThenReallocChanged")
        self.assertLess(body.index("await refreshDashboardData(true)"), body.index("await runReAllocationForSkus("))

    def test_drift_notice_button_reloads_first(self):
        body = _fn(_src(), "syncTargetDriftNotice")
        self.assertIn("reloadThenReallocChanged(", body)
        self.assertNotIn('onclick="runReAllocationForSkus(', body)

    def test_send_blocked_notice_offers_the_same_fix(self):
        body = _fn(_src(), "_showStaleTargetNotice")
        self.assertIn("reloadThenReallocChanged(", body)


if __name__ == "__main__":
    unittest.main()
