"""
เป้าตั้งต้นของงวด — สำเนาชุดแรกไว้กันเป้าหาย/ถูกทับ

ที่มา: `data/target_boxes_{SL}_{งวด}.csv` ถูกเขียนทับทุกครั้งที่โหลดขั้นที่ 1 ใหม่
และไม่มีสำเนาเก่าเก็บไว้เลย เป้าเปลี่ยนแล้วไม่มีอะไรให้เทียบหรือกู้
"""

from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.core.paths import (  # noqa: E402
    target_baseline_path,
    target_boxes_cache_path,
    target_sun_cache_path,
)
from backend.services.target_baseline import (  # noqa: E402
    baseline_exists,
    capture_baseline_once,
    diff_against_baseline,
    read_baseline,
    restore_baseline_to_target_files,
)

SUP = "SLBASE"


def _sku(rows):
    return pd.DataFrame([
        {"sku": s, "supervisor_target_boxes": b, "price_per_box": p} for s, b, p in rows
    ])


def _sun(rows):
    return pd.DataFrame([{"emp_id": e, "target_sun": t} for e, t in rows])


class _TmpCwd(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()


class TestCaptureOnce(_TmpCwd):
    def test_captures_on_first_call(self):
        self.assertFalse(baseline_exists(SUP, 9, 2026))
        made = capture_baseline_once(SUP, 9, 2026, _sku([("A", 100, 12.5)]), _sun([("E1", 500.0)]))
        self.assertTrue(made)
        self.assertTrue(os.path.isfile(target_baseline_path(SUP, 9, 2026)))

    def test_never_overwrites_after_first_send(self):
        """ส่งงวดนี้แล้ว = ล็อก — ค่าใน Target Sun หลังส่งมีค่าที่เราส่งปนอยู่ ถ้าทับก็เป็นแค่สำเนาค่าล่าสุด"""
        from unittest.mock import patch

        capture_baseline_once(SUP, 9, 2026, _sku([("A", 100, 12.5)]), _sun([("E1", 500.0)]))
        with patch("backend.services.target_baseline._ledger_has_sends", return_value=True):
            made2 = capture_baseline_once(SUP, 9, 2026, _sku([("A", 999, 12.5)]), _sun([("E1", 1.0)]))
        self.assertFalse(made2)
        base = read_baseline(SUP, 9, 2026)
        self.assertEqual(base["skus"][0]["supervisor_target_boxes"], 100, "ส่งแล้วต้องเป็นค่าเดิมเสมอ")

    def test_revises_before_first_send_and_keeps_history(self):
        """
        OPEN_ITEMS 10.4: HQ ลงเป้าทีละส่วนช่วงวันที่ 15 — เปิดงวดตอนลงครึ่งเดียวแล้วจำค่าครึ่งเดียวไว้ตลอด
        ก่อนส่งครั้งแรก เป้าเปลี่ยน = เก็บรุ่นใหม่ · รุ่นเก่าไม่ลบ (history/)
        """
        capture_baseline_once(SUP, 9, 2026, _sku([("A", 50, 12.5)]), _sun([("E1", 250.0)]), captured_by="t1")
        made2 = capture_baseline_once(SUP, 9, 2026, _sku([("A", 100, 12.5), ("B", 30, 5.0)]),
                                      _sun([("E1", 500.0)]), captured_by="t2")
        self.assertTrue(made2)
        base = read_baseline(SUP, 9, 2026)
        self.assertEqual(base["total_target_boxes"], 130)
        self.assertEqual(base["revision"], 2)
        self.assertEqual(base["previous_revisions"][0]["total_target_boxes"], 50)
        hist = os.listdir(os.path.join("data", "baselines", "history"))
        self.assertEqual(len(hist), 1)
        import json

        with open(os.path.join("data", "baselines", "history", hist[0]), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["total_target_boxes"], 50)

    def test_same_targets_or_price_only_change_does_not_revise(self):
        """เป้าเงินรายคน = หีบ × ราคา — ราคาขยับ (แคช/Fabric ล่ม) ทำให้เงินขยับตาม แต่ไม่ใช่ HQ เปลี่ยนเป้า"""
        capture_baseline_once(SUP, 9, 2026, _sku([("A", 100, 12.5)]), _sun([("E1", 1250.0)]))
        self.assertFalse(capture_baseline_once(SUP, 9, 2026, _sku([("A", 100, 12.5)]), _sun([("E1", 1250.0)])))
        self.assertFalse(capture_baseline_once(SUP, 9, 2026, _sku([("A", 100, 13.0)]), _sun([("E1", 1300.0)])))
        self.assertFalse(capture_baseline_once(SUP, 9, 2026, _sku([("A", 100, 0.0)]), _sun([("E1", 0.0)])))
        self.assertEqual(read_baseline(SUP, 9, 2026).get("revision"), 1)
        self.assertFalse(os.path.isdir(os.path.join("data", "baselines", "history")))

    def test_never_revises_to_zero_money_when_prices_missing(self):
        """เป้าเปลี่ยนจริงแต่ราคายังดึงไม่ได้ (เงินรวม 0) — รอรอบที่ราคาครบ ไม่เก็บชุดเงิน 0 แล้วไปล็อก"""
        capture_baseline_once(SUP, 9, 2026, _sku([("A", 50, 10.0)]), _sun([("E1", 500.0)]))
        self.assertFalse(capture_baseline_once(SUP, 9, 2026, _sku([("A", 80, 0.0)]), _sun([("E1", 0.0)])))
        self.assertEqual(read_baseline(SUP, 9, 2026)["total_target_boxes"], 50)
        self.assertTrue(capture_baseline_once(SUP, 9, 2026, _sku([("A", 80, 10.0)]), _sun([("E1", 800.0)])))

    def test_locked_when_send_evidence_exists_even_if_ledger_unreadable(self):
        """ล็อกต้องเอียงไปทางล็อก: ledger อ่านไม่ได้ / มีสำเนาแถวก่อนส่ง = ถือว่าอาจส่งแล้ว"""
        from backend.services import sent_ledger
        from backend.services.ts_row_snapshots import presend_dir

        capture_baseline_once(SUP, 9, 2026, _sku([("A", 50, 10.0)]), _sun([("E1", 500.0)]))
        lp = sent_ledger.ledger_path(SUP, 9, 2026)
        os.makedirs(os.path.dirname(lp), exist_ok=True)
        with open(lp, "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertFalse(capture_baseline_once(SUP, 9, 2026, _sku([("A", 99, 10.0)]), _sun([("E1", 990.0)])))
        os.remove(lp)
        d = presend_dir(SUP, 9, 2026)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "x.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        self.assertFalse(capture_baseline_once(SUP, 9, 2026, _sku([("A", 99, 10.0)]), _sun([("E1", 990.0)])))
        self.assertEqual(read_baseline(SUP, 9, 2026)["total_target_boxes"], 50)

    def test_does_not_create_an_empty_file(self):
        """ไฟล์เปล่าจะกันการเก็บครั้งหน้าไว้ตลอดกาล — ต้องไม่สร้าง"""
        made = capture_baseline_once(SUP, 9, 2026, _sku([]), _sun([]))
        self.assertFalse(made)
        self.assertFalse(baseline_exists(SUP, 9, 2026))

    def test_records_totals_for_quick_comparison(self):
        capture_baseline_once(SUP, 9, 2026, _sku([("A", 100, 10.0), ("B", 50, 20.0)]), _sun([("E1", 500.0)]))
        base = read_baseline(SUP, 9, 2026)
        self.assertEqual(base["total_target_boxes"], 150)
        self.assertEqual(base["total_target_sun"], 500.0)
        self.assertEqual(base["sup_id"], SUP)

    def test_failure_never_raises(self):
        """การเก็บหลักฐานต้องไม่ทำให้การโหลดหน้าจอพัง"""
        self.assertFalse(capture_baseline_once(SUP, 9, 2026, None, None))


class TestCaptureOnceConcurrent(_TmpCwd):
    """
    เปิดงวดใหม่พร้อมกันสองแท็บครั้งแรก — ต้องได้ไฟล์ของคนแรกเท่านั้น

    เดิม "เช็คว่ายังไม่มี → เขียน" ไม่มีล็อก สองคำขอผ่านด่าน isfile พร้อมกันได้ แล้วคน
    ที่สองเขียนทับของคนแรกเงียบ ๆ (ผิดสัญญาเขียนครั้งเดียว) — ใช้ Event คุมจังหวะให้
    คำขอแรกค้างอยู่กลางการเขียน ไม่ใช้ sleep เดา
    """

    def test_second_capture_while_first_is_writing_does_not_overwrite(self):
        import threading
        from unittest.mock import patch

        from backend.services import target_baseline as tb

        writing = threading.Event()
        release = threading.Event()
        real_write = tb.atomic_write_json

        def slow_write(path, doc, **kw):
            if doc.get("captured_by") == "first":
                writing.set()
                release.wait(5)
            return real_write(path, doc, **kw)

        results = {}

        def run(who, boxes):
            results[who] = capture_baseline_once(
                SUP, 9, 2026, _sku([("A", boxes, 1.0)]), _sun([("E1", 100.0)]),
                captured_by=who,
            )

        with patch.object(tb, "atomic_write_json", side_effect=slow_write):
            t1 = threading.Thread(target=run, args=("first", 10))
            t1.start()
            self.assertTrue(writing.wait(5))
            t2 = threading.Thread(target=run, args=("second", 99))
            t2.start()
            # คำขอที่สองต้องรอล็อก ไม่ใช่เขียนแซงไปก่อน
            t2.join(0.3)
            self.assertTrue(t2.is_alive(), "คำขอที่สองต้องรอคำขอแรกเขียนเสร็จก่อน")
            release.set()
            t1.join(5)
            t2.join(5)

        # คำขอที่สองรอคำขอแรกเขียนเสร็จ แล้วจึงเห็นว่าเป้าเปลี่ยน (ยังไม่เคยส่ง) → เก็บเป็นรุ่น 2
        # ไม่มีใครเขียนทับกันกลางทาง และรุ่นแรกยังอยู่ใน history (OPEN_ITEMS 10.4)
        self.assertEqual(results, {"first": True, "second": True})
        doc = read_baseline(SUP, 9, 2026)
        self.assertEqual((doc["captured_by"], doc["revision"], doc["total_target_boxes"]), ("second", 2, 99))
        self.assertEqual(doc["previous_revisions"][0]["total_target_boxes"], 10)


class TestDiff(_TmpCwd):
    def setUp(self):
        super().setUp()
        capture_baseline_once(
            SUP, 9, 2026,
            _sku([("A", 100, 10.0), ("B", 50, 20.0)]),
            _sun([("E1", 500.0), ("E2", 300.0)]),
        )

    def test_no_diff_when_unchanged(self):
        same = diff_against_baseline(
            SUP, 9, 2026, _sku([("A", 100, 10.0), ("B", 50, 20.0)]), _sun([("E1", 500.0), ("E2", 300.0)])
        )
        self.assertIsNone(same)

    def test_detects_box_change_with_before_and_after(self):
        d = diff_against_baseline(SUP, 9, 2026, _sku([("A", 70, 10.0), ("B", 50, 20.0)]), _sun([("E1", 500.0), ("E2", 300.0)]))
        self.assertIsNotNone(d)
        self.assertEqual(d["boxes_before"], 150)
        self.assertEqual(d["boxes_after"], 120)
        self.assertEqual(d["boxes_delta"], -30)
        self.assertEqual(d["changes"][0], {"sku": "A", "before": 100, "after": 70, "delta": -30})

    def test_detects_a_missing_sku(self):
        d = diff_against_baseline(SUP, 9, 2026, _sku([("A", 100, 10.0)]), _sun([("E1", 500.0), ("E2", 300.0)]))
        gone = [c for c in d["changes"] if c["sku"] == "B"]
        self.assertEqual(gone[0]["after"], 0, "SKU ที่หายไปต้องถูกรายงานว่าเหลือ 0")

    def test_detects_employee_target_change(self):
        d = diff_against_baseline(SUP, 9, 2026, _sku([("A", 100, 10.0), ("B", 50, 20.0)]), _sun([("E1", 111.0), ("E2", 300.0)]))
        self.assertEqual(d["emp_target_changed"], 1)

    def test_returns_none_without_a_baseline(self):
        self.assertIsNone(diff_against_baseline("SLNONE", 9, 2026, _sku([("A", 1, 1.0)]), _sun([])))


class TestRestore(_TmpCwd):
    def setUp(self):
        super().setUp()
        capture_baseline_once(
            SUP, 9, 2026, _sku([("A", 100, 10.0), ("B", 50, 20.0)]), _sun([("E1", 500.0)])
        )

    def test_writes_both_target_files(self):
        """เป้าหีบกับเป้าเงินต้องมาจากรอบเดียวกัน ไม่งั้นตัวปรับสเกลรายได้เพี้ยนทั้งชุด"""
        res = restore_baseline_to_target_files(SUP, 9, 2026)
        self.assertEqual(res["total_boxes"], 150)
        boxes = pd.read_csv(target_boxes_cache_path(SUP, 9, 2026))
        sun = pd.read_csv(target_sun_cache_path(SUP, 9, 2026))
        self.assertEqual(int(boxes["supervisor_target_boxes"].sum()), 150)
        self.assertEqual(sorted(boxes["sku"].tolist()), ["A", "B"])
        self.assertEqual(sun["emp_id"].tolist(), ["E1"])

    def test_restored_file_keeps_the_columns_readers_expect(self):
        restore_baseline_to_target_files(SUP, 9, 2026)
        cols = set(pd.read_csv(target_boxes_cache_path(SUP, 9, 2026)).columns)
        for need in ("sku", "supervisor_target_boxes", "price_per_box", "price_missing"):
            self.assertIn(need, cols)

    def test_does_not_touch_the_baseline_itself(self):
        before = json.load(open(target_baseline_path(SUP, 9, 2026), encoding="utf-8"))
        restore_baseline_to_target_files(SUP, 9, 2026)
        after = json.load(open(target_baseline_path(SUP, 9, 2026), encoding="utf-8"))
        self.assertEqual(before, after)

    def test_missing_baseline_is_a_clear_404(self):
        with self.assertRaises(HTTPException) as c:
            restore_baseline_to_target_files("SLNONE", 9, 2026)
        self.assertEqual(c.exception.status_code, 404)


class TestWiring(unittest.TestCase):
    def test_capture_sits_where_every_path_converges(self):
        """
        ต้องอยู่หลังจุดที่ df_sku/df_sun พร้อมแล้วทั้งสองเส้นทาง (ดึงใหม่ + อ่านจากไฟล์)

        ถ้าวางไว้เฉพาะฝั่งดึงใหม่ ทีมที่เคยเปิดงวดไว้แล้วจะไม่มีวันได้ baseline
        เพราะเข้าทางอ่านไฟล์ตลอด — เคยพลาดแบบนั้นมาแล้วตอนทดสอบกับทีมสาธิต
        """
        from backend.services import employees

        src = inspect.getsource(employees.load_employees_payload)
        # มีสองจุดโดยตั้งใจ: ทีมสาธิตออกจากฟังก์ชันก่อนถึงเส้นทางหลัก จึงต้องเก็บของตัวเอง
        self.assertEqual(src.count("capture_baseline_once"), 2)

        i_demo_return = src.index("return demo_data.build_employees_payload")
        i_demo_capture = src.index("capture_baseline_once")
        self.assertLess(i_demo_capture, i_demo_return, "ทีมสาธิตต้องเก็บก่อน return")

        i_resolve = src.index("if df_sun_csv is None:")
        i_main_capture = src.rindex("capture_baseline_once")
        self.assertLess(
            i_resolve, i_main_capture,
            "ต้องอยู่หลังจุดที่ df_sun_csv ถูกเติมให้ครบ — ไม่งั้นเก็บ baseline ที่ไม่มีเป้าเงิน",
        )

    def test_demo_teams_capture_too(self):
        """ทีมสาธิตเขียนไฟล์เป้าจริง — เป็นทางเดียวที่สาธิต/ทดสอบการกู้คืนได้โดยไม่แตะข้อมูลจริง"""
        from backend.services import employees

        src = inspect.getsource(employees.load_employees_payload)
        head = src[: src.index("return demo_data.build_employees_payload")]
        self.assertIn("write_demo_caches", head)
        self.assertIn("capture_baseline_once", head)

    def test_drift_is_logged(self):
        from backend.services import employees

        src = inspect.getsource(employees.load_employees_payload)
        self.assertIn("target_baseline_drift", src)

    def test_restore_endpoint_is_dev_only(self):
        """กู้คืน = ทับข้อมูลที่คนอื่นอาจใช้อยู่ ผู้ดูแลระดับอื่นต้องทำไม่ได้"""
        from backend.routers import admin as admin_router

        src = inspect.getsource(admin_router.admin_restore_target_baseline)
        self.assertIn("require_admin_user", src)
        self.assertNotIn("require_admin_scoped", src)

    def test_read_endpoint_is_open_to_every_admin_but_scoped(self):
        from backend.routers import admin as admin_router

        src = inspect.getsource(admin_router.admin_get_target_baseline)
        self.assertIn("require_admin_scoped", src)
        self.assertIn("ensure_sup_in_admin_scope", src)

    def test_baseline_dir_is_not_swept_by_cache_cleanup(self):
        """ตัวล้าง cache วนเฉพาะไฟล์ชั้นบนใน data/ — baseline อยู่ในโฟลเดอร์ย่อยจึงรอด"""
        from backend.core import caches

        self.assertTrue(target_baseline_path("SL1", 9, 2026).startswith("data/baselines/"))
        for prefix in caches._CACHE_PREFIXES:
            self.assertFalse("baseline".startswith(prefix.rstrip("_")))


if __name__ == "__main__":
    unittest.main()
