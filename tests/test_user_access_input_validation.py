"""
หน้าผู้ใช้: รหัสทีมและภาคที่กรอกต้องถูกรูปแบบ (QA 21 ต.ค. 2026 L4)

เดิม "sl 999x" ถูกบันทึกเป็น "SL 999X" (มีช่องว่าง) และภาค「อิสาน」(พิมพ์ผิด) บันทึกได้ —
ผู้ใช้คนนั้นหาทีมตัวเองไม่เจอ / ไม่ถูกนับในภาค โดยไม่มีอะไรเตือน
"""

from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.routers import admin as admin_router  # noqa: E402


class TestUserplFormat(unittest.TestCase):
    def test_valid_codes_pass(self):
        for code in ("SL397", "SL1001", "SLDEMO1", "NONE"):
            admin_router._check_new_userpl(code)

    def test_spaces_and_wrong_prefix_are_refused(self):
        for code in ("SL 999X", "S L397", "397", "XX397", "SL"):
            with self.subTest(code=code), self.assertRaises(HTTPException) as cm:
                admin_router._check_new_userpl(code)
            self.assertEqual(cm.exception.status_code, 400)


class TestRegion(unittest.TestCase):
    def test_canonical_names_pass_unchanged(self):
        for r in ("กรุงเทพ", "กลาง", "อีสาน", "เหนือ", "ใต้", "ภาคสาธิต"):
            self.assertEqual(admin_router._clean_region(r), r)

    def test_common_variants_are_stored_as_canonical(self):
        self.assertEqual(admin_router._clean_region("ภาคอีสาน"), "อีสาน")
        self.assertEqual(admin_router._clean_region("กทม"), "กรุงเทพ")
        self.assertEqual(admin_router._clean_region(" เหนือ "), "เหนือ")

    def test_typo_is_refused_with_the_allowed_list(self):
        with self.assertRaises(HTTPException) as cm:
            admin_router._clean_region("อิสาน")
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("อีสาน", str(cm.exception.detail))

    def test_not_given_and_empty_are_left_alone(self):
        self.assertIsNone(admin_router._clean_region(None))
        self.assertEqual(admin_router._clean_region(""), "")



class TestDemoTeamsOnlyForDemoAccounts(unittest.TestCase):
    """QA 21 ต.ค. 2026 L3: demoadmin เคยเพิ่มแถว SLDEMO3 ให้อีเมลผู้ใช้จริงได้"""

    def test_demo_team_on_real_email_is_refused(self):
        with self.assertRaises(HTTPException) as cm:
            admin_router._check_demo_pairing("amata.p@sahapat.co.th", "SLDEMO3")
        self.assertEqual(cm.exception.status_code, 400)

    def test_demo_team_on_demo_email_and_real_team_on_real_email_pass(self):
        admin_router._check_demo_pairing("demosuper@sahapat.co.th", "SLDEMO1")
        admin_router._check_demo_pairing("amata.p@sahapat.co.th", "SL397")

if __name__ == "__main__":
    unittest.main()
