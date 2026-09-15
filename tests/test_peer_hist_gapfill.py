"""
เติมประวัติของทีม peer สำหรับ SKU ที่ทีมนั้น "ไม่เคยมีเป้าเงินเองมาก่อน" (โหมดรวมภาค/หน่วย)

โจทย์จริง (SL341/SKU 426544, ก.ย. 2026): โหมดรวมภาคอ่านประวัติจาก cache ของทุกทีม
ใน hist_sup_ids อยู่แล้ว แต่ cache ของแต่ละทีมถูกสร้างตอน Step-1 โดยดึงประวัติเฉพาะ SKU
ที่ "ทีมนั้นเองมีเป้าเงิน" เท่านั้น — SKU ที่มีเป้าอยู่ที่ทีมอื่นในกลุ่มเดียวกันจึงไม่เคยถูก
ดึงให้ทีมนี้เลย พนักงานทีมนี้เลยดูเหมือน "ไม่เคยขาย" ทั้งที่ Fabric มีประวัติจริง

ดู docs/next-plan-2026-09.md "ทำ A เลย" และ backend/services/optimize.py
_fill_missing_peer_hist / _peer_target_skus

เทสต์นี้ไม่แตะเน็ตเลย — ใช้ fake fabric connector จำลอง get_historical_sales
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import optimize as opt  # noqa: E402

logging.disable(logging.CRITICAL)

MONTH, YEAR = 9, 2026


class _FakeFabric:
    """จำลอง FabricDAXConnector.get_historical_sales — บันทึกทุกคำขอไว้ตรวจสอบ"""

    def __init__(self, rows: list[dict] | None = None):
        self._rows = rows or []
        self.calls: list[dict] = []
        self.raise_on_call = False

    def get_historical_sales(self, target_month, target_year, sku_list=None, emp_list=None, n_months=3):
        if self.raise_on_call:
            raise RuntimeError("fabric ล่มจำลอง")
        self.calls.append(
            {
                "target_month": target_month,
                "target_year": target_year,
                "sku_list": list(sku_list or []),
                "emp_list": list(emp_list or []),
                "n_months": n_months,
            }
        )
        matched = [
            r
            for r in self._rows
            if r["sku"] in (sku_list or []) and r["emp_id"] in (emp_list or []) and r.get("n_months", n_months) == n_months
        ]
        if not matched:
            return pd.DataFrame(columns=["emp_id", "sku", "hist_boxes", "hist_amount"])
        return pd.DataFrame(
            [
                {"emp_id": r["emp_id"], "sku": r["sku"], "hist_boxes": r["hist_boxes"], "hist_amount": r.get("hist_amount", 0.0)}
                for r in matched
            ]
        )


class _TempDataDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    @staticmethod
    def _write_target_boxes(sid: str, skus: list[str]):
        pd.DataFrame(
            [{"sku": s, "supervisor_target_boxes": 5, "price_per_box": 10.0} for s in skus]
        ).to_csv(f"data/target_boxes_{sid}_{YEAR}_{MONTH:02d}.csv", index=False)

    @staticmethod
    def _write_hist_cache(sid: str, n_months: int, rows: list[dict]):
        from backend.core.paths import hist_cache_path

        path = hist_cache_path(sid, MONTH, YEAR, n_months=n_months)
        pd.DataFrame(rows or [], columns=["emp_id", "sku", "hist_boxes"]).to_csv(path, index=False)

    @staticmethod
    def _read_hist_cache(sid: str, n_months: int) -> pd.DataFrame:
        from backend.core.paths import hist_cache_path

        path = hist_cache_path(sid, MONTH, YEAR, n_months=n_months)
        if not os.path.exists(path):
            return pd.DataFrame(columns=["emp_id", "sku", "hist_boxes"])
        return pd.read_csv(path, dtype={"sku": str, "emp_id": str})


class TestPeerTargetSkus(_TempDataDir):
    def test_none_when_team_has_no_target_boxes_file(self):
        self.assertIsNone(opt._peer_target_skus("SLNOPE", MONTH, YEAR))

    def test_returns_the_teams_own_sku_set(self):
        self._write_target_boxes("SLA", ["A", "B"])
        self.assertEqual(opt._peer_target_skus("SLA", MONTH, YEAR), {"A", "B"})


class TestFillMissingPeerHist(_TempDataDir):
    def setUp(self):
        super().setUp()
        # SLA มีเป้าเองแค่ SKU A · SLB มีเป้าเองแค่ SKU B — จักรวาลรวม {A, B}
        self._write_target_boxes("SLA", ["A"])
        self._write_target_boxes("SLB", ["B"])
        self._write_hist_cache("SLA", 3, [{"emp_id": "E1", "sku": "A", "hist_boxes": 10}])
        self._write_hist_cache("SLB", 3, [{"emp_id": "E2", "sku": "B", "hist_boxes": 4}])

    def _emp_by_sup(self):
        return {"SLA": ["E1"], "SLB": ["E2"]}

    def test_fetches_only_the_real_gap_per_team(self):
        fabric = _FakeFabric(
            rows=[
                {"emp_id": "E1", "sku": "B", "hist_boxes": 7, "n_months": 3},
                {"emp_id": "E2", "sku": "A", "hist_boxes": 3, "n_months": 3},
            ]
        )
        opt._fill_missing_peer_hist(
            fabric, ["SLA", "SLB"], self._emp_by_sup(), {"A", "B"},
            MONTH, YEAR, n_months=3, sku_links=[],
        )
        self.assertEqual(len(fabric.calls), 2)
        by_emp = {c["emp_list"][0]: c for c in fabric.calls}
        self.assertEqual(by_emp["E1"]["sku_list"], ["B"], "SLA ขาดแค่ B (มีเป้า A เองอยู่แล้ว)")
        self.assertEqual(by_emp["E2"]["sku_list"], ["A"], "SLB ขาดแค่ A (มีเป้า B เองอยู่แล้ว)")

    def test_gap_rows_are_appended_to_the_teams_own_cache_file(self):
        fabric = _FakeFabric(
            rows=[{"emp_id": "E1", "sku": "B", "hist_boxes": 7, "n_months": 3}]
        )
        opt._fill_missing_peer_hist(
            fabric, ["SLA", "SLB"], self._emp_by_sup(), {"A", "B"},
            MONTH, YEAR, n_months=3, sku_links=[],
        )
        out = self._read_hist_cache("SLA", 3)
        self.assertEqual(sorted(out["sku"].tolist()), ["A", "B"])
        self.assertEqual(
            int(out.loc[out["sku"] == "B", "hist_boxes"].iloc[0]), 7,
            "แถวเดิม (A) ต้องยังอยู่ แถวใหม่ (B) ต้องถูกต่อเข้าไป ไม่ใช่เขียนทับไฟล์",
        )

    def test_no_real_gap_means_zero_fabric_calls(self):
        """ทีมเดียว (ไม่มี peer) ไม่มีช่องว่างให้เติมเลย — ต้องไม่ยิง Fabric"""
        fabric = _FakeFabric()
        opt._fill_missing_peer_hist(
            fabric, ["SLA"], {"SLA": ["E1"]}, {"A"},
            MONTH, YEAR, n_months=3, sku_links=[],
        )
        self.assertEqual(fabric.calls, [])

    def test_sku_already_present_in_cache_is_not_refetched(self):
        """ช่องว่างที่เคยเติมสำเร็จไปแล้วรอบก่อน (มีอยู่ในไฟล์แล้ว) ต้องไม่ยิงซ้ำ"""
        self._write_hist_cache(
            "SLA", 3, [
                {"emp_id": "E1", "sku": "A", "hist_boxes": 10},
                {"emp_id": "E1", "sku": "B", "hist_boxes": 7},
            ],
        )
        fabric = _FakeFabric()
        opt._fill_missing_peer_hist(
            fabric, ["SLA", "SLB"], self._emp_by_sup(), {"A", "B"},
            MONTH, YEAR, n_months=3, sku_links=[],
        )
        by_emp = {c["emp_list"][0]: c for c in fabric.calls}
        self.assertNotIn("E1", by_emp, "SLA ไม่มีช่องว่างเหลือแล้ว ไม่ควรยิง Fabric อีก")

    def test_team_without_a_target_boxes_file_is_skipped_quietly(self):
        fabric = _FakeFabric()
        opt._fill_missing_peer_hist(
            fabric, ["SLNOPE"], {"SLNOPE": ["E9"]}, {"A", "B"},
            MONTH, YEAR, n_months=3, sku_links=[],
        )
        self.assertEqual(fabric.calls, [])

    def test_team_with_no_known_employees_is_skipped(self):
        """SLB ไม่มีรายชื่อพนักงานให้ query — ต้องข้ามอย่างปลอดภัย (SLA ยังมีช่องว่างของตัวเอง)"""
        fabric = _FakeFabric()
        opt._fill_missing_peer_hist(
            fabric, ["SLA", "SLB"], {"SLA": ["E1"]}, {"A", "B"},
            MONTH, YEAR, n_months=3, sku_links=[],
        )
        self.assertEqual(len(fabric.calls), 1)
        self.assertEqual(fabric.calls[0]["emp_list"], ["E1"])

    def test_fabric_failure_does_not_raise(self):
        fabric = _FakeFabric()
        fabric.raise_on_call = True
        opt._fill_missing_peer_hist(
            fabric, ["SLA", "SLB"], self._emp_by_sup(), {"A", "B"},
            MONTH, YEAR, n_months=3, sku_links=[],
        )  # ต้องไม่ raise

    def test_none_fabric_is_a_no_op(self):
        opt._fill_missing_peer_hist(
            None, ["SLA", "SLB"], self._emp_by_sup(), {"A", "B"},
            MONTH, YEAR, n_months=3, sku_links=[],
        )  # ต้องไม่ raise


class TestGapFillIsWiredIntoOptimize(unittest.TestCase):
    """โค้ดต้องเรียกใช้จริงตรงจุดที่ถูกต้อง ไม่ใช่แค่มีฟังก์ชันลอย ๆ"""

    def test_wired_before_3m_and_6m_cache_read(self):
        import inspect

        src = inspect.getsource(opt.run_optimization_service)
        self.assertIn("_fill_missing_peer_hist(", src)
        before_3m = src.index("_fill_missing_peer_hist(")
        after_3m = src.index("df_hist_3 = _read_hist_cache_across_teams(")
        self.assertLess(before_3m, after_3m)

    def test_wired_before_12m_cache_read_inside_never_sold_on(self):
        import inspect

        src = inspect.getsource(opt.run_optimization_service)
        never_sold_block = src.index("if never_sold_on:", src.index("df_hist_12 = pd.DataFrame()"))
        twelve_m_read = src.index("n_months=12", src.index("_read_hist_cache_across_teams(", never_sold_block))
        gapfill_12 = src.index("n_months=12", never_sold_block)
        self.assertLess(gapfill_12, twelve_m_read)


if __name__ == "__main__":
    unittest.main()
