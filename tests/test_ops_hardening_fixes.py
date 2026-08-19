"""
ชุดแก้ความปลอดภัย/ความถูกต้องรอบ ops (A5, A6, B9, B11 + A7)

แต่ละข้อคือรูรั่วที่เจอจากการตรวจระบบ ไม่ใช่ฟีเจอร์ใหม่ — เทสนี้ตรึงไว้ไม่ให้ย้อนกลับ
"""
from __future__ import annotations

import os
import re
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

import pandas as pd  # noqa: E402

from backend.services import optimize as opt_svc  # noqa: E402

APP_JS = os.path.join(REPO, "frontend", "app.js")


def _read(path: str) -> str:
    with open(os.path.join(REPO, path), encoding="utf-8") as fh:
        return fh.read()


class TestA6NoHardcodedSupervisor(unittest.TestCase):
    """
    เดิม endpoint หลายตัวมี default `sup_id="SL330"` ฝังไว้

    ผลคือ client ที่ลืมส่ง sup_id จะได้ข้อมูลของทีมจริงทีมหนึ่งแทนที่จะถูกปฏิเสธ
    — เงียบ ผิดคน และ SL330 เป็นรหัสที่มีอยู่จริงในระบบ
    """

    ROUTERS = (
        "backend/routers/optimize.py",
        "backend/routers/export.py",
        "backend/routers/debug.py",
    )

    def test_no_router_defaults_to_a_real_supervisor_code(self):
        for path in self.ROUTERS:
            with self.subTest(path=path):
                self.assertNotIn('Query("SL330")', _read(path))

    def test_sup_id_is_required_everywhere(self):
        for path in self.ROUTERS:
            src = _read(path)
            for m in re.finditer(r"sup_id: str = Query\(([^)]*)\)", src):
                with self.subTest(path=path, decl=m.group(0)):
                    self.assertTrue(
                        m.group(1).strip().startswith("..."),
                        "sup_id ต้องเป็นพารามิเตอร์บังคับ",
                    )

    def test_routes_still_load(self):
        import backend.routers.optimize  # noqa: F401
        import backend.routers.export    # noqa: F401
        import backend.routers.debug     # noqa: F401


class TestA5OneLakeUploadIsDevOnly(unittest.TestCase):
    """
    `POST /lakehouse/upload` เขียนไฟล์ขึ้น OneLake จริง ไม่มีปุ่มใน UI

    เดิมเปิดให้ผู้ใช้ที่ล็อกอินคนไหนก็ได้ที่เขียนทีมตัวเองได้ โดยไม่ผ่านด่าน
    สิทธิ์ส่งรายคน (can_import_targetsun) ต่างจากเส้นทางส่ง Target Sun จริง
    """

    def setUp(self):
        self.src = _read("backend/routers/lakehouse.py")
        m = re.search(
            r'@router\.post\("/lakehouse/upload"\)(.*?)\n@router\.', self.src, re.S
        )
        self.assertIsNotNone(m, "ไม่พบ route /lakehouse/upload")
        self.route = m.group(1)

    def test_guarded_by_dev_only_dependency(self):
        self.assertIn("Depends(require_admin_user)", self.route)
        self.assertNotIn("Depends(require_authenticated_user)", self.route)

    def test_the_send_path_is_not_downgraded_by_the_same_change(self):
        """เส้นทางส่ง Target Sun จริงต้องยังเป็นของผู้ใช้ปกติ + ด่านสิทธิ์เดิม"""
        m = re.search(
            r'@router\.post\("/lakehouse/prepare-targetsun"\)(.*?)\n@router\.',
            self.src, re.S,
        )
        self.assertIsNotNone(m)
        self.assertIn("Depends(require_authenticated_user)", m.group(1))
        self.assertIn("ensure_targetsun_import_allowed", m.group(1))

    def test_writes_an_audit_entry(self):
        self.assertIn("onelake_upload", self.route)


