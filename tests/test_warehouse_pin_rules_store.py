"""
กติกาบังคับคลังเดียว — เทสชั้น store (CRUD/CAS) ล้วน ไม่แตะ Fabric/Target Sun

ดู backend/services/warehouse_pin_rules_store.py และแผนที่อนุมัติ (plan file) สำหรับ
บริบทฟีเจอร์เต็ม
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import warehouse_pin_rules_store as store  # noqa: E402

logging.disable(logging.CRITICAL)


def _rule(section="702", area="3", div="S", wh="G010", **kw):
    r = {"section": section, "areacode": area, "divisioncode": div, "warehouse_code": wh}
    r.update(kw)
    return r


class WarehousePinRulesStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_env = os.environ.get("WAREHOUSE_PIN_RULES_PATH")
        os.environ["WAREHOUSE_PIN_RULES_PATH"] = os.path.join(self._tmp.name, "warehouse_pin_rules.json")

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("WAREHOUSE_PIN_RULES_PATH", None)
        else:
            os.environ["WAREHOUSE_PIN_RULES_PATH"] = self._old_env
        self._tmp.cleanup()

    def test_missing_file_is_empty_state_not_a_crash(self):
        state = store.read_state()
        self.assertEqual(state["rules"], [])
        self.assertEqual(state["rev"], 0)

    def test_write_then_read_round_trips(self):
        out = store.write_rules([_rule()], updated_by="tester@x.com")
        self.assertEqual(len(out["rules"]), 1)
        self.assertEqual(out["rev"], 1)
        again = store.read_state()
        self.assertEqual(again["rules"][0]["warehouse_code"], "G010")
        self.assertEqual(again["rules"][0]["created_by"], "")  # ไม่ได้ส่งมา ว่างได้

    def test_id_is_assigned_when_missing(self):
        out = store.write_rules([_rule()])
        self.assertTrue(out["rules"][0]["id"])

    def test_duplicate_section_area_division_rejected(self):
        with self.assertRaises(ValueError):
            store.write_rules([_rule(wh="G010"), _rule(wh="R082")])

    def test_two_different_regions_for_same_section_are_both_allowed(self):
        out = store.write_rules([_rule(area="3"), _rule(area="4")])
        self.assertEqual(len(out["rules"]), 2)

    def test_blank_required_field_rejected(self):
        for field in ("section", "areacode", "divisioncode", "warehouse_code"):
            with self.subTest(field=field):
                bad = _rule()
                bad[field] = ""
                with self.assertRaises(ValueError):
                    store.write_rules([bad])

    def test_cas_conflict_on_stale_rev(self):
        store.write_rules([_rule()])  # rev -> 1
        with self.assertRaises(store.WarehousePinRulesConflict) as ctx:
            store.write_rules([_rule(area="4")], expected_rev=0)
        self.assertEqual(ctx.exception.current["rev"], 1)

    def test_cas_passes_with_correct_expected_rev(self):
        first = store.write_rules([_rule()])
        second = store.write_rules([_rule(), _rule(area="4")], expected_rev=first["rev"])
        self.assertEqual(second["rev"], 2)
        self.assertEqual(len(second["rules"]), 2)

    def test_rules_by_key_indexes_correctly(self):
        store.write_rules([_rule(section="702", area="3", div="S", wh="G010")])
        idx = store.rules_by_key()
        self.assertIn(("702", "3", "S"), idx)
        self.assertEqual(idx[("702", "3", "S")]["warehouse_code"], "G010")

    def test_empty_rules_list_is_valid_and_clears_prior_rules(self):
        store.write_rules([_rule()])
        out = store.write_rules([], expected_rev=1)
        self.assertEqual(out["rules"], [])


if __name__ == "__main__":
    unittest.main()
