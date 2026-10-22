"""
ป้าย 🔒 กติกาบังคับคลังในขั้นที่ 3 ต้องตรงกับของที่ส่งจริง (QA 22 ต.ค. 2026)

ตอนส่งจริง lakehouse._apply_warehouse_pin_rules ตรวจ "ทีละขา" (พนักงาน×สินค้า×ภาค×division×หน่วย)
แต่ป้ายบนจอเดิมดูแค่ขาแรกของพนักงาน×สินค้า และหัวคอลัมน์เอาแถวแรกแล้วบอกว่า "เป้าทั้งหมด" ไปคลังนั้น
- คนเดียวมีทั้งแถวเครดิต (S) และรถเงินสด (C) + กติกาเฉพาะ C → ป้ายต้องขึ้น และบอกว่าแค่บางส่วน
- ทุกขาโดนกติกาเดียวกัน → ไม่ใช่บางส่วน
- ป้ายอย่างเดียว: ยอดหีบ/คลังบนจอเหมือนเดิมทุกตัว
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import optimize as opt  # noqa: E402
from backend.services import warehouse_pin_rules_store as store  # noqa: E402

logging.disable(logging.CRITICAL)

SUP = "SLWHPINLBL"
YEAR, MONTH = 2026, 10


def _leg(emp, sku, st, area="3", div="S", wh="G001"):
    return {"emp_id": emp, "sku": sku, "qty": 5, "salestype": st, "divisioncode": div,
            "areacode": area, "provincecode": "P1", "warehouse_code": wh}


class PinLabelFromLegsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        self._old_env = os.environ.get("WAREHOUSE_PIN_RULES_PATH")
        os.environ["WAREHOUSE_PIN_RULES_PATH"] = os.path.join(self._tmp.name, "rules.json")

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("WAREHOUSE_PIN_RULES_PATH", None)
        else:
            os.environ["WAREHOUSE_PIN_RULES_PATH"] = self._old_env
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _grain(self, rows):
        pd.DataFrame(rows).to_csv(f"data/tga_lines_{SUP}_{YEAR}_{MONTH:02d}.csv", index=False)

    def _rules(self, *rules):
        store.write_rules([
            {"section": "702", "skus": r[0], "areacode": "3", "divisioncode": "S",
             "salestype": r[1], "emp_ids": r[2] if len(r) > 2 else [], "warehouse_code": r[3] if len(r) > 3 else "G010"}
            for r in rules
        ])

    def _run(self, df):
        out = opt._apply_wh_pin_preview(df, SUP, MONTH, YEAR)
        return {(e, s): (w, bool(p)) for e, s, w, p in
                zip(out["emp_id"], out["sku"], out["wh_pin_forced"], out["wh_pin_partial"])}, out

    def test_second_leg_matches_unit_rule_label_shows_partial(self):
        # ขาแรก S ไม่โดน ขาที่สอง C โดน — เดิมดูแค่ขาแรก ป้ายไม่ขึ้นเลย ทั้งที่ตอนส่ง C ถูกย้าย
        self._grain([_leg("E1", "SKU1", "S"), _leg("E1", "SKU1", "C")])
        self._rules((["SKU1"], "C"))
        df = pd.DataFrame([{"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 10, "warehouse_code": ""}])
        got, out = self._run(df)
        self.assertEqual(got[("E1", "SKU1")], ("G010", True))
        self.assertEqual(out["allocated_boxes"].tolist(), [10])
        self.assertEqual(out["warehouse_code"].tolist(), [""])

    def test_all_legs_same_rule_not_partial(self):
        self._grain([_leg("E1", "SKU1", "S"), _leg("E1", "SKU1", "C"), _leg("E2", "SKU1", "S")])
        self._rules((["SKU1"], ""))
        df = pd.DataFrame([{"emp_id": e, "sku": "SKU1", "allocated_boxes": 4, "warehouse_code": "G001"} for e in ("E1", "E2")])
        got, _ = self._run(df)
        self.assertEqual(got[("E1", "SKU1")], ("G010", False))
        self.assertEqual(got[("E2", "SKU1")], ("G010", False))

    def test_unmatched_pair_stays_blank(self):
        self._grain([_leg("E1", "SKU1", "S"), _leg("E1", "SKU2", "S")])
        self._rules((["SKU1"], ""))
        df = pd.DataFrame([{"emp_id": "E1", "sku": s, "allocated_boxes": 3, "warehouse_code": "G001"} for s in ("SKU1", "SKU2")])
        got, out = self._run(df)
        self.assertEqual(got[("E1", "SKU2")], ("", False))
        self.assertEqual(sorted(out.groupby("sku")["allocated_boxes"].sum().items()), [("SKU1", 3), ("SKU2", 3)])

    def test_no_rules_has_partial_column(self):
        self._grain([_leg("E1", "SKU1", "S")])
        df = pd.DataFrame([{"emp_id": "E1", "sku": "SKU1", "allocated_boxes": 1, "warehouse_code": "G001"}])
        out = opt._apply_wh_pin_preview(df, SUP, MONTH, YEAR)
        self.assertEqual(out["wh_pin_partial"].tolist(), [False])


class FrontendHeaderBadgeTest(unittest.TestCase):
    """หัวคอลัมน์ต้องสรุปจากทุกแถว ไม่ใช่แถวแรก — รันฟังก์ชันจริงด้วย node"""

    def test_summary_uses_all_rows(self):
        import shutil
        import subprocess

        if not shutil.which("node"):
            self.skipTest("ไม่มี node")
        src = open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8").read()

        def grab(name):
            i = src.index(f"function {name}(")
            return src[i:src.index("\n}\n", i) + 2]

        js = (
            "function escH(s){return String(s);}\n" + grab("_skuWhPinSummary") + grab("_skuWhPinBadgeHtml")
            + r"""
