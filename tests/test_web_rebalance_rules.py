"""
§4.1-7 ปุ่มปรับยอดอัตโนมัติในหน้าเว็บต้องทำตามกฎเดียวกับ backend

- คู่ที่กติกาไม่เคยขายตัดเป็น 0 ห้ามได้เพิ่ม — backend ส่งรายชื่อคู่มาให้ (never_sold_zero_pairs)
- SKU ที่กติกาสั่งเฉลี่ย (no_seller/push_target) แจกเท่ากัน ไม่ใช่ตามประวัติ
- ติ๊ก「ทุกคนอย่างน้อย 1 หีบ」= ดึงคืนไม่ต่ำกว่า 1
- กฎพวกนี้ต้องรอดการโหลดร่างกลับ (snapshot + ร่างในเครื่อง)
คณิตของ spreadIncrease/spreadDecrease เทสต์ด้วย node ที่ tests/logic.test.js
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import data as rd  # noqa: E402
from backend.services import allocation_store, emp_assignment_store  # noqa: E402
from backend.services import optimize as opt  # noqa: E402


def _fn(src: str, name: str) -> str:
    i = src.index(f"function {name}(")
    return src[i:src.index("\n}\n", i)]


class TestZeroPairsPayload(unittest.TestCase):
    def test_grouped_by_sku(self):
        got = opt._zero_pairs_by_sku({("E2", "A"), ("E1|W1", "A"), ("E3", "B")})
        self.assertEqual(got, {"A": ["E1|W1", "E2"], "B": ["E3"]})

    def test_sent_in_optimize_response(self):
        import inspect

        self.assertIn('"never_sold_zero_pairs": _zero_pairs_by_sku(never_sold_pairs_all)',
                      inspect.getsource(opt.run_optimization_service))


class TestFrontendRebalanceRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            cls.src = fh.read().replace("\r\n", "\n")

    def test_rebalance_uses_backend_rules(self):
        body = _fn(self.src, "autoRebalance")
        self.assertIn("zeroKeys.has(_neverSoldZeroKeyOf(a)) || isWhBlocked(a)) return 0", body)
        # คนแยกคลัง: สินค้าที่ไม่มีเป้าที่คลังของแถวนี้ห้ามได้เพิ่ม (8 ต.ค. 2026 — ตรงกับ optimize._wh_blocked_pairs)
        self.assertIn('!e.wh_redirect_skus[skuKey].includes("")', body)
        self.assertIn("evenNeverSold.has(skuKey)", body)
        self.assertIn("cells.map(() => minFloor)", body)

    def test_zero_key_matches_server_or_emp_id(self):
        body = _fn(self.src, "_neverSoldZeroKeyOf")
        self.assertIn("wh ? `${emp}|${wh}` : emp", body)

    def test_both_optimize_paths_store_zero_keys(self):
        self.assertIn("S.neverSoldZeroKeys = _neverSoldZeroKeySet([json]);", self.src)
        self.assertIn("S.neverSoldZeroKeys = _neverSoldZeroKeySet(entries.map(([, j]) => j));", self.src)

    def test_rules_survive_draft_reload(self):
        self.assertIn("neverSoldZeroKeys: [...(S.neverSoldZeroKeys || [])]", _fn(self.src, "saveDraft"))
        self.assertIn("body.never_sold_zero_keys = [...(S.neverSoldZeroKeys || [])]",
                      _fn(self.src, "saveServerAllocationSnapshot"))


CRLF, LF = chr(13) + chr(10), chr(10)


class TestRebalanceTeamFirst(unittest.TestCase):
    """โหมดรวม: เกลี่ยในทีมเดียวกันก่อน (ผู้ใช้ขอ 30 ก.ย. 2026) — ลองบนหน้าเว็บแล้ว:
    A1 (ทีม A) แก้ +4 → เดิมหักจาก B1 (ทีม B ประวัติน้อยกว่า) · ใหม่หักจาก A2 (ทีมเดียวกัน)"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            cls.body = _fn(fh.read().replace(CRLF, LF), "autoRebalance")

    def test_team_target_is_what_engine_gave_the_team(self):
        self.assertIn("allocs.every((a) => a.engine_boxes != null)", self.body)
        self.assertIn("_supervisorCodeForAllocRow(a)", self.body)
        self.assertIn("want - have", self.body)

    def test_only_in_multi_team_views(self):
        self.assertIn("const multiTeam = !!(S.compositeAllocView || S.aggregateMode);", self.body)

    def test_spill_over_is_reported(self):
        self.assertIn("crossTeam.push({ sku, boxes: Math.abs(rest) })", self.body)
        self.assertIn("S.rebalanceCrossTeam = crossTeam;", self.body)


