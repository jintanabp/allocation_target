"""Tests for atomic file writes (กัน torn read เมื่อหลายคนใช้พร้อมกัน)."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

import pandas as pd  # noqa: E402

from backend.core.atomic_io import (  # noqa: E402
    atomic_write_csv,
    atomic_write_json,
    atomic_write_text,
    read_locked,
)


class TestAtomicIO(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _p(self, name: str) -> str:
        return os.path.join(self._tmpdir, name)

    def test_write_text_roundtrip(self):
        p = self._p("a.txt")
        atomic_write_text(p, "สวัสดี")
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read(), "สวัสดี")

    def test_write_json_roundtrip(self):
        p = self._p("a.json")
        atomic_write_json(p, {"k": "ค่า", "n": 1}, indent=2)
        with open(p, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"k": "ค่า", "n": 1})

    def test_write_csv_roundtrip(self):
        p = self._p("a.csv")
        atomic_write_csv(p, pd.DataFrame({"sku": ["1", "2"], "boxes": [3, 4]}))
        back = pd.read_csv(p, dtype={"sku": str})
        self.assertEqual(back["sku"].tolist(), ["1", "2"])

    def test_creates_missing_dirs(self):
        p = os.path.join(self._tmpdir, "deep", "nested", "a.json")
        atomic_write_json(p, {"ok": True})
        self.assertTrue(os.path.isfile(p))

    def test_tmp_cleaned_up_when_write_fails(self):
        p = self._p("bad.json")

        class Unserializable:
            pass

        with self.assertRaises(TypeError):
            atomic_write_json(p, {"bad": Unserializable()})
        self.assertFalse(os.path.exists(p), "ไฟล์ปลายทางต้องไม่ถูกสร้างเมื่อเขียนพัง")
        leftovers = [f for f in os.listdir(self._tmpdir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [], f"เหลือไฟล์ temp ค้าง: {leftovers}")

    def test_existing_file_untouched_when_write_fails(self):
        p = self._p("keep.json")
        atomic_write_json(p, {"v": "เดิม"})

        class Unserializable:
            pass

        with self.assertRaises(TypeError):
            atomic_write_json(p, {"bad": Unserializable()})
        with open(p, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"v": "เดิม"}, "ของเดิมต้องไม่ถูกทำลาย")

    def test_readers_never_see_partial_json_even_without_lock(self):
        """
        พิสูจน์ "atomicity" ล้วน ๆ: readers ที่ไม่ถือ lock ต้องไม่เจอ JSON ครึ่งใบ
        (open(..., "w") + json.dump ธรรมดาจะพังข้อนี้)

        ผลข้างเคียงที่ตั้งใจบันทึกไว้: บน Windows writer จะโดน PermissionError เป็นระยะ
        เพราะ replace ไฟล์ที่ reader เปิด handle ค้างไม่ได้ — บน POSIX ไม่เกิด
        นี่คือเหตุผลที่ต้องมี read_locked() และทำไม reader ในโค้ดจริงต้องใช้มัน
        """
        p = self._p("hot.json")
        atomic_write_json(p, {"rows": []})
        stop = threading.Event()
        torn: list[str] = []
        # error ฝั่ง reader ที่เป็นเรื่อง "ไฟล์ถูกล็อก/สลับตัวอยู่" ไม่ใช่ "อ่านได้ครึ่งใบ"
        # บน Windows เปิดไฟล์ตอน replace กำลังเกิดจะได้ PermissionError/FileNotFoundError
        # ซึ่งเป็นข้อจำกัดของ OS ไม่ใช่ atomicity พัง — เดิมนับรวมเป็น torn ทำให้เทสต์
        # ล้มแบบสุ่มเวลาเครื่องงานหนัก (เจอจริงตอนรัน node check พร้อมชุดเทสต์)
        locked: list[str] = []
        writer_denied = [0]
        reads = [0]
        lock = threading.Lock()

        def writer():
            i = 0
            while not stop.is_set():
                i += 1
                try:
                    atomic_write_json(p, {"rows": [{"n": x} for x in range(i % 400)]})
                except PermissionError:
                    with lock:
                        writer_denied[0] += 1

        def reader():
            while not stop.is_set():
                try:
                    with open(p, encoding="utf-8") as f:
                        doc = json.load(f)
                    if not isinstance(doc.get("rows"), list):
                        with lock:
                            torn.append("rows ไม่ใช่ list")
                    with lock:
                        reads[0] += 1
                except (PermissionError, FileNotFoundError) as e:
                    with lock:
                        locked.append(f"{type(e).__name__}: {e}")
                except Exception as e:  # torn read (JSON ครึ่งใบ) จะโผล่ที่นี่
                    with lock:
                        torn.append(f"{type(e).__name__}: {e}")

        threads = [threading.Thread(target=writer, daemon=True)]
        threads += [threading.Thread(target=reader, daemon=True) for _ in range(4)]
        for t in threads:
            t.start()
        time.sleep(1.5)
        stop.set()
        for t in threads:
            t.join(timeout=5)

        self.assertGreater(reads[0], 50, "อ่านน้อยเกินไป — test ไม่ได้ทดสอบอะไร")
        self.assertEqual(torn[:5], [], f"เจอ torn read {len(torn)} ครั้ง")
        if os.name != "nt":
            self.assertEqual(
                writer_denied[0], 0, "POSIX ไม่ควรมี PermissionError จาก rename เลย"
            )
            self.assertEqual(
                locked[:5], [], "POSIX ไม่ควรมี error จากการล็อกไฟล์ฝั่ง reader เลย"
            )

    def test_writer_not_blocked_by_locked_readers(self):
        """
        บน Windows os.replace พังถ้า reader ถือ handle ไฟล์ปลายทางค้าง
        reader ที่ใช้ read_locked() ต้องไม่ทำให้ writer พัง
        """
        p = self._p("rw.csv")
        atomic_write_csv(p, pd.DataFrame({"sku": ["A"], "n": [1]}))
        stop = threading.Event()
        errors: list[str] = []
        writes = [0]
        reads = [0]
        lock = threading.Lock()

        def writer():
            while not stop.is_set():
                try:
                    atomic_write_csv(p, pd.DataFrame({"sku": ["A"], "n": [2]}))
                    with lock:
                        writes[0] += 1
                except Exception as e:
                    with lock:
                        errors.append(f"writer {type(e).__name__}: {e}")

        def reader():
            while not stop.is_set():
                try:
                    with read_locked(p):
                        pd.read_csv(p)
                    with lock:
                        reads[0] += 1
                except Exception as e:
                    with lock:
                        errors.append(f"reader {type(e).__name__}: {e}")

        threads = [threading.Thread(target=writer, daemon=True)]
        threads += [threading.Thread(target=reader, daemon=True) for _ in range(3)]
        for t in threads:
            t.start()
        time.sleep(1.2)
        stop.set()
        for t in threads:
            t.join(timeout=5)

        self.assertGreater(writes[0], 10, "เขียนน้อยเกินไป — test ไม่ได้ทดสอบอะไร")
        self.assertGreater(reads[0], 10, "อ่านน้อยเกินไป — test ไม่ได้ทดสอบอะไร")
        self.assertEqual(errors[:5], [], f"พัง {len(errors)} ครั้ง")

    def test_concurrent_writers_same_path_no_tmp_collision(self):
        """
        8 writers เขียน path เดียวกันพร้อมกัน — mkstemp ต้องทำให้ temp ไม่ชนกัน
        (ต่างจาก tmp name ตายตัวแบบ f"{path}.tmp")
        ผลสุดท้ายต้องเป็นของ writer คนใดคนหนึ่งแบบครบถ้วน ไม่ใช่ของปนกัน
        """
        p = self._p("race.json")
        barrier = threading.Barrier(8, timeout=10)
        errors: list[str] = []

        def w(idx: int):
            try:
                barrier.wait()
                for _ in range(30):
                    atomic_write_json(p, {"owner": idx, "payload": [idx] * 200})
            except Exception as e:
                errors.append(f"{type(e).__name__}: {e}")

        threads = [threading.Thread(target=w, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(errors, [], f"writer พัง: {errors}")
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(
            doc["payload"],
            [doc["owner"]] * 200,
            "ไฟล์สุดท้ายต้องเป็นของ writer คนเดียว ไม่ใช่ข้อมูลปนกัน",
        )
        leftovers = [f for f in os.listdir(self._tmpdir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [], f"เหลือไฟล์ temp ค้าง: {leftovers}")


if __name__ == "__main__":
    unittest.main()
