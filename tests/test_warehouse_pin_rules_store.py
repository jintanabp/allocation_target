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
        self.assertEqual(again["rules"][0]["created_by"], "tester@x.com")  # คนที่กดบันทึก

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
        self.assertEqual(idx[("SKU1", "3", "S")][0]["warehouse_code"], "G010")
        self.assertEqual(idx[("SKU2", "3", "S")][0]["warehouse_code"], "G010")
        # sku คนละตัว คนละภาค ต้องไม่ปนกัน
        self.assertNotIn(("SKU1", "4", "S"), idx)

    # ── เงื่อนไขเสริม: หน่วย (salestype) + เฉพาะพนักงาน (emp_ids) — ผู้ใช้ขอ 21 ต.ค. 2026 ──

    def test_old_rules_without_new_fields_read_as_unrestricted(self):
        out = store.write_rules([_rule()])
        self.assertEqual(out["rules"][0]["salestype"], "")
        self.assertEqual(out["rules"][0]["emp_ids"], [])
        cands = store.rules_by_key()[("SKU1", "3", "S")]
        self.assertIsNotNone(store.match_rule(cands, "ANY1", "C"))

    def test_salestype_and_emp_ids_are_normalized(self):
        out = store.write_rules([_rule(salestype="s", emp_ids=[" s345", "S344", "s344"])])
        self.assertEqual(out["rules"][0]["salestype"], "S")
        self.assertEqual(out["rules"][0]["emp_ids"], ["S344", "S345"])

    def test_unknown_salestype_rejected(self):
        with self.assertRaises(ValueError):
            store.write_rules([_rule(salestype="X")])

    def test_salestype_rule_matches_only_that_unit(self):
        store.write_rules([_rule(salestype="S")])
        cands = store.rules_by_key()[("SKU1", "3", "S")]
        self.assertIsNotNone(store.match_rule(cands, "E1", "S"))
        self.assertIsNone(store.match_rule(cands, "E1", "C"))
        self.assertIsNone(store.match_rule(cands, "E1", ""))

    def test_emp_rule_matches_only_listed_employees(self):
        store.write_rules([_rule(salestype="S", emp_ids=["S344", "S345"])])
        cands = store.rules_by_key()[("SKU1", "3", "S")]
        self.assertIsNotNone(store.match_rule(cands, "s344", "S"))
        self.assertIsNone(store.match_rule(cands, "S999", "S"))
        self.assertIsNone(store.match_rule(cands, "S344", "C"))

    def test_more_specific_rule_wins_and_both_can_coexist(self):
        store.write_rules([
            _rule(section="702", wh="R082"),                                   # ทั้งภาค
            _rule(section="702", wh="G020", salestype="S"),                     # เฉพาะเครดิต
            _rule(section="702", wh="G010", salestype="S", emp_ids=["S344"]),   # เฉพาะคน
        ])
        cands = store.rules_by_key()[("SKU1", "3", "S")]
        self.assertEqual(store.match_rule(cands, "S344", "S")["warehouse_code"], "G010")
        self.assertEqual(store.match_rule(cands, "S999", "S")["warehouse_code"], "G020")
        self.assertEqual(store.match_rule(cands, "S999", "C")["warehouse_code"], "R082")

    def test_same_scope_overlapping_employees_rejected(self):
        with self.assertRaises(ValueError):
            store.write_rules([
                _rule(section="702", wh="G010", salestype="S", emp_ids=["S344", "S345"]),
                _rule(section="704", wh="R082", salestype="S", emp_ids=["S345"]),
            ])

    def test_same_scope_disjoint_employees_allowed(self):
        out = store.write_rules([
            _rule(section="702", wh="G010", salestype="S", emp_ids=["S344"]),
            _rule(section="702", wh="R082", salestype="S", emp_ids=["S345"]),
        ])
        self.assertEqual(len(out["rules"]), 2)

    def test_credit_and_van_rules_for_same_sku_allowed(self):
        out = store.write_rules([
            _rule(section="702", wh="G010", salestype="S"),
            _rule(section="702", wh="R082", salestype="C"),
        ])
        self.assertEqual(len(out["rules"]), 2)

    def test_new_rule_records_who_saved_it(self):
        """หน้าเว็บไม่ส่ง created_by มา — ช่อง「ตั้งโดย」ต้องเป็นคนที่กดบันทึก ไม่ใช่ว่าง"""
        out = store.write_rules([_rule()], updated_by="Boss@spc.co.th")
        self.assertEqual(out["rules"][0]["created_by"], "boss@spc.co.th")

    def test_existing_rule_keeps_original_creator_when_someone_else_saves(self):
        first = store.write_rules([_rule()], updated_by="a@spc.co.th")
        kept = dict(first["rules"][0], created_by="", created_at="")  # หน้าเว็บส่งกลับแบบไม่มีสองช่องนี้
        second = store.write_rules(
            [kept, _rule(skus=["SKU9"], area="4")], updated_by="b@spc.co.th"
        )
        by_sku = {r["skus"][0]: r for r in second["rules"]}
        self.assertEqual(by_sku["SKU1"]["created_by"], "a@spc.co.th")
        self.assertEqual(by_sku["SKU1"]["created_at"], first["rules"][0]["created_at"])
        self.assertEqual(by_sku["SKU9"]["created_by"], "b@spc.co.th")

    def test_old_rule_saved_without_creator_stays_blank_not_misattributed(self):
        """กติกาที่บันทึกไว้ก่อนแก้ (created_by ว่าง) ห้ามเดาว่าเป็นของคนที่กดบันทึกรอบนี้"""
        first = store.write_rules([_rule(created_by="")])
        # จำลองไฟล์เดิมบน server ที่ created_by ว่าง
        import json
        path = os.environ["WAREHOUSE_PIN_RULES_PATH"]
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        doc["rules"][0]["created_by"] = ""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        out = store.write_rules([first["rules"][0]], updated_by="b@spc.co.th")
        self.assertEqual(out["rules"][0]["created_by"], "")

    def test_empty_rules_list_is_valid_and_clears_prior_rules(self):
        store.write_rules([_rule()])
        out = store.write_rules([], expected_rev=1)
        self.assertEqual(out["rules"], [])


if __name__ == "__main__":
    unittest.main()