class TestRebalanceMoneyAwareMix(unittest.TestCase):
    """ปุ่มปรับยอดแบบผสม (30 ก.ย. 2026): เติมเฉพาะคนที่มีประวัติขาย โดยให้คนเงินขาดเป้ามากสุดก่อน ·
    หักจากคนเงินเกินเป้ามากสุด · แบ่งเท่า/ไม่มีคนเคยขาย/ไม่มีเป้าเงิน = วิธีเดิม
    ลองบนหน้าเว็บแล้ว: E4 ลดสินค้า B 6 หีบ → E1 ที่เงินขาด 6,000 ได้ครบ 6 หีบ (วิธีเดิมแบ่งตามประวัติ)"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            cls.body = _fn(fh.read().replace(CRLF, LF), "autoRebalance")

    def test_increase_only_to_sellers_by_money(self):
        self.assertIn("(Number(a.hist_avg) || 0) > 0", self.body)
        self.assertIn("AppLogic.moneyFirstIncrease(", self.body)

    def test_decrease_by_money_with_floor(self):
        self.assertIn("AppLogic.moneyFirstDecrease(", self.body)
        self.assertIn("floor: minFloor", self.body)

    def test_falls_back_for_even_or_no_money(self):
        self.assertIn("const moneyOk = !evenSku && price > 0 && cells.some((a) => yellowOf(a) > 0);", self.body)

    def test_money_updates_as_boxes_move(self):
        self.assertIn("valueByKey.set(k, (valueByKey.get(k) || 0) + n * price);", self.body)


class TestSnapshotKeepsRebalanceRules(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data/allocations")
        pd.DataFrame([{"emp_id": "A1", "emp_name": "A1", "super_code": "SLA"}]).to_csv(
            "data/emp_cache_SLA_2026_10.csv", index=False
        )
        self._p = [
            patch.object(allocation_store, "allocations_dir", return_value=os.path.abspath("data/allocations")),
            patch.object(emp_assignment_store, "read_rows", return_value=[]),
            patch.object(rd, "ensure_allocation_write_allowed"),
            patch("backend.services.usage_log_store.log_from_user"),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _put(self, **kw):
        body = rd.AllocationSnapshotBody(
            sup_id="SLA", target_month=10, target_year=2026, status="optimized",
            allocations=[{"emp_id": "A1", "sku": "X", "allocated_boxes": 1}], **kw,
        )
        rd.put_allocation_snapshot(body, user={"email": "s@x.co"})
        return allocation_store.read_snapshot("SLA", 10, 2026)

    def test_saved_with_engine_run(self):
        snap = self._put(engine_yellow={"A1": 5}, never_sold_zero_keys=["A1|X"], force_min_one=True)
        self.assertEqual(snap["never_sold_zero_keys"], ["A1|X"])
        self.assertTrue(snap["force_min_one"])

    def test_kept_when_later_save_has_no_engine_run(self):
        self._put(engine_yellow={"A1": 5}, never_sold_zero_keys=["A1|X"], force_min_one=True)
        snap = self._put(yellow={"A1": 6})
        self.assertEqual(snap["never_sold_zero_keys"], ["A1|X"])
        self.assertTrue(snap["force_min_one"])


if __name__ == "__main__":
    unittest.main()
