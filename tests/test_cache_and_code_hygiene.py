"""
งานเก็บกวาด 1.7 — กันของที่ "ดูไม่มีพิษภัย" แต่กัดตอนหน้างาน

1. โค้ดตายที่ยังอยู่ในไฟล์ ทำให้คนอ่านคิดว่ามีการตรวจอยู่ทั้งที่ไม่มี
2. ตัวแปร .env ที่ไม่มีผลจริง ทำให้คนตั้งค่าแล้วเข้าใจผิดว่าปิดฟีเจอร์ได้
3. cache ที่ควร/ไม่ควรถูกล้างตามอายุ — ล้างผิดตัวคือบล็อกการส่งของผู้ใช้
4. รหัสพนักงานต้อง normalize แบบเดียวกันทั้งสองฝั่ง ไม่งั้นจับคู่ไม่ติดเงียบ ๆ
5. เทสต้องไม่ทิ้งโฟลเดอร์ชั่วคราวไว้เกลื่อนเครื่อง
"""
from __future__ import annotations

import glob
import os
import re
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.core import caches  # noqa: E402
from backend.services.lakehouse import norm_emp_code  # noqa: E402


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


class TestDeadCodeRemoved(unittest.TestCase):
    def test_tga_import_key_complete_is_gone(self):
        """เคยเป็นตัวตรวจฟิลด์บังคับ แต่ไม่มีใครเรียกแล้ว — เก็บไว้ = เข้าใจผิดว่ายังตรวจอยู่"""
        for rel in ("backend/services/lakehouse.py",):
            self.assertNotIn("_tga_import_key_complete", _read(rel))


class TestEnvExampleHasNoDeadSwitch(unittest.TestCase):
    """
    `TGA_FILTER_BY_EFFECTIVE` อยู่ใน .env.example เหมือนเป็นสวิตช์ปิดการกรอง
    แต่ `tga_filter_by_selected_period()` คืน True ตายตัว ไม่เคยอ่านค่านี้เลย
    """

    def test_not_in_env_example(self):
        self.assertNotIn("TGA_FILTER_BY_EFFECTIVE", _read("config/.env.example"))

    def test_not_advertised_in_readme(self):
        self.assertNotIn("`TGA_FILTER_BY_EFFECTIVE`", _read("readme.md"))

    def test_the_rule_is_still_hard_coded(self):
        from backend.core.tga_period import tga_filter_by_selected_period

        os.environ["TGA_FILTER_BY_EFFECTIVE"] = "0"
        try:
            self.assertTrue(
                tga_filter_by_selected_period(),
                "ค่าใน env ต้องไม่มีผล — ถ้ามีผลเมื่อไรแปลว่ากติกาเปลี่ยนไปแล้ว",
            )
        finally:
            os.environ.pop("TGA_FILTER_BY_EFFECTIVE", None)


class TestCacheCleanupPolicy(unittest.TestCase):
    """
    เส้นแบ่ง: cache เร่งความเร็ว = ล้างได้ · หลักฐานที่เส้นทางส่งต้องใช้ = ห้ามล้าง
    """

    def test_speed_caches_are_cleaned(self):
        for prefix in ("hist_cache_", "hist_lysm_", "hist_prev_", "hist_cy_", "emp_cache_"):
            with self.subTest(prefix=prefix):
                self.assertIn(prefix, caches._CACHE_PREFIXES)

    def test_payload_cache_is_now_cleaned(self):
        """มี TTL ของตัวเองอยู่แล้ว แต่ TTL ไม่ลบไฟล์ ดิสก์จึงบวมไปเรื่อย ๆ"""
        self.assertIn("payload_cache_", caches._CACHE_PREFIXES)

    def test_send_path_evidence_is_never_cleaned(self):
        """
        target_boxes_ = ไฟล์เป้าที่ด่านตรวจใช้เทียบ
        target_sun_   = เป้าเงินของงวดนั้น
        tga_lines_    = TGA grain ที่ใช้แตกหีบลงคีย์จริงของ Oracle
        ลบตัวใดตัวหนึ่ง = ผู้ใช้ส่ง snapshot เก่าไม่ได้ หรือโดนตัด SKU ทั้งที่ไม่ผิด
        """
        for prefix in ("target_boxes_", "target_sun_", "tga_lines_"):
            with self.subTest(prefix=prefix):
                self.assertNotIn(prefix, caches._CACHE_PREFIXES)

    def test_prefixes_are_a_tuple_startswith_accepts(self):
        self.assertIsInstance(caches._CACHE_PREFIXES, tuple)
        self.assertTrue("payload_cache_SL1_2026_09.json".startswith(caches._CACHE_PREFIXES))
        self.assertFalse("tga_lines_SL1_2026_09.csv".startswith(caches._CACHE_PREFIXES))


class TestEmployeeCodeNormalisation(unittest.TestCase):
    """
    ฝั่ง grain กับฝั่งผลกระจายต้องใช้กติกาเดียวกัน ไม่งั้นจับคู่ไม่ติด
    แล้ว SKU นั้นถูกตัดทั้งตัวตามนโยบาย S3.5 โดยที่ข้อมูลไม่ได้ผิดอะไรเลย
    """

    def test_trims_and_uppercases(self):
        self.assertEqual(norm_emp_code("  b320 "), "B320")
        self.assertEqual(norm_emp_code("B320"), "B320")

    def test_pads_pure_digits_to_five(self):
        self.assertEqual(norm_emp_code("1234"), "01234")
        self.assertEqual(norm_emp_code("01234"), "01234")

    def test_does_not_pad_alphanumeric_codes(self):
        """รหัสจริงทั้งหมดตอนนี้เป็นแบบนี้ — ต้องไม่ถูกแตะ"""
        self.assertEqual(norm_emp_code("B26"), "B26")

    def test_handles_none_and_blank(self):
        self.assertEqual(norm_emp_code(None), "")
        self.assertEqual(norm_emp_code("   "), "")

    def test_matches_the_target_sun_reader_rule(self):
        from backend.services.targetsun_read import _normalize_salesman_code

        for raw in ("b320", " B320 ", "1234", "01234", "B26", ""):
            with self.subTest(raw=raw):
                self.assertEqual(norm_emp_code(raw), _normalize_salesman_code(raw))


class TestNoLeakedTempDirs(unittest.TestCase):
    def test_every_mkdtemp_has_a_matching_cleanup(self):
        offenders = []
        for path in glob.glob(os.path.join(REPO, "tests", "test_*.py")):
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            made = len(re.findall(r"tempfile\.mkdtemp\(", src))
            if not made:
                continue
            cleaned = len(re.findall(r"rmtree\(self\._tmpdir", src))
            if cleaned < made:
                offenders.append(f"{os.path.basename(path)}: mkdtemp={made} rmtree={cleaned}")
        self.assertEqual([], offenders, "เทสสร้างโฟลเดอร์ชั่วคราวแล้วไม่ลบ:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
