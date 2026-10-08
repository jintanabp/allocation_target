"""
ผลตรวจ 7 ต.ค. 2026 ข10 — รายชื่อไม่ต้องตั้งเป้า / การย้ายพนักงาน ที่แอดมินแก้จากเว็บ ต้องเก็บใน data/ ไม่ใช่ config/

config/ อยู่ใน git — deploy แบบ git pull/reset ทับค่าที่ตั้งไว้บน server เงียบ ๆ
ย้ายแบบไม่ต้องทำอะไรเอง: data/ ยังไม่มีไฟล์ = อ่าน config/ เดิมเป็นค่าตั้งต้น · บันทึกครั้งแรกลง data/
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from backend.services import emp_assignment_store as eas
from backend.services import no_target_store as nts


class TestAdminListsInDataDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name
        os.makedirs(os.path.join(root, "config"))
        os.makedirs(os.path.join(root, "data"))
        self.root = root
        self._env = mock.patch.dict(os.environ, {"NO_TARGET_EMPLOYEES_JSON_PATH": "", "EMP_ASSIGNMENTS_JSON_PATH": ""})
        self._env.start()
        self._r1 = mock.patch.object(nts, "_repo_root", return_value=root)
        self._r2 = mock.patch.object(eas, "_repo_root", return_value=root)
        self._r1.start()
        self._r2.start()

    def tearDown(self):
        self._r1.stop()
        self._r2.stop()
        self._env.stop()
        self._tmp.cleanup()

    def _cfg(self, name, doc):
        with open(os.path.join(self.root, "config", name), "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def test_emp_moves_seed_from_config_then_write_to_data(self):
        self._cfg("emp_assignments.json", {"assignments": [{"emp_id": "S516", "from_sup": "SL372", "to_sup": "SL359"}]})
        self.assertEqual([r["emp_id"] for r in eas.read_rows()], ["S516"])        # อ่าน config/ เดิม
        eas.set_assignment("C1", "SL359", from_sup="SL300")
        data_path = os.path.join(self.root, "data", "emp_assignments.json")
        self.assertTrue(os.path.isfile(data_path))                                 # เขียนลง data/
        self.assertEqual(sorted(r["emp_id"] for r in eas.read_rows()), ["C1", "S516"])  # ของเดิมไม่หาย
        with open(os.path.join(self.root, "config", "emp_assignments.json"), encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["assignments"]), 1)                 # config/ ไม่ถูกแตะ

    def test_no_target_seed_from_config_then_write_to_data(self):
        self._cfg("no_target_employees.json", {"employees": [{"super_code": "SL397", "emp_id": "C445"}]})
        rows = nts.read_entries()
        self.assertEqual([r["emp_id"] for r in rows], ["C445"])
        nts.write_entries(rows + [{"super_code": "SL509", "emp_id": "C999"}])
        self.assertTrue(os.path.isfile(os.path.join(self.root, "data", "no_target_employees.json")))
        self.assertEqual(sorted(r["emp_id"] for r in nts.read_entries()), ["C445", "C999"])

    def test_data_file_wins_over_config(self):
        self._cfg("emp_assignments.json", {"assignments": [{"emp_id": "OLD", "to_sup": "SL1"}]})
        with open(os.path.join(self.root, "data", "emp_assignments.json"), "w", encoding="utf-8") as f:
            json.dump({"assignments": [{"emp_id": "NEW", "to_sup": "SL2"}]}, f)
        self.assertEqual([r["emp_id"] for r in eas.read_rows()], ["NEW"])


if __name__ == "__main__":
    unittest.main()
