"""
ซุปบัญชีใหม่ที่ทีมตัวเองยังไม่มีพนักงานใน Fabric (เคส SL545, 28 ต.ค. 2026)

บัญชีตั้งดิวิชัน/ภาคไว้แล้ว จึงได้สิทธิ์ดูทีมอื่นในหน่วยเดียวกัน แต่ตอนล็อกอินระบบ
เปิดทีมตัวเองก่อนเสมอ → 404「ไม่พบพนักงานใต้ SuperCode」→ ล็อกอินหยุด ไม่ถึงตัวสลับทีม
แก้: ถอยไปเปิดทีมแรกในหน่วยเดียวกัน — เฉพาะกรณีทีมไม่มีพนักงานเท่านั้น
"""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services.access_control import filter_managers_payload_for_user  # noqa: E402


class PeersExistWithoutOwnStaff(unittest.TestCase):
    def test_a_supervisor_missing_from_the_hierarchy_still_gets_peers(self):
        """SL545 ไม่อยู่ใน access_hierarchy (ไม่มีพนักงาน) — ทีมเพื่อนต้องยังส่งให้หน้าจอ"""
        full = {
            "rows": [{"supervisor_code": "SL501", "depend_on": "NONE"},
                     {"supervisor_code": "SL502", "depend_on": "NONE"}],
            "supervisors": ["SL501", "SL502"],
            "by_manager": {},
        }
        user = {
            "userpls_supervisor_pick": ["SL545"],
            "userpls_manager_pick": [],
            "allowed_supervisor_codes": {"SL545", "SL501", "SL502"},
            "home_supervisor_codes": {"SL545"},
        }
        out = filter_managers_payload_for_user(full, user)
        self.assertEqual(out["home_supervisor_codes"], ["SL545"])
        self.assertEqual(out["peer_supervisor_codes"], ["SL501", "SL502"])


class LoginFallsBackToAPeer(unittest.TestCase):
    """หน้าเว็บรันในเทสไม่ได้ — ตรวจโครงของโค้ด (ลองพฤติกรรมจริงในเบราว์เซอร์แล้ว)"""

    def setUp(self):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            self.js = fh.read()
        i = self.js.index("function _supervisorEmptyTeamFallback()")
        self.fn = self.js[i:self.js.index("\n}\n", i)]

    def test_only_for_the_no_employees_404(self):
        """ไม่มีเป้า/งวดผิด/server ล่ม ต้องโชว์ error ตามเดิม ไม่ถูกพาไปทีมคนอื่นเงียบ ๆ"""
        self.assertIn('S.loginRole !== "supervisor"', self.fn)
        self.assertIn("fail.status !== 404", self.fn)
        self.assertIn("ไม่พบพนักงานใต้", self.fn)

    def test_never_falls_back_to_itself(self):
        self.assertIn("c !== own", self.fn)

    def test_the_failure_is_recorded_by_loaddata(self):
        i = self.js.index("async function loadData(")
        block = self.js[i:i + 2500]
        self.assertIn("S._lastLoadFail = null", block)
        self.assertIn("S._lastLoadFail = { status: res.status", block)

    def test_login_uses_it_and_tells_the_user(self):
        i = self.js.index("_supervisorEmptyTeamFallback() : \"\"")
        block = self.js[i:i + 900]
        self.assertIn("loadData(S.supId", block)
        self.assertIn("ยังไม่มีพนักงานในระบบ", block)


if __name__ == "__main__":
    unittest.main()
