"""
ชุดข้อมูลสำหรับพัฒนาในเครื่อง — สิ่งที่ห้ามหลุดออกไปกับไฟล์ zip

จุดประสงค์: พัฒนาในเครื่องด้วยข้อมูลชุดเดียวกับ server (ของหลายอย่างในชุดนี้
ไม่มีปุ่มดาวน์โหลดของตัวเอง เดิมต้องขอ IT ก๊อปให้ทีละรอบ)

เทสชุดนี้เฝ้าสามเรื่องที่พังแล้วเสียหายจริง:
  1. **`.env` ต้องไม่ติดไปด้วย** — มี FABRIC_CLIENT_SECRET อยู่ในนั้น
  2. **ต้องเป็น dev เท่านั้น** — ดึงทะเบียนผู้ใช้ทั้งองค์กรออกในครั้งเดียว
  3. **ต้องถูก audit** — เป็นการนำข้อมูลออกนอกระบบ ต้องตามรอยได้ว่าใครกดเมื่อไหร่
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import dev_bundle  # noqa: E402


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


class _FakeRepo:
    """โครงไฟล์จำลอง — จะได้ไม่ต้องพึ่ง data/ จริงของเครื่องที่รันเทส"""

    LAYOUT = {
        "config/user_access.json": '[{"email":"a@b.c"}]',
        "config/sl_links.json": "{}",
        "config/.env": "FABRIC_CLIENT_SECRET=ห้ามหลุด",
        "config/app_runtime.example.json": "{}",
        "data/tga_lines_SLX_2026_09.csv": "emp_id,sku,qty\n",
        "data/emp_cache_SLX_2026_09.csv": "emp_id\n",
        "data/target_boxes_SLX_2026_09.csv": "sku,supervisor_target_boxes\n",
        "data/allocations/SLX_2026_09.json": '{"sup_id":"SLX"}',
        "data/baselines/SLX_2026_09.json": "{}",
        "data/payload_cache_SLX_2026_09.json": '{"ไม่ควรอยู่ในชุด":1}',
        "data/logs/usage_2026-09-07.jsonl": '{"ไม่ควรอยู่ในชุด":1}',
    }

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name
        for rel, body in self.LAYOUT.items():
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
        self._patch = mock.patch.object(dev_bundle, "_repo_root", lambda: root)
        self._patch.start()
        return root

    def __exit__(self, *exc):
        self._patch.stop()
        self._tmp.cleanup()


def _names(content: bytes) -> set[str]:
    return set(zipfile.ZipFile(io.BytesIO(content)).namelist())


class TestSecretsNeverLeave(unittest.TestCase):
    def test_the_env_file_is_not_in_the_bundle(self):
        with _FakeRepo():
            content, _ = dev_bundle.build_dev_bundle()
        for name in _names(content):
            self.assertNotIn(".env", name, "ห้าม .env ติดไปเด็ดขาด — มี secret อยู่ข้างใน")

    def test_only_json_is_taken_from_config(self):
        """glob เป็น config/*.json — ไฟล์ชนิดอื่นใน config/ ต้องไม่ติดมา"""
        with _FakeRepo():
            content, _ = dev_bundle.build_dev_bundle()
        for name in _names(content):
            if name.startswith("config/"):
                self.assertTrue(name.endswith(".json"), name)

    def test_the_globs_are_hard_coded_not_caller_supplied(self):
        """กันไม่ให้ใครเติมพารามิเตอร์ path แล้วดูดไฟล์นอกโฟลเดอร์ที่ตั้งใจ"""
        src = _read("backend/services/dev_bundle.py")
        for _name, pattern, _light, _desc in dev_bundle.BUNDLE_PARTS:
            self.assertIsInstance(pattern, str)
            self.assertNotIn("..", pattern)
            self.assertRegex(pattern, r"^(config|data)/")
        self.assertNotIn("os.environ", src, "อย่ารับ path จาก env มาต่อ glob")


class TestWhatGoesIn(unittest.TestCase):
    def test_the_full_bundle_has_every_part(self):
        with _FakeRepo():
            content, manifest = dev_bundle.build_dev_bundle(light=False)
        names = _names(content)
        self.assertIn("config/user_access.json", names)
        self.assertIn("data/tga_lines_SLX_2026_09.csv", names)
        self.assertIn("data/emp_cache_SLX_2026_09.csv", names)
        self.assertIn("data/allocations/SLX_2026_09.json", names)
        self.assertIn("data/baselines/SLX_2026_09.json", names)
        self.assertFalse(any(m.get("skipped") for m in manifest))

    def test_the_light_bundle_drops_the_heavy_parts(self):
        with _FakeRepo():
            content, manifest = dev_bundle.build_dev_bundle(light=True)
        names = _names(content)
        self.assertIn("config/user_access.json", names)
        self.assertIn("data/tga_lines_SLX_2026_09.csv", names)
        self.assertNotIn("data/allocations/SLX_2026_09.json", names)
        self.assertNotIn("data/baselines/SLX_2026_09.json", names)
        skipped = {m["part"] for m in manifest if m.get("skipped")}
        self.assertEqual(skipped, set(dev_bundle.HEAVY_PARTS))

    def test_unrelated_caches_and_logs_stay_out(self):
        """แคช payload กับ log มีปุ่มของตัวเอง/ก้อนใหญ่ — ไม่เอามาปนในชุดนี้"""
        with _FakeRepo():
            content, _ = dev_bundle.build_dev_bundle(light=False)
        names = _names(content)
        self.assertNotIn("data/payload_cache_SLX_2026_09.json", names)
        self.assertNotIn("data/logs/usage_2026-09-07.jsonl", names)

    def test_the_manifest_explains_what_is_inside(self):
        with _FakeRepo():
            content, _ = dev_bundle.build_dev_bundle()
        text = zipfile.ZipFile(io.BytesIO(content)).read("MANIFEST.txt").decode("utf-8")
        self.assertIn("อีเมลพนักงาน", text, "ต้องเตือนเรื่องข้อมูลส่วนบุคคล")
        self.assertIn("server_snapshot", text, "ต้องบอกว่าวางไฟล์ที่ไหนไม่ให้ทับ config")
        self.assertIn("git track", text, "ต้องเตือนกับดักเผลอ commit ทับของบน server")

    def test_the_filename_says_which_kind_it_is(self):
        self.assertIn("light", dev_bundle.bundle_filename(light=True))
        self.assertNotIn("light", dev_bundle.bundle_filename(light=False))


class TestTheEndpointIsGuarded(unittest.TestCase):
    """อ่านซอร์ส — แบบเดียวกับ tests/test_admin_permissions.py::TestWiring"""

    SRC = _read("backend/routers/admin.py")

    def _handler(self) -> str:
        i = self.SRC.index('@router.get("/dev-bundle")')
        return self.SRC[i : i + 2000]

    def test_it_is_dev_only(self):
        block = self._handler()
        self.assertIn("Depends(require_admin_user)", block)
        self.assertNotIn("require_admin_scoped", block)
        self.assertNotIn("require_capability", block)

    def test_it_is_audited(self):
        block = self._handler()
        self.assertIn("_audit_admin(", block)
        self.assertIn("admin_dev_bundle", block)

    def test_the_frontend_button_exists_and_calls_it(self):
        app = _read("frontend/app.js")
        html = _read("frontend/index.html")
        self.assertIn("/admin/dev-bundle", app)
        self.assertIn('id="devBundleLightBtn"', html)
        self.assertIn('id="devBundleFullBtn"', html)
        self.assertIn("adminDownloadDevBundle(true)", html)
        self.assertIn("adminDownloadDevBundle(false)", html)

    def test_the_button_lives_in_the_dev_only_tab(self):
        """แท็บ「แหล่งข้อมูล」ล็อกไว้ให้ dev อยู่แล้ว — ปุ่มต้องอยู่ในนั้น ไม่ใช่แท็บอื่น"""
        html = _read("frontend/index.html")
        i = html.index('data-panel="data"')
        j = html.index('data-panel="', i + 10)
        self.assertIn('id="devBundleFullBtn"', html[i:j])

    def test_the_capability_registry_still_locks_that_tab(self):
        from backend.services import admin_capabilities

        cap = admin_capabilities.CAPABILITIES["data_source"]
        self.assertEqual(cap["tab"], "data")
        self.assertEqual(
            tuple(cap["allowed_roles"]), (),
            "แท็บแหล่งข้อมูลต้องยังล็อกให้ dev เท่านั้น — มอบให้บทบาทอื่นไม่ได้",
        )


if __name__ == "__main__":
    unittest.main()
