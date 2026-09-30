"""
ด่านคลังก่อนส่ง (ผู้ใช้ขอ 30 ก.ย. 2026)

กติกา: ว่างมาว่างไป มีรหัสไหนมาส่งรหัสนั้น — คีย์ upsert ของ Target Sun รวมคลัง ถ้า Target Sun
ตอนนี้มีแถวของคู่พนักงาน×สินค้าคนละคลังกับไฟล์ แถวเดิมไม่ถูกทับ เป้าเบิ้ล (SL453/SL380)

  - ตอนเตรียมไฟล์: เจอ = 409 send_warehouse_conflict ให้ผู้ใช้เลือก「ใช้คลังตาม Target Sun」
  - เลือกแล้ว: หีบของทุกคู่เท่าเดิม (ค่าที่แก้มือไม่หาย ไม่ต้องกระจายใหม่) แค่แตกลงแถวที่มีจริง
  - ตอนส่งจริง: ตรวจซ้ำ ถ้า Target Sun เปลี่ยนระหว่างรอ = ไม่ส่งทีมนั้น
  - รวมภาค: แต่ละ SL ตรวจกับพนักงานในไฟล์ของตัวเอง (รวมคนทีมอื่นที่ติดมา)

ทุกอย่าง mock ห้ามต่อ Target Sun จริง
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.schemas import LakehouseUploadRequest  # noqa: E402
from backend.services import lakehouse as lh  # noqa: E402
from backend.services import targetsun_import as ti  # noqa: E402

logging.disable(logging.CRITICAL)

SUP = "SLWH"
DIMS = {"salestype": "S1", "divisioncode": "D1", "areacode": "10", "provincecode": "P1"}


def _grain(emp, sku, wh, qty):
    return {"emp_id": emp, "sku": sku, "qty": qty, "warehouse_code": wh, **DIMS}


def _snap(rows):
    """rows = [(emp, sku, wh, qty)] → ของที่ _live_target_snapshot คืน"""
    qty_by_key: dict[str, int] = {}
    for emp, sku, wh, qty in rows:
        k = lh._live_target_row_key({
            "PRODUCTCODE": sku, "SALESMANCODE": emp, "SALESTYPE": "S1", "DIVISIONCODE": "D1",
            "AREACODE": "10", "PROVINCECODE": "P1", "WAREHOUSECODE": wh,
        })
        qty_by_key[k] = qty_by_key.get(k, 0) + qty
    by_sku: dict[str, int] = {}
    for emp, sku, wh, qty in rows:
        by_sku[sku] = by_sku.get(sku, 0) + qty
    return {"by_sku": by_sku, "row_count": len(rows), "keys": set(qty_by_key), "qty_by_key": qty_by_key}


def _file(df):
    """(emp, wh) → หีบ ของไฟล์ที่เตรียม"""
    return {
        (str(r.SALESMANCODE), str(r.WAREHOUSECODE)): int(r.QUANTITYCASE)
        for r in df.itertuples(index=False)
    }


class _Base(unittest.TestCase):
    grain = [_grain("E1", "A", "", 5), _grain("E2", "A", "R001", 5)]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        pd.DataFrame(self.grain).to_csv(f"data/tga_lines_{SUP}_2026_08.csv", index=False)
        pd.DataFrame([{"sku": "A", "supervisor_target_boxes": 10, "price_per_box": 1.0}]).to_csv(
            f"data/target_boxes_{SUP}_2026_08.csv", index=False
        )
        self._p = [
            patch.object(ti, "assert_target_snapshot_is_fresh", return_value=None),
            patch.object(lh, "_enrich_emp_dimensions", side_effect=lambda df, rows_raw, **kw: df),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _req(self, allocations=None, **kw):
        # 6/4 = ตัวเลขที่ผู้ใช้แก้มือ (ไม่ใช่สัดส่วน grain 5/5) — ต้องไม่ถูกเปลี่ยน
        return LakehouseUploadRequest(
            sup_id=SUP, target_month=8, target_year=2026, upload_user_code="TESTER",
            allocations=allocations or [
                {"emp_id": "E1", "sku": "A", "allocated_boxes": 6},
                {"emp_id": "E2", "sku": "A", "allocated_boxes": 4},
            ],
            **kw,
        )

    def _prepare(self, live_rows, **kw):
        with patch.object(ti, "_live_target_snapshot", return_value=_snap(live_rows) if live_rows is not None else None):
            return ti.prepare_targetsun_import(self._req(**kw))

    def _built(self, live_rows, **kw):
        """ไฟล์ที่ prepare สร้างจริง (เรียกผ่าน _build_send_file ตัวเดียวกัน)"""
        with patch.object(ti, "_live_target_snapshot", return_value=_snap(live_rows)):
            _c, _f, df, *_rest = ti._build_send_file(self._req(**kw))
        return df


class TestWarehouseGate(_Base):
    def test_same_as_targetsun_passes(self):
        res = self._prepare([("E1", "A", "", 5), ("E2", "A", "R001", 5)])
        self.assertTrue(res["prepare_token"])
        self.assertEqual(res["warehouse_adjusted_pairs"], 0)

    def test_warehouse_changed_in_targetsun_blocks_with_choice(self):
        live = [("E1", "A", "R009", 5), ("E2", "A", "R001", 5)]
        with self.assertRaises(HTTPException) as cm:
            self._prepare(live)
        d = cm.exception.detail
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(d["code"], "send_warehouse_conflict")
        self.assertTrue(d["resolvable"])
        self.assertEqual(d["confirm_field"], "use_targetsun_warehouse")
        self.assertEqual(d["conflict_count"], 1)
        c = d["conflicts"][0]
        self.assertEqual((c["emp_id"], c["sku"]), ("E1", "A"))
        self.assertEqual(c["targetsun_rows"], [{"warehouse": "R009", "boxes": 5}])
        self.assertEqual(c["file_rows"], [{"warehouse": "", "boxes": 6}])

    def test_use_targetsun_warehouse_keeps_boxes(self):
        live = [("E1", "A", "R009", 5), ("E2", "A", "R001", 5)]
        df = self._built(live, use_targetsun_warehouse=True)
        self.assertEqual(_file(df), {("E1", "R009"): 6, ("E2", "R001"): 4},
                         "หีบที่แก้มือ 6/4 ต้องเท่าเดิม แค่ E1 ย้ายไปคลังที่มีใน Target Sun")
        res = self._prepare(live, use_targetsun_warehouse=True)
        self.assertEqual(res["warehouse_adjusted_pairs"], 1)

    def test_extra_row_added_in_targetsun_is_covered(self):
        """มีคนเพิ่มแถว R002 ให้ E1 ใน Target Sun — ไม่อยู่ในไฟล์ = ค้างอยู่ เป้าเบิ้ล"""
        live = [("E1", "A", "", 3), ("E1", "A", "R002", 1), ("E2", "A", "R001", 5)]
        with self.assertRaises(HTTPException):
            self._prepare(live)
        df = self._built(live, use_targetsun_warehouse=True)
        f = _file(df)
        self.assertEqual(set(k for k in f if k[0] == "E1"), {("E1", ""), ("E1", "R002")})
        self.assertEqual(f[("E1", "")] + f[("E1", "R002")], 6)
        self.assertEqual(f[("E2", "R001")], 4)

    def test_zero_leftover_without_new_row_is_fine(self):
        """แถวค้างที่เป็น 0 และไฟล์ทับของเดิมครบ ไม่ทำให้อะไรเบิ้ล"""
        live = [("E1", "A", "", 5), ("E1", "A", "R005", 0), ("E2", "A", "R001", 5)]
        self.assertTrue(self._prepare(live)["prepare_token"])

    def test_pair_not_in_targetsun_is_not_a_conflict(self):
        live = [("E2", "A", "R001", 5)]  # E1 ยังไม่มีแถว = คู่ใหม่ ส่งคลังตาม grain ได้
        self.assertTrue(self._prepare(live)["prepare_token"])

    def test_unreadable_targetsun_does_not_block(self):
        self.assertTrue(self._prepare(None)["prepare_token"])

    def test_changed_between_prepare_and_send_is_not_sent(self):
        res = self._prepare([("E1", "A", "", 5), ("E2", "A", "R001", 5)])
        changed = _snap([("E1", "A", "", 5), ("E1", "A", "R777", 2), ("E2", "A", "R001", 5)])
        with patch.object(ti, "_live_target_snapshot", return_value=changed), \
                patch.object(ti, "team_emp_codes_from_grain", return_value=[]), \
                patch.object(ti, "_post_targetsun_multipart") as post, \
                self.assertRaises(HTTPException) as cm:
            ti.import_prepared_targetsun(self._req(prepare_token=res["prepare_token"]))
        post.assert_not_called()
        self.assertEqual(cm.exception.detail["code"], "send_warehouse_conflict")
        self.assertFalse(cm.exception.detail["resolvable"])


class TestWarehouseGateRegional(_Base):
    """รวมภาค: ไฟล์ของ SLWH มี E9 ของทีม SLOTHER ติดมา (กระจายรวมกัน ส่งแยก SL)"""

    def setUp(self):
        super().setUp()
        pd.DataFrame([_grain("E9", "A", "R100", 3)]).to_csv(
            "data/tga_lines_SLOTHER_2026_08.csv", index=False
        )

    def _alloc(self):
        return [
            {"emp_id": "E1", "sku": "A", "allocated_boxes": 5},
            {"emp_id": "E2", "sku": "A", "allocated_boxes": 3},
            {"emp_id": "E9", "sku": "A", "allocated_boxes": 2},
        ]

    def test_other_team_employee_is_checked_against_targetsun(self):
        live = [("E1", "A", "", 5), ("E2", "A", "R001", 5), ("E9", "A", "R200", 3)]
        with patch.object(ti, "_live_target_snapshot", return_value=_snap(live)) as spy, \
                self.assertRaises(HTTPException) as cm:
            ti.prepare_targetsun_import(self._req(self._alloc(), send_batch_id="b1"))
        self.assertIn("E9", spy.call_args.args[3], "ต้องอ่าน Target Sun ของคนทีมอื่นที่อยู่ในไฟล์ด้วย")
        c = cm.exception.detail["conflicts"]
        self.assertEqual([(x["emp_id"], x["targetsun_rows"][0]["warehouse"]) for x in c], [("E9", "R200")])

    def test_regional_use_targetsun_warehouse_keeps_each_employee_boxes(self):
        live = [("E1", "A", "", 5), ("E2", "A", "R001", 5), ("E9", "A", "R200", 3)]
        df = self._built(live, allocations=self._alloc(), send_batch_id="b1",
                         use_targetsun_warehouse=True)
        self.assertEqual(_file(df), {("E1", ""): 5, ("E2", "R001"): 3, ("E9", "R200"): 2})


if __name__ == "__main__":
    unittest.main()
