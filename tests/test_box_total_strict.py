"""
กติกาหลัก (ผู้ใช้ยืนยัน 29 ก.ย. 2026): ยอดหีบรวมหลังกระจายต้องเท่าเป้าที่เข้ามา
ห้ามขาดหรือเกินแม้แต่หีบเดียว — ทั้งกระจายราย SL และกระจายทั้งภาค/ทั้งหน่วย

  - ราย SL: ยอดต่อ SKU ของทีมต้องตรงเป้าทีม
  - ทั้งภาค: ยอดรวมทั้งภาคต่อ SKU ต้องตรงเป้ารวม (รายทีมต่างได้ตาม I7)
  - บันทึกร่างได้แม้ยอดไม่ตรง แต่ส่งไม่ได้ และไม่มีทางยืนยันข้าม

ไฟล์นี้ครอบส่วนที่เพิ่มเข้ามาเพื่อปิดช่องโหว่ — ด่านรายทีมกับด่านระดับชุดเอง
อยู่ใน test_send_target_gate.py และ test_send_batch_verify.py
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.core.allocation_checks import validate_allocation_vs_targets  # noqa: E402
from backend.schemas import LakehouseUploadRequest  # noqa: E402
from backend.services import optimize as opt  # noqa: E402
from backend.services import targetsun_import as ti  # noqa: E402

logging.disable(logging.CRITICAL)


def _sku(rows):
    return pd.DataFrame([{"sku": s, "supervisor_target_boxes": b} for s, b in rows])


def _alloc(rows):
    return pd.DataFrame([{"emp_id": e, "sku": s, "allocated_boxes": b} for e, s, b in rows])


class TestI1Checker(unittest.TestCase):
    def test_exact_match_passes(self):
        self.assertEqual(
            validate_allocation_vs_targets(
                _alloc([("E1", "A", 6), ("E2", "A", 4)]), _sku([("A", 10)])
            ),
            [],
        )

    def test_one_box_short_fails(self):
        out = validate_allocation_vs_targets(
            _alloc([("E1", "A", 6), ("E2", "A", 3)]), _sku([("A", 10)])
        )
        self.assertEqual((out[0]["sku"], out[0]["allocated_sum"]), ("A", 9))

    def test_one_box_over_fails(self):
        out = validate_allocation_vs_targets(
            _alloc([("E1", "A", 6), ("E2", "A", 5)]), _sku([("A", 10)])
        )
        self.assertEqual(out[0]["allocated_sum"], 11)

    def test_fraction_is_not_truncated_away(self):
        """เดิม int(10.6) = 10 ตรงเป้าพอดีแล้วผ่านเงียบ ๆ"""
        out = validate_allocation_vs_targets(
            _alloc([("E1", "A", 6.3), ("E2", "A", 4.3)]), _sku([("A", 10)])
        )
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]["allocated_sum"], 10.6)

    def test_boxes_on_a_sku_without_target_fail(self):
        """เดิมวนจากเป้าอย่างเดียว SKU ที่ไม่มีเป้าแต่มีหีบจึงไม่เคยถูกเห็น"""
        out = validate_allocation_vs_targets(
            _alloc([("E1", "A", 10), ("E1", "Z", 2)]), _sku([("A", 10)])
        )
        self.assertEqual([(o["sku"], o["expected_boxes"]) for o in out], [("Z", 0)])

    def test_zero_rows_on_a_sku_without_target_pass(self):
        self.assertEqual(
            validate_allocation_vs_targets(
                _alloc([("E1", "A", 10), ("E1", "Z", 0)]), _sku([("A", 10)])
            ),
            [],
        )


class TestOptimizeHasNoEscapeHatch(unittest.TestCase):
    def test_env_switch_removed(self):
        src = inspect.getsource(opt)
        code = "\n".join(ln.split("#")[0] for ln in src.splitlines())
        self.assertNotIn("ALLOC_ALLOW_MISMATCH", code)
        self.assertFalse(hasattr(opt, "_allow_allocation_mismatch"))

    def test_partial_merge_is_rechecked_against_full_targets(self):
        """กระจายเฉพาะบาง SKU แล้วรวมผลเดิมกลับ — ต้องตรวจ I1 ทั้งงวดอีกรอบ"""
        src = inspect.getsource(opt)
        merge_at = src.index("_merge_partial_result(result_csv_path")
        after = src[merge_at:merge_at + 1500]
        self.assertIn("validate_allocation_vs_targets(df_to_write, df_sku_full)", after)


class TestFractionalTargetsRejected(unittest.TestCase):
    def test_optimize_rejects_fractional_target_before_the_engine_rounds_it(self):
        src = inspect.getsource(opt)
        at = src.index('"target_boxes_not_integer"')
        self.assertLess(at, src.index("df_sku_full = df_sku"), "ต้องตรวจก่อนเข้าเครื่องคำนวณ")


class TestImportRequiresVerifiedBatch(unittest.TestCase):
    """token ของการส่งรวมภาคต้องผ่านด่านยอดรวมทั้งชุดก่อน — ด่านอยู่ที่ server"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _bundle(self, token, batch_id):
        ti._save_prepare_bundle(
            token, content=b"x", fname="f.xlsx", sup_id="SLA", nrow=1, zero_rows=0,
            dropped_dims=0, not_in_ts=[], upload_user_code=None,
            sku_totals={"X": 5}, target_month=8, target_year=2026,
            send_batch_id=batch_id,
        )
        _, _, meta = ti._load_prepare_bundle(token, "SLA")
        return meta

    def test_single_team_token_needs_no_batch_mark(self):
        meta = self._bundle("t1", None)
        ti._assert_batch_verified("t1", meta)

    def test_batch_token_blocked_until_verified(self):
        meta = self._bundle("t1", "run1")
        with self.assertRaises(HTTPException) as ctx:
            ti._assert_batch_verified("t1", meta)
        self.assertEqual(ctx.exception.detail["code"], "send_batch_not_verified")

    def test_batch_token_passes_after_mark(self):
        m1, m2 = self._bundle("t1", "run1"), self._bundle("t2", "run1")
        m1["prepare_token"], m2["prepare_token"] = "t1", "t2"
        ti.mark_batch_verified([m1, m2])
        _, _, meta = ti._load_prepare_bundle("t1", "SLA")
        self.assertTrue(meta["batch_verified"])
        self.assertEqual(meta["batch_tokens"], ["t1", "t2"])
        ti._assert_batch_verified("t1", meta)

    def test_token_not_in_the_verified_set_is_blocked(self):
        """เตรียมไฟล์ใหม่ของทีมเดิมหลังตรวจชุดแล้ว — ไฟล์ใหม่ยังไม่ผ่านการตรวจ"""
        m1 = self._bundle("t1", "run1")
        m1["prepare_token"] = "t1"
        ti.mark_batch_verified([m1])
        _, _, meta = ti._load_prepare_bundle("t1", "SLA")
        meta["batch_tokens"] = ["other"]
        with self.assertRaises(HTTPException):
            ti._assert_batch_verified("t1", meta)

    def test_import_checks_the_mark_before_posting(self):
        src = inspect.getsource(ti.import_prepared_targetsun)
        self.assertLess(
            src.index("_assert_batch_verified("), src.index("_post_targetsun_multipart("),
        )

    def test_legacy_one_shot_path_refuses_batch_sends(self):
        req = LakehouseUploadRequest(
            sup_id="SLA", target_month=8, target_year=2026, send_batch_id="run1",
        )
        with self.assertRaises(HTTPException) as ctx:
            ti.import_allocations_to_targetsun(req)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_router_marks_batch_after_verify(self):
        from backend.routers import lakehouse as rl

        src = inspect.getsource(rl.verify_send_batch_before_import)
        self.assertLess(src.index("verify_send_batch(metas)"), src.index("mark_batch_verified("))

    def test_bundle_records_batch_fields(self):
        self._bundle("t1", "run1")
        with open("data/ts_prepare/t1.json", encoding="utf-8") as fh:
            meta = json.load(fh)
        self.assertEqual(meta["send_batch_id"], "run1")
        self.assertIs(meta["batch_verified"], False)
        self.assertIn("full_send", meta)


if __name__ == "__main__":
    unittest.main()
