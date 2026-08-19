"""
เป้าหีบ/Target Sun ต้องแยกราย (sup, งวด)

บั๊กเดิม: employees.py เขียน data/target_boxes.csv ที่ไม่มี sup_id ในชื่อไฟล์และไม่มีคอลัมน์ sup_id
optimize.py จึงอ่านเป้าของทีมที่โหลดล่าสุดมาป้อน LP โดยไม่มีทางกรองให้ถูกทีม
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

import pandas as pd  # noqa: E402

from backend.core.atomic_io import atomic_write_csv  # noqa: E402
from backend.core.paths import (  # noqa: E402
    target_boxes_cache_path,
    target_sun_cache_path,
)
from backend.core.targets import (  # noqa: E402
    load_target_csv_for,
    target_csv_ready,
)


def _sku_df(skus, price):
    return pd.DataFrame(
        {
            "sku": skus,
            "price_per_box": [price] * len(skus),
            "supervisor_target_boxes": [10] * len(skus),
        }
    )


def _sun_df(emps):
    return pd.DataFrame({"emp_id": emps, "target_amount": [100] * len(emps)})


class TestTargetCsvScoping(unittest.TestCase):
    """รันใน cwd ชั่วคราว เพราะ paths.py คืน path แบบ relative ("data/...")"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        os.chdir(self._tmpdir)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_for(self, sup, month, year, skus, price):
        atomic_write_csv(target_boxes_cache_path(sup, month, year), _sku_df(skus, price))
        atomic_write_csv(target_sun_cache_path(sup, month, year), _sun_df(["E1"]))

    def test_paths_include_sup_and_period(self):
        p = target_boxes_cache_path("SL330", 7, 2026)
        self.assertIn("SL330", p)
        self.assertIn("2026", p)
        self.assertIn("07", p)
        self.assertNotEqual(p, target_boxes_cache_path("SL397", 7, 2026))
        self.assertNotEqual(p, target_boxes_cache_path("SL330", 8, 2026))

    def test_no_cross_sup_bleed(self):
        """regression ของบั๊กจริง: SL330 ต้องไม่มีวันได้ข้อมูลของ SL397"""
        self._write_for("SL330", 7, 2026, ["A1", "A2"], price=100.0)
        self._write_for("SL397", 7, 2026, ["B1", "B2"], price=999.0)

        df330, _ = load_target_csv_for("SL330", 7, 2026)
        df397, _ = load_target_csv_for("SL397", 7, 2026)

        self.assertEqual(sorted(df330["sku"]), ["A1", "A2"])
        self.assertEqual(sorted(df397["sku"]), ["B1", "B2"])
        self.assertEqual(set(df330["price_per_box"]), {100.0})
        self.assertEqual(set(df397["price_per_box"]), {999.0})

    def test_period_is_scoped_too(self):
        self._write_for("SL330", 7, 2026, ["JUL"], price=1.0)
        self._write_for("SL330", 8, 2026, ["AUG"], price=2.0)
        df_jul, _ = load_target_csv_for("SL330", 7, 2026)
        df_aug, _ = load_target_csv_for("SL330", 8, 2026)
        self.assertEqual(df_jul["sku"].tolist(), ["JUL"])
        self.assertEqual(df_aug["sku"].tolist(), ["AUG"])

    def test_legacy_global_fallback_then_ignored(self):
        """
        ยังไม่มีไฟล์ราย sup → ตกไปอ่าน global เดิม (ผู้ใช้ไม่เจอ error ตอน deploy ใหม่)
        พอมีไฟล์ราย sup แล้ว → ต้องเลิกสน global ทันที
        """
        atomic_write_csv("data/target_boxes.csv", _sku_df(["OLD"], 5.0))
        atomic_write_csv("data/target_sun.csv", _sun_df(["E9"]))

        df, _ = load_target_csv_for("SL330", 7, 2026)
        self.assertEqual(df["sku"].tolist(), ["OLD"], "ควร fallback ไป global เดิม")

        self._write_for("SL330", 7, 2026, ["NEW"], price=7.0)
        df2, _ = load_target_csv_for("SL330", 7, 2026)
        self.assertEqual(df2["sku"].tolist(), ["NEW"], "มีไฟล์ราย sup แล้วต้องไม่ใช้ global")

    def test_fallback_can_be_disabled(self):
        atomic_write_csv("data/target_boxes.csv", _sku_df(["OLD"], 5.0))
        df, _ = load_target_csv_for("SL330", 7, 2026, allow_legacy_fallback=False)
        self.assertIsNone(df, "ปิด fallback แล้วต้องไม่หยิบไฟล์ global มาใช้")

    def test_target_csv_ready_gate(self):
        """กันไม่ให้ payload cache บังการสร้างไฟล์เป้า — ต้องครบทั้งคู่ถึงจะถือว่าพร้อม"""
        self.assertFalse(target_csv_ready("SL330", 7, 2026))
        atomic_write_csv(target_boxes_cache_path("SL330", 7, 2026), _sku_df(["A"], 1.0))
        self.assertFalse(target_csv_ready("SL330", 7, 2026), "มีแค่ target_boxes ยังไม่พอ")
        atomic_write_csv(target_sun_cache_path("SL330", 7, 2026), _sun_df(["E1"]))
        self.assertTrue(target_csv_ready("SL330", 7, 2026))

    def test_cleanup_removes_legacy_global_but_keeps_per_sup_targets(self):
        """
        regression เดิม: ลบไฟล์ราย sup แต่ปล่อยไฟล์ global เก่าไว้
        load_target_csv_for จะตกกลับไปอ่าน global = ได้เป้าทีมอื่น

        ตอนนี้ปิดสองชั้น (เปลี่ยนพฤติกรรมโดยตั้งใจ):
          - ไฟล์เป้าราย sup ไม่ถูกล้างตามอายุอีกแล้ว เพราะเป็นหลักฐานที่ประตู
            ตรวจก่อนส่งใช้เทียบ ถ้ามันหายการส่งจะถูกบล็อกด้วย send_target_unverifiable
          - ไฟล์ global เก่ายังถูกลบเหมือนเดิม
        """
        import time

        from backend.core.caches import cleanup_old_caches

        self._write_for("SL330", 7, 2026, ["MINE"], price=1.0)
        atomic_write_csv("data/target_boxes.csv", _sku_df(["OTHERTEAM"], 9.0))

        old = time.time() - 30 * 86400
        for f in os.listdir("data"):
            os.utime(os.path.join("data", f), (old, old))

        cleanup_old_caches(max_age_days=7)

        self.assertFalse(
            os.path.exists("data/target_boxes.csv"),
            "ไฟล์ global เก่าต้องถูกลบด้วย ไม่งั้นกลายเป็น fallback ที่ให้ข้อมูลทีมอื่น",
        )
        self.assertTrue(
            os.path.exists(target_boxes_cache_path("SL330", 7, 2026)),
            "ไฟล์เป้าราย sup ต้องอยู่ต่อ — ประตูตรวจก่อนส่งใช้ไฟล์นี้เป็นหลักฐาน",
        )
        df, _ = load_target_csv_for("SL330", 7, 2026)
        self.assertEqual(
            df["sku"].tolist(), ["MINE"],
            "ต้องได้เป้าของทีมตัวเองเสมอ ห้ามได้ของทีมอื่นไม่ว่ากรณีใด",
        )

    def test_cleanup_keeps_legacy_global_in_dev_mode(self):
        """โหมด dev วางไฟล์ global เอง — ห้ามลบของเขา"""
        import time

        from backend.core.caches import cleanup_old_caches

        atomic_write_csv("data/target_boxes.csv", _sku_df(["DEV"], 1.0))
        old = time.time() - 30 * 86400
        os.utime("data/target_boxes.csv", (old, old))

        os.environ["USE_LEGACY_TARGET_CSV"] = "1"
        try:
            cleanup_old_caches(max_age_days=7)
        finally:
            os.environ.pop("USE_LEGACY_TARGET_CSV", None)
        self.assertTrue(os.path.exists("data/target_boxes.csv"))

    def test_excel_source_path_falls_back_when_per_sup_missing(self):
        """
        generate_excel คืน {} เงียบ ๆ ถ้า path ไม่มีไฟล์ → Excel เป้าว่างโดยไม่มี error
        ตอน df_sku มาจาก fallback global ต้องส่ง path ที่มีไฟล์จริงไปให้
        """
        from backend.core.targets import target_boxes_source_path

        atomic_write_csv("data/target_boxes.csv", _sku_df(["OLD"], 5.0))
        self.assertEqual(
            target_boxes_source_path("SL330", 7, 2026),
            "data/target_boxes.csv",
            "ยังไม่มีไฟล์ราย sup → ต้องชี้ไป global ที่มีอยู่จริง ไม่ใช่ path ที่ไม่มีไฟล์",
        )
        self._write_for("SL330", 7, 2026, ["NEW"], price=7.0)
        self.assertEqual(
            target_boxes_source_path("SL330", 7, 2026),
            target_boxes_cache_path("SL330", 7, 2026),
        )

    def test_concurrent_two_sups_never_bleed(self):
        """
        สองทีมโหลด Dashboard พร้อมกันวน ๆ ขณะที่อีก thread อ่านเป้าของ SL330
        ทุกแถวที่อ่านได้ต้องเป็นของ SL330 เสมอ — บนโค้ดเดิม (ไฟล์ global) test นี้พังแน่นอน
        """
        self._write_for("SL330", 7, 2026, ["A1", "A2"], price=100.0)
        self._write_for("SL397", 7, 2026, ["B1", "B2"], price=999.0)

        stop = threading.Event()
        errors: list[str] = []
        reads = [0]
        lock = threading.Lock()

        def writer(sup, skus, price):
            while not stop.is_set():
                atomic_write_csv(
                    target_boxes_cache_path(sup, 7, 2026), _sku_df(skus, price)
                )

        def reader():
            while not stop.is_set():
                try:
                    df, _ = load_target_csv_for("SL330", 7, 2026)
                    bad = set(df["sku"]) - {"A1", "A2"}
                    if bad:
                        with lock:
                            errors.append(f"SL330 ได้ SKU ของทีมอื่น: {sorted(bad)}")
                    with lock:
                        reads[0] += 1
                except Exception as e:
                    with lock:
                        errors.append(f"{type(e).__name__}: {e}")

        threads = [
            threading.Thread(target=writer, args=("SL330", ["A1", "A2"], 100.0), daemon=True),
            threading.Thread(target=writer, args=("SL397", ["B1", "B2"], 999.0), daemon=True),
            threading.Thread(target=reader, daemon=True),
            threading.Thread(target=reader, daemon=True),
        ]
        for t in threads:
            t.start()
        threading.Event().wait(1.2)
        stop.set()
        for t in threads:
            t.join(timeout=5)

        self.assertGreater(reads[0], 20, "อ่านน้อยเกินไป — test ไม่ได้ทดสอบอะไร")
        self.assertEqual(errors[:5], [], f"ข้อมูลข้ามทีม/พัง {len(errors)} ครั้ง")


if __name__ == "__main__":
    unittest.main()
