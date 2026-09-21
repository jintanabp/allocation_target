"""
กติกาบังคับคลังเดียว — เทสชั้น store (CRUD/CAS) ล้วน ไม่แตะ Fabric/Target Sun

คีย์การแมตช์คือ SKU ตรง ๆ (skus: [...]) ไม่ใช่ section — section เป็นแค่ label โชว์บน
ตาราง (ดู docstring บนสุดของ backend/services/warehouse_pin_rules_store.py)
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


def _rule(section="702", skus=None, area="3", div="S", wh="G010", **kw):
    r = {
        "section": section,
        "skus": skus if skus is not None else ["SKU1"],
        "areacode": area,
        "divisioncode": div,
        "warehouse_code": wh,
    }
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
        out = store.write_rules([_rule(skus=["SKU1", "SKU2"])], updated_by="tester@x.com")
        self.assertEqual(len(out["rules"]), 1)
        self.assertEqual(out["rev"], 1)
        again = store.read_state()
        self.assertEqual(again["rules"][0]["warehouse_code"], "G010")
        self.assertEqual(again["rules"][0]["skus"], ["SKU1", "SKU2"])
        self.assertEqual(again["rules"][0]["created_by"], "")  # ไม่ได้ส่งมา ว่างได้

    def test_skus_are_deduped_and_sorted(self):
        out = store.write_rules([_rule(skus=["SKU2", "SKU1", "SKU1", " SKU3 "])])
        self.assertEqual(out["rules"][0]["skus"], ["SKU1", "SKU2", "SKU3"])

    def test_id_is_assigned_when_missing(self):
        out = store.write_rules([_rule()])
        self.assertTrue(out["rules"][0]["id"])

    def test_same_sku_twice_in_the_same_area_division_is_rejected(self):
        """คีย์การแมตช์คือ SKU — SKU เดียวกันสองกติกาในภาค/division เดียวกัน = ไม่รู้คลังไหนชนะ"""
        with self.assertRaises(ValueError):
            store.write_rules([
                _rule(section="702", skus=["SKU1"], wh="G010"),
                _rule(section="704", skus=["SKU1", "SKU9"], wh="R082"),
            ])

    def test_different_skus_in_the_same_section_area_division_are_now_allowed(self):
        """เปลี่ยนจากกติกาเดิม (เคยห้าม section+area+division ซ้ำ) — ตอนนี้แมตช์ด้วย SKU
        ตรง ๆ กติกาสอง SKU คนละตัวในกลุ่ม/ภาค/division เดียวกัน จึงอยู่ร่วมกันได้ปกติ"""
        out = store.write_rules([
            _rule(section="702", skus=["SKU1"], wh="G010"),
            _rule(section="702", skus=["SKU2"], wh="R082"),
        ])
        self.assertEqual(len(out["rules"]), 2)

    def test_two_different_regions_for_the_same_sku_are_both_allowed(self):
        out = store.write_rules([_rule(skus=["SKU1"], area="3"), _rule(skus=["SKU1"], area="4")])
        self.assertEqual(len(out["rules"]), 2)

    def test_blank_required_field_rejected(self):
        for field in ("section", "areacode", "divisioncode", "warehouse_code"):
            with self.subTest(field=field):
                bad = _rule()
                bad[field] = ""
                with self.assertRaises(ValueError):
                    store.write_rules([bad])

    def test_empty_skus_list_is_rejected(self):
        with self.assertRaises(ValueError):
            store.write_rules([_rule(skus=[])])

    def test_cas_conflict_on_stale_rev(self):
        store.write_rules([_rule()])  # rev -> 1
        with self.assertRaises(store.WarehousePinRulesConflict) as ctx:
            store.write_rules([_rule(skus=["SKU9"], area="4")], expected_rev=0)
        self.assertEqual(ctx.exception.current["rev"], 1)

    def test_cas_passes_with_correct_expected_rev(self):
        first = store.write_rules([_rule()])
        second = store.write_rules(
            [_rule(), _rule(section="704", skus=["SKU9"], area="4")], expected_rev=first["rev"]
        )
        self.assertEqual(second["rev"], 2)
        self.assertEqual(len(second["rules"]), 2)

    def test_rules_by_key_indexes_every_sku_in_a_rule(self):
        store.write_rules([_rule(section="702", skus=["SKU1", "SKU2"], area="3", div="S", wh="G010")])
        idx = store.rules_by_key()
        self.assertIn(("SKU1", "3", "S"), idx)
        self.assertIn(("SKU2", "3", "S"), idx)
        self.assertEqual(idx[("SKU1", "3", "S")]["warehouse_code"], "G010")
        self.assertEqual(idx[("SKU2", "3", "S")]["warehouse_code"], "G010")
        # sku คนละตัว คนละภาค ต้องไม่ปนกัน
        self.assertNotIn(("SKU1", "4", "S"), idx)

    def test_empty_rules_list_is_valid_and_clears_prior_rules(self):
        store.write_rules([_rule()])
        out = store.write_rules([], expected_rev=1)
        self.assertEqual(out["rules"], [])


if __name__ == "__main__":
    unittest.main()
