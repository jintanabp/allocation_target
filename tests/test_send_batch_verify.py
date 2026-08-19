"""
ด่านระดับชุด: ยอดรวมของทุกทีมที่ส่งรอบเดียวกัน ต้องเท่าเป้ารวมของทีมเหล่านั้น

โหมดรวมภาคย้ายหีบข้ามทีมได้ตามที่ออกแบบไว้ (I7) ยอดรายทีมจึงไม่ตรงเป้าทีมเป็นเรื่องปกติ
และผู้ใช้กดยืนยันจนชิน สิ่งที่ห้ามเปลี่ยนคือ "ยอดรวมของภาค" — ถ้าเพี้ยนแปลว่าหีบหาย
หรืองอกจริง ไม่ใช่แค่ย้ายที่ จึงไม่มี flag ให้กดข้าม

อีกเรื่องที่ด่านนี้ดูแล: SKU ที่ทีมหนึ่งส่งไม่ได้ ต้องถูกตัดทุกทีมในชุด ไม่งั้นเป้าของ
SKU นั้นทั้งภาคจะครึ่ง ๆ กลาง ๆ
"""

from __future__ import annotations

import inspect
import logging
import os
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.services import lakehouse as lh  # noqa: E402

logging.disable(logging.CRITICAL)


def _meta(sup_id, totals, *, excluded=None, month=8, year=2026, with_totals=True):
    m = {
        "sup_id": sup_id,
        "excluded_skus": list(excluded or []),
        "target_month": month,
        "target_year": year,
    }
    if with_totals:
        m["sku_totals"] = dict(totals)
    return m


class TestVerifySendBatch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)
        self._targets("SLA", {"X": 10, "Y": 4})
        self._targets("SLB", {"X": 20, "Y": 6})

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _targets(self, sup_id, by_sku, month=8, year=2026):
        pd.DataFrame(
            [{"sku": s, "supervisor_target_boxes": b, "price_per_box": 1.0} for s, b in by_sku.items()]
        ).to_csv(f"data/target_boxes_{sup_id}_{year}_{month:02d}.csv", index=False)

    def test_boxes_moved_between_teams_still_passes(self):
        """A ส่ง 25 (เป้า 10) B ส่ง 5 (เป้า 20) — รายทีมเพี้ยนแต่รวมภาคยังเท่าเป้ารวม"""
        res = lh.verify_send_batch([
            _meta("SLA", {"X": 25, "Y": 4}),
            _meta("SLB", {"X": 5, "Y": 6}),
        ])
        self.assertTrue(res["verified"])
        self.assertEqual(res["scope"], "batch")

    def test_batch_total_short_blocks(self):
        with self.assertRaises(HTTPException) as ctx:
            lh.verify_send_batch([
                _meta("SLA", {"X": 10}),
                _meta("SLB", {"X": 15}),   # รวม 25 แต่เป้ารวม 30
            ])
        d = ctx.exception.detail
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(d["code"], "send_batch_total_mismatch")
        self.assertEqual(d["diff_boxes"], -5)
        self.assertEqual(
            d["diffs"][0],
            {"sku": "X", "sending_boxes": 25, "expected_boxes": 30, "diff": -5},
        )

    def test_batch_total_over_also_blocks(self):
        with self.assertRaises(HTTPException) as ctx:
            lh.verify_send_batch([
                _meta("SLA", {"X": 20}),
                _meta("SLB", {"X": 20}),   # รวม 40 แต่เป้ารวม 30
            ])
        self.assertEqual(ctx.exception.detail["diff_boxes"], 10)

    def test_sku_excluded_in_one_team_but_sent_by_another_blocks(self):
        with self.assertRaises(HTTPException) as ctx:
            lh.verify_send_batch([
                _meta("SLA", {"X": 10}, excluded=["Y"]),
                _meta("SLB", {"X": 20, "Y": 6}),
            ])
        d = ctx.exception.detail
        self.assertEqual(d["code"], "send_batch_sku_partial")
        self.assertEqual(d["exclude_skus"], ["Y"])
        self.assertEqual(d["partial"][0], {"sup_id": "SLB", "sku": "Y", "boxes": 6})

    def test_sku_excluded_everywhere_is_exempt_from_the_total_check(self):
        res = lh.verify_send_batch([
            _meta("SLA", {"X": 10}, excluded=["Y"]),
            _meta("SLB", {"X": 20}, excluded=["Y"]),
        ])
        self.assertTrue(res["verified"])
        self.assertEqual(res["excluded_skus"], ["Y"])

    def test_single_team_is_left_to_the_per_team_gate(self):
        """ทีมเดียวไม่มีการย้ายหีบข้ามทีม — ด่านรายทีมถามและผู้ใช้อาจยืนยันไว้แล้ว"""
        res = lh.verify_send_batch([_meta("SLA", {"X": 3})])
        self.assertTrue(res["verified"])
        self.assertEqual(res["scope"], "single_team")

    def test_unreadable_targets_reports_instead_of_false_alarm(self):
        res = lh.verify_send_batch([
            _meta("SLA", {"X": 10}),
            _meta("SLNOFILE", {"X": 20}),
        ])
        self.assertFalse(res["verified"])
        self.assertEqual(res["reason"], "missing_targets")
        self.assertEqual(res["unreadable_sup_ids"], ["SLNOFILE"])

    def test_old_bundle_without_totals_is_reported_not_guessed(self):
        res = lh.verify_send_batch([
            _meta("SLA", {"X": 10}),
            _meta("SLB", {}, with_totals=False),
        ])
        self.assertFalse(res["verified"])
        self.assertEqual(res["reason"], "no_totals")

    def test_mixed_periods_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            lh.verify_send_batch([
                _meta("SLA", {"X": 10}, month=8),
                _meta("SLB", {"X": 20}, month=9),
            ])
        self.assertEqual(ctx.exception.status_code, 400)

    def test_sku_without_a_target_is_ignored(self):
        res = lh.verify_send_batch([
            _meta("SLA", {"X": 10, "ZZZ": 99}),
            _meta("SLB", {"X": 20, "Y": 10}),
        ])
        # Y รวม 10 เท่าเป้ารวม 10, X รวม 30 เท่าเป้ารวม 30, ZZZ ไม่มีเป้า → ข้าม
        self.assertTrue(res["verified"])


class TestBatchGateCannotBeBypassed(unittest.TestCase):
    def test_no_confirm_flag_in_the_batch_check(self):
        src = inspect.getsource(lh.verify_send_batch)
        code = "\n".join(ln.split("#")[0] for ln in src.splitlines())
        self.assertNotIn(
            "confirm", code,
            "ยอดรวมภาคเพี้ยน = หีบหายจริง ห้ามให้กดยืนยันข้าม",
        )

    def test_no_env_escape_hatch(self):
        self.assertNotIn("environ", inspect.getsource(lh.verify_send_batch))


if __name__ == "__main__":
    unittest.main()