class TestB9NoMutationObserverForModalClose(unittest.TestCase):
    """
    เดิมรอผลจาก modal ด้วยการเฝ้า childList ของ body

    ถ้าจังหวะ DOM ไม่ตรงสมมติฐาน (มี modal อื่นซ้อน / ถูกแทนที่แทนที่จะถูกลบ)
    callback จะไม่ยิง แล้ว Promise ค้างถาวร = ปุ่มส่งค้างจนกว่าจะรีเฟรชหน้า
    ตอนนี้ใช้ onSecondary ของ _showInfoModal ซึ่งถูกเรียกครบทุกทางปิด
    """

    def setUp(self):
        self.src = _read("frontend/app.js")

    def test_no_mutation_observer_left(self):
        self.assertNotIn("new MutationObserver", self.src)

    def test_info_modal_calls_on_secondary_on_every_close_path(self):
        m = re.search(r"function _showInfoModal\(\{(.*?)\n\}\n", self.src, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        # ปุ่มปิด + คลิกพื้นหลัง + Escape (ผ่าน bindModalBehaviour)
        self.assertGreaterEqual(
            body.count("onSecondary && onSecondary()"), 3,
            "ทุกทางปิดต้องเรียก onSecondary ไม่งั้นตัวที่รอคำตอบค้าง",
        )

    def test_every_send_gate_supplies_on_secondary(self):
        for fn in (
            "_confirmServerMismatchBeforeSend",
            "_confirmUnverifiableTargetBeforeSend",
            "_confirmStaleTargetBeforeSend",
        ):
            m = re.search(rf"function {fn}\((.*?)\n\}}\n", self.src, re.S)
            with self.subTest(fn=fn):
                self.assertIsNotNone(m, f"ไม่พบ {fn}")
                self.assertIn("onSecondary", m.group(1))

    def test_shortfall_modal_forwards_cancel(self):
        m = re.search(r"function _showShortfallModal\((.*?)\n\}\n", self.src, re.S)
        self.assertIsNotNone(m)
        self.assertIn("onSecondary: onCancel", m.group(1))


class TestA7PeriodComesFromServerTime(unittest.TestCase):
    """
    เดือนเริ่มต้นฝั่งเว็บเคยคิดจาก timezone ของเครื่องผู้ใช้ แต่ backend ใช้
    Asia/Bangkok ตายตัว — ปลายเดือนจึงได้คนละงวดกัน
    """

    def test_frontend_prefers_the_server_period(self):
        src = _read("frontend/app.js")
        m = re.search(r"function getNextMonthPeriod\(\)(.*?)\n\}\n", src, re.S)
        self.assertIsNotNone(m)
        self.assertIn("S.expectedPeriod", m.group(1))

    def test_frontend_fallback_uses_bangkok_not_local_time(self):
        src = _read("frontend/app.js")
        m = re.search(r"function _todayInBangkok\(\)(.*?)\n\}\n", src, re.S)
        self.assertIsNotNone(m, "ต้องมีตัวคิดวันที่ตามเวลาไทย")
        self.assertIn('timeZone: "Asia/Bangkok"', m.group(1))

    def test_server_sends_the_expected_period(self):
        src = _read("backend/routers/managers.py")
        self.assertIn('out["expected_period"]', src)
        self.assertIn("expected_allocation_period_ce", src)

    def test_server_helper_uses_bangkok(self):
        from backend.core.tga_period import expected_allocation_period_ce

        y, m = expected_allocation_period_ce()
        self.assertGreater(y, 2000)
        self.assertTrue(1 <= m <= 12)


class TestB11LockedCellKeysAreNormalised(unittest.TestCase):
    """
    invariant I2: เซลล์ที่ผู้ใช้กดล็อกต้องไม่ถูกขยับ

    `_post_merge_revenue_balance` สร้าง locked_map จาก sku ดิบของ request
    ขณะที่ base_map / flex_skus / even_skus ใช้ _norm_sku() — ต่างกันแค่ช่องว่าง
    หน้า-หลังก็ทำให้ล็อกถูกเมินเงียบ ๆ ในโหมดหลายกลยุทธ์ + tiered
    """

    def _run(self, locked_sku: str) -> pd.DataFrame:
        emps = ["E1", "E2", "E3"]
        df_alloc = pd.DataFrame(
            [{"emp_id": e, "sku": s, "allocated_boxes": b}
             for e, s, b in (
                 ("E1", "100000", 30), ("E2", "100000", 30), ("E3", "100000", 40),
                 ("E1", "200000", 5),  ("E2", "200000", 5),  ("E3", "200000", 10),
             )]
        )
        # ตั้งเป้าเงินให้ E1 "เกิน" และ E2 "ขาด" — ตัวเกลี่ยจะอยากย้ายหีบ SKU 100000
        # จาก E1 ไป E2 ถ้าล็อกทำงาน เซลล์ E1×100000 ต้องไม่ขยับแม้แต่หีบเดียว
        df_emp = pd.DataFrame([
            {"emp_id": "E1", "ly_sales": 1000.0, "yellow_target": 20000.0},
            {"emp_id": "E2", "ly_sales": 1000.0, "yellow_target": 40000.0},
            {"emp_id": "E3", "ly_sales": 1000.0, "yellow_target": 40500.0},
        ])
        df_sku = pd.DataFrame([
            {"sku": "100000", "supervisor_target_boxes": 100, "price_per_box": 1000.0},
            {"sku": "200000", "supervisor_target_boxes": 20, "price_per_box": 50.0},
        ])
        hist = pd.DataFrame(
            [{"emp_id": e, "sku": s, "qty": 10}
             for e in emps for s in ("100000", "200000")]
        )
        return opt_svc._post_merge_revenue_balance(
            df_alloc,
            df_emp,
            df_sku,
            sku_strategy={"100000": "PROP", "200000": "PROP"},
            hist_by_strategy={"PROP": hist},
            locked_edits_data=[{"emp_id": "E1", "sku": locked_sku, "locked_boxes": 30}],
            force_min_one=False,
            cap_multiplier=None,
            even_skus=frozenset(),
            tiered_allocation=True,
            tier_pct=0.2,
            revenue_tolerance_baht=1.0,
        )

    def _locked_value(self, df: pd.DataFrame) -> int:
        row = df[(df["emp_id"] == "E1") & (df["sku"] == "100000")]
        return int(row["allocated_boxes"].iloc[0])

    def test_clean_sku_keeps_the_lock(self):
        self.assertEqual(self._locked_value(self._run("100000")), 30)

    def test_sku_with_stray_whitespace_still_keeps_the_lock(self):
        """ค่าที่ส่งมาจากหน้าเว็บอาจมีช่องว่างติดมา — ต้องล็อกได้เหมือนกัน"""
        self.assertEqual(self._locked_value(self._run(" 100000 ")), 30)

    def test_total_is_unchanged_by_the_balancer(self):
        before = 30 + 30 + 40
        df = self._run(" 100000 ")
        after = int(df[df["sku"] == "100000"]["allocated_boxes"].sum())
        self.assertEqual(after, before, "เกลี่ยมูลค่าต้องไม่ทำให้ยอดรวมต่อ SKU เปลี่ยน")

    def test_malformed_lock_is_skipped_not_crashed(self):
        df = self._run("100000")   # ปกติก่อน
        self.assertFalse(df.empty)
        out = opt_svc._post_merge_revenue_balance(
            df,
            pd.DataFrame([{"emp_id": "E1", "ly_sales": 1.0, "yellow_target": 1.0}]),
            pd.DataFrame([{"sku": "100000", "supervisor_target_boxes": 100,
                           "price_per_box": 1.0}]),
            sku_strategy={"100000": "PROP"},
            hist_by_strategy={"PROP": pd.DataFrame(columns=["emp_id", "sku", "qty"])},
            locked_edits_data=[{"emp_id": "E1"}],       # ไม่มี sku / locked_boxes
            force_min_one=False,
            cap_multiplier=None,
            even_skus=frozenset(),
            tiered_allocation=True,
            tier_pct=0.2,
            revenue_tolerance_baht=1.0,
        )
        self.assertIsNotNone(out)


if __name__ == "__main__":
    unittest.main()