const rows = [
  {sku: "A", wh_pin_forced: "W9", allocated_boxes: 3},
  {sku: "A", wh_pin_forced: "W9", allocated_boxes: 0},
  {sku: "B", wh_pin_forced: "W9", allocated_boxes: 2},
  {sku: "B", wh_pin_forced: "", allocated_boxes: 4},
  {sku: "C", wh_pin_forced: "W9", allocated_boxes: 2},
  {sku: "C", wh_pin_forced: "W8", allocated_boxes: 1},
  {sku: "D", wh_pin_forced: "W9", wh_pin_partial: true, allocated_boxes: 1},
  {sku: "E", wh_pin_forced: "W9", allocated_boxes: 1},
  {sku: "E", wh_pin_forced: "", allocated_boxes: 0},
  {sku: "F", wh_pin_forced: "", allocated_boxes: 5},
];
const m = _skuWhPinSummary(rows);
const out = {};
for (const s of ["A","B","C","D","E","F"]) out[s] = _skuWhPinBadgeHtml(s, m);
console.log(JSON.stringify(out));
"""
        )
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        import json

        out = json.loads(r.stdout)
        text = {k: re.sub(r"<[^>]+>", "", v) for k, v in out.items()}
        self.assertEqual(text["A"], "🔒 W9")
        self.assertIn("เป้าทั้งหมด", out["A"])
        self.assertEqual(text["B"], "🔒 W9 บางส่วน")          # คนที่ไม่โดนกติกายังมีหีบ
        self.assertEqual(text["C"], "🔒 W8, W9")              # ทุกแถวโดน แต่คนละคลัง
        self.assertNotIn("เป้าทั้งหมด", out["C"])
        self.assertEqual(text["D"], "🔒 W9 บางส่วน")          # backend บอกว่าครอบแค่บางขา
        self.assertEqual(text["E"], "🔒 W9")                  # แถวหีบ 0 ที่ไม่โดน ไม่นับ
        self.assertEqual(out["F"], "")

    def test_draft_keeps_pin_label(self):
        src = open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8").read()
        i = src.index("function _slimAllocationsForDraft(")
        body = src[i:src.index("\n}\n", i)]
        self.assertIn("row.wh_pin_forced = String(a.wh_pin_forced)", body)
        self.assertIn("row.wh_pin_partial = true", body)


if __name__ == "__main__":
    unittest.main()
