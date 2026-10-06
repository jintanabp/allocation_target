"""
ดูแทนแล้วแก้ค่าในหน้าแอดมิน: updated_by ต้องจดคนกดจริง (OPEN_ITEMS 8.4 — ผลตรวจ 6 ต.ค. 2026 ค3)
"""

from __future__ import annotations

import inspect
import os
import re
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import admin as adm  # noqa: E402


class TestEditorLabel(unittest.TestCase):
    def test_plain_admin(self):
        self.assertEqual(adm._editor_label({"email": "a@x.co"}), "a@x.co")
        self.assertEqual(adm._editor_label({"email": "a@x.co", "acting_admin_email": None}), "a@x.co")

    def test_view_as_records_real_admin(self):
        u = {"email": "boss@x.co", "acting_admin_email": "dev@x.co", "view_as_email": "boss@x.co"}
        self.assertEqual(adm._editor_label(u), "dev@x.co (ดูแทน boss@x.co)")

    def test_same_person_not_duplicated(self):
        self.assertEqual(adm._editor_label({"email": "Dev@x.co", "acting_admin_email": "dev@x.co"}), "Dev@x.co")

    def test_every_admin_write_uses_label(self):
        src = inspect.getsource(adm)
        bad = re.findall(r"updated_by=(?!_editor_label\(admin\))[^,)\n]+", src)
        self.assertEqual(bad, [], "ทุกจุดที่จด updated_by ในหน้าแอดมินต้องใช้ _editor_label(admin)")
        self.assertNotIn('nr["updated_by"] = email', src)


if __name__ == "__main__":
    unittest.main()
