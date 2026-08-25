"""
แคชตั้งต้นจาก seed/ ต้องเติมช่องว่างได้ แต่ห้ามทับของจริง

เจอของจริง 2026-08-25: Fabric ตอบ capacity เต็ม เซิร์ฟเวอร์ที่ยังไม่เคยดึงราคา
งวด 09/2026 สำเร็จเลยไม่มีราคาให้ใช้ → เป้ารวม (บาท) เป็น 0 → ทุกทีมเปิดงวด
ไม่ได้พร้อมกัน · ก๊อปไฟล์ขึ้นเซิร์ฟเวอร์ตรง ๆ ไม่ได้ จึงส่งผ่าน git ที่ seed/
แล้วให้แอปคัดลอกเข้าที่ตอนเปิดเครื่อง

ห้าม track ไฟล์ใน data/cache/ ตรง ๆ เพราะไฟล์จริงเขียนทับตัวเองทุกครั้งที่ดึง
สำเร็จ — pull ครั้งหน้าจะชนกันเอง (บทเรียนจาก config/app_runtime.json)
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from backend.services import fabric_cache as fc


class TestSeedCache(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="seedcache_")
        self.repo = os.path.join(self._tmpdir, "repo")
        self.seed = os.path.join(self.repo, "seed", "cache")
        self.live = os.path.join(self._tmpdir, "live")
        os.makedirs(self.seed)
        os.makedirs(self.live)
        self._old = os.environ.get("FABRIC_CACHE_DIR")
        os.environ["FABRIC_CACHE_DIR"] = self.live

    def tearDown(self):
        if self._old is None:
            os.environ.pop("FABRIC_CACHE_DIR", None)
        else:
            os.environ["FABRIC_CACHE_DIR"] = self._old
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _put(self, folder: str, name: str, marker: str):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            json.dump({"marker": marker}, f)

    def _marker(self, name: str) -> str:
        with open(os.path.join(self.live, name), encoding="utf-8") as f:
            return json.load(f)["marker"]

    def _run(self) -> int:
        with patch.object(fc, "_repo_root", return_value=self.repo):
            return fc.seed_cache_from_repo()

    def test_copies_when_live_cache_is_missing(self):
        self._put(self.seed, "price_per_box_2026_09.json", "seed")
        self.assertEqual(self._run(), 1)
        self.assertEqual(self._marker("price_per_box_2026_09.json"), "seed")

    def test_never_overwrites_existing_live_cache(self):
        """ของที่ดึงสดมาได้ต้องชนะเสมอ"""
        self._put(self.seed, "price_per_box_2026_09.json", "seed")
        self._put(self.live, "price_per_box_2026_09.json", "ของจริง")
        self.assertEqual(self._run(), 0)
        self.assertEqual(self._marker("price_per_box_2026_09.json"), "ของจริง")

    def test_copies_only_the_missing_ones(self):
        self._put(self.seed, "price_per_box_2026_09.json", "seed")
        self._put(self.seed, "dim_product_2026_09.json", "seed")
        self._put(self.live, "dim_product_2026_09.json", "ของจริง")
        self.assertEqual(self._run(), 1)
        self.assertEqual(self._marker("dim_product_2026_09.json"), "ของจริง")
        self.assertEqual(self._marker("price_per_box_2026_09.json"), "seed")

    def test_ignores_non_json(self):
        with open(os.path.join(self.seed, "readme.txt"), "w", encoding="utf-8") as f:
            f.write("ไม่ใช่แคช")
        self.assertEqual(self._run(), 0)
        self.assertFalse(os.path.exists(os.path.join(self.live, "readme.txt")))

    def test_no_seed_folder_is_not_an_error(self):
        shutil.rmtree(os.path.join(self.repo, "seed"))
        self.assertEqual(self._run(), 0)


if __name__ == "__main__":
    unittest.main()
