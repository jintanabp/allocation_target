"""
บัญชีสาธิต — ให้ dev กด "ดูแบบนี้" แล้วโชว์ระบบให้ผู้ใช้ดูโดยไม่แตะข้อมูลจริง

สิ่งที่ต้องจริงเท่าของจริง:
  - ทำได้ทั้งโหมด "รายคน" และ "รวมภาค" (จึงต้องมีหลายทีมในภาคเดียวกัน)
  - ตัวเลขสอดคล้องกันเอง: เป้ารวมภาค = ผลบวกเป้ารายทีม

สิ่งที่ต้องไม่จริงเด็ดขาด:
  - ส่งเข้า Target Sun ไม่ได้ (กันสองชั้น)
  - ไม่แตะ Fabric / เน็ต
  - ผู้ใช้จริงต้องไม่เห็นทีมสาธิตปนในรายชื่อทีมของตัวเอง
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend import deps  # noqa: E402
from backend.services import access_control as ac  # noqa: E402
from backend.services import demo_data  # noqa: E402
from backend.services import employees as emp_svc  # noqa: E402


def _roster_path() -> str:
    """
    รายชื่อผู้ใช้ที่จะตรวจ — ไฟล์จริงถ้ามี ไม่มีก็ไฟล์ต้นแบบ

    config/user_access.json ไม่อยู่ใน git แล้ว (แอดมินแก้บนเซิร์ฟเวอร์ผ่านหน้าเว็บ
    การ track ไว้ทำให้ pull เขียนทับจนคนที่เพิ่งเพิ่มหายไป) · บนเครื่อง dev และ
    เซิร์ฟเวอร์จะมีไฟล์จริง ส่วนบน CI ตรวจไฟล์ต้นแบบซึ่งเป็นที่มาของการติดตั้งใหม่
    """
    real = os.path.join(REPO, "config", "user_access.json")
    if os.path.isfile(real):
        return real
    return os.path.join(REPO, "config", "user_access.example.json")


class TestDemoRosterRows(unittest.TestCase):
    """สามบัญชีต้องอยู่ในรายชื่อผู้ใช้และตั้งค่าถูก"""

    @classmethod
    def setUpClass(cls):
        # config/user_access.json ไม่อยู่ใน git แล้ว (แอดมินแก้บนเซิร์ฟเวอร์ผ่านหน้าเว็บ
        # การ track ไว้ทำให้ pull เขียนทับจนคนที่เพิ่งเพิ่มหาย) — บนเครื่อง dev/เซิร์ฟเวอร์
        # จะมีไฟล์จริงให้ตรวจ ส่วนบน CI ตรวจไฟล์ต้นแบบซึ่งเป็นที่มาของการติดตั้งใหม่
        path = _roster_path()
        cls.roster_path = path
        with open(path, encoding="utf-8") as fh:
            cls.rows = json.load(fh)
        cls.by_email = {str(r.get("email", "")).lower(): r for r in cls.rows}

    def test_all_three_accounts_exist(self):
        for em in demo_data.DEMO_EMAILS:
            self.assertIn(em, self.by_email, f"ไม่พบบัญชีสาธิต {em}")

    def test_none_of_them_can_send(self):
        """ชั้นที่หนึ่งของการกันส่ง — ค่าในไฟล์"""
        for em in demo_data.DEMO_EMAILS:
            with self.subTest(email=em):
                self.assertFalse(
                    self.by_email[em].get("can_import_targetsun"),
                    "บัญชีสาธิตต้องไม่มีสิทธิ์ส่ง",
                )

    def test_demosuper_is_a_plain_supervisor(self):
        r = self.by_email["demosuper@sahapat.co.th"]
        self.assertEqual(r["login_kind"], "supervisor_acc")
        self.assertNotIn("role", r, "บัญชีนี้ต้องไม่มีสิทธิ์ดูแลระบบ")

    def test_demoadmin_is_admin_only_without_a_position(self):
        r = self.by_email["demoadmin@sahapat.co.th"]
        self.assertEqual(r.get("role"), "admin")
        self.assertEqual(str(r.get("userpl") or ""), "", "แอดมินอย่างเดียวไม่มีรหัส SL")

    def test_demosuperwithadmin_overlays_both(self):
        """โจทย์คือ 'เป็น super ที่มีสิทธิแอดมิน' — ต้องมีทั้งสองอย่างพร้อมกัน"""
        r = self.by_email["demosuperwithadmin@sahapat.co.th"]
        self.assertEqual(r["login_kind"], "supervisor_acc")
        self.assertEqual(r.get("role"), "admin")
        self.assertTrue(str(r.get("userpl") or "").strip())

    def test_demo_region_is_its_own(self):
        """ใช้ชื่อภาคเฉพาะกิจ ไม่งั้น region_peers จะลากทีมจริงเข้ามาปนในเดโม"""
        for em in ("demosuper@sahapat.co.th", "demosuperwithadmin@sahapat.co.th"):
            with self.subTest(email=em):
                self.assertEqual(self.by_email[em].get("acc_region"), demo_data.DEMO_REGION)


class TestDemoAccessScope(unittest.TestCase):
    """
    ตรวจขอบเขตจริงผ่าน build_user_access_context — ต้องบังคับ path ให้ตรงกับ
    _roster_path() ไม่งั้นบน CI (ไม่มีไฟล์จริง) จะพังด้วย PermissionError
    """

    @classmethod
    def setUpClass(cls):
        cls._old_path = os.environ.get("USER_ACCESS_JSON_PATH")
        os.environ["USER_ACCESS_JSON_PATH"] = _roster_path()
        ac.invalidate_user_access_cache()

    @classmethod
    def tearDownClass(cls):
        if cls._old_path is None:
            os.environ.pop("USER_ACCESS_JSON_PATH", None)
        else:
            os.environ["USER_ACCESS_JSON_PATH"] = cls._old_path
        ac.invalidate_user_access_cache()

    def test_demosuper_sees_all_three_demo_teams(self):
        ctx = ac.build_user_access_context("demosuper@sahapat.co.th")
        self.assertEqual(
            sorted(ctx["allowed_supervisor_codes"]), sorted(demo_data.DEMO_SUP_IDS)
        )

    def test_demosuper_sees_no_real_team(self):
        ctx = ac.build_user_access_context("demosuper@sahapat.co.th")
        for code in ctx["allowed_supervisor_codes"]:
            self.assertTrue(
                demo_data.is_demo_supervisor(code),
                f"บัญชีสาธิตต้องไม่เห็นทีมจริง แต่เห็น {code}",
            )

    def test_demoadmin_sees_no_team_at_all(self):
        ctx = ac.build_user_access_context("demoadmin@sahapat.co.th")
        self.assertEqual(set(ctx["allowed_supervisor_codes"]), set())
        self.assertEqual(ac.role_for_email("demoadmin@sahapat.co.th"), ac.ROLE_REGION_ADMIN)

    def test_demosuperwithadmin_has_a_team_and_admin_rights(self):
        em = "demosuperwithadmin@sahapat.co.th"
        ctx = ac.build_user_access_context(em)
        self.assertTrue(ctx["allowed_supervisor_codes"], "ต้องยังมีทีมของตัวเอง")
        self.assertEqual(ac.role_for_email(em), ac.ROLE_REGION_ADMIN)
        self.assertFalse(ctx.get("is_admin"), "ต้องไม่ใช่ dev")

    def test_no_demo_account_can_send(self):
        for em in demo_data.DEMO_EMAILS:
            with self.subTest(email=em):
                ctx = ac.build_user_access_context(em)
                self.assertFalse(ctx.get("can_import_targetsun"))


class TestDemoDataIsUsableLikeTheRealThing(unittest.TestCase):
    def setUp(self):
        self.payloads = {
            c: emp_svc.load_employees_payload(c, 9, 2026) for c in demo_data.DEMO_SUP_IDS
        }

    def test_each_team_has_its_own_people(self):
        seen = set()
        for code, p in self.payloads.items():
            ids = {e["emp_id"] for e in p["employees"]}
            self.assertTrue(ids, f"{code} ต้องมีพนักงาน")
            self.assertFalse(ids & seen, "พนักงานต้องไม่ซ้ำข้ามทีม")
            seen |= ids

    def test_teams_are_different_sizes(self):
        """ทีมขนาดเท่ากันหมดดูปลอม — เดโมควรเหมือนของจริง"""
        sizes = {len(p["employees"]) for p in self.payloads.values()}
        self.assertGreater(len(sizes), 1)

    def test_every_sku_has_a_target_and_a_price(self):
        for code, p in self.payloads.items():
            for s in p["skus"]:
                with self.subTest(team=code, sku=s["sku"]):
                    self.assertGreater(int(s["supervisor_target_boxes"]), 0)
                    self.assertGreater(float(s["price_per_box"]), 0)
                    self.assertFalse(s["price_missing"])

    def test_aggregate_total_equals_sum_of_teams(self):
        """หัวใจของโหมดรวมภาค — ถ้าไม่ตรงตั้งแต่ข้อมูลเดโม ก็สาธิตไม่ได้"""
        merged = emp_svc.merge_employees_payloads(
            [self.payloads[c] for c in demo_data.DEMO_SUP_IDS],
            aggregate_label="รวมภาคสาธิต",
            aggregate_sup_ids=list(demo_data.DEMO_SUP_IDS),
        )
        got = sum(int(s["supervisor_target_boxes"]) for s in merged["skus"])
        want = sum(
            int(s["supervisor_target_boxes"])
            for c in demo_data.DEMO_SUP_IDS
            for s in self.payloads[c]["skus"]
        )
        self.assertEqual(got, want)

    def test_aggregate_keeps_everyone(self):
        merged = emp_svc.merge_employees_payloads(
            [self.payloads[c] for c in demo_data.DEMO_SUP_IDS],
            aggregate_label="รวมภาคสาธิต",
            aggregate_sup_ids=list(demo_data.DEMO_SUP_IDS),
        )
        want = sum(len(p["employees"]) for p in self.payloads.values())
        self.assertEqual(len(merged["employees"]), want)
        self.assertEqual(
            sorted({e["super_code"] for e in merged["employees"]}),
            sorted(demo_data.DEMO_SUP_IDS),
        )

    def test_payload_is_stable_across_calls(self):
        """เดโมต้องซ้ำได้ — ตัวเลขเปลี่ยนทุกครั้งจะอธิบายให้ผู้ใช้ฟังไม่ได้"""
        again = emp_svc.load_employees_payload(demo_data.DEMO_SUP_ID, 9, 2026)
        self.assertEqual(
            json.dumps(again["employees"], sort_keys=True, ensure_ascii=False),
            json.dumps(self.payloads[demo_data.DEMO_SUP_ID]["employees"], sort_keys=True, ensure_ascii=False),
        )

    def test_it_says_it_is_a_demo(self):
        p = self.payloads[demo_data.DEMO_SUP_ID]
        self.assertTrue(p.get("is_demo"))
        msgs = " ".join(w.get("message", "") for w in p["sku_warnings"])
        self.assertIn("สมมติ", msgs, "ต้องมีคำเตือนบนหน้าจอว่าเป็นข้อมูลสมมติ")

    def test_it_never_touches_fabric(self):
        original = emp_svc.FabricDAXConnector

        class _Boom:
            def __init__(self, *a, **k):
                raise AssertionError("ทีมสาธิตต้องไม่ต่อ Fabric")

        emp_svc.FabricDAXConnector = _Boom          # type: ignore[assignment]
        try:
            for code in demo_data.DEMO_SUP_IDS:
                emp_svc.load_employees_payload(code, 9, 2026)
        finally:
            emp_svc.FabricDAXConnector = original   # type: ignore[assignment]


class TestDemoWritesTheSameCachesAsStep1(unittest.TestCase):
    """
    ขั้นกระจายหีบ / ดาวน์โหลด Excel อ่านเป้าและประวัติจาก "ไฟล์ cache"
    ไม่ได้อ่านจาก payload ที่ส่งไปหน้าเว็บ

    ถ้าไม่เขียนไฟล์พวกนี้ ทีมสาธิตจะเปิดหน้าจอได้แต่กดเริ่มคำนวณไม่ได้
    (ขึ้น "ไม่พบเป้าหีบของทีมนี้") ซึ่งเดโมไม่จบ
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_writes_every_file_the_next_steps_need(self):
        demo_data.write_demo_caches(demo_data.DEMO_SUP_ID, 9, 2026)
        code = demo_data.DEMO_SUP_ID
        for name in (
            f"target_boxes_{code}_2026_09.csv",
            f"target_sun_{code}_2026_09.csv",
            f"emp_cache_{code}_2026_09.csv",
            f"hist_cache_{code}_2026_09.csv",
            f"hist_cache_{code}_2026_09_6m.csv",
            f"tga_lines_{code}_2026_09.csv",
        ):
            with self.subTest(file=name):
                self.assertTrue(os.path.isfile(os.path.join("data", name)), f"ไม่ได้เขียน {name}")

    def test_target_boxes_file_matches_the_payload(self):
        """ตัวเลขในไฟล์กับที่หน้าจอเห็นต้องเป็นชุดเดียวกัน ไม่งั้นตรวจยอดไม่ผ่าน"""
        code = demo_data.DEMO_SUP_ID
        demo_data.write_demo_caches(code, 9, 2026)
        with open(os.path.join("data", f"target_boxes_{code}_2026_09.csv"), encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        from_file = {r["sku"]: int(r["supervisor_target_boxes"]) for r in rows}
        from_payload = {s["sku"]: int(s["supervisor_target_boxes"]) for s in demo_data.demo_skus(code)}
        self.assertEqual(from_file, from_payload)

    def test_writing_twice_is_stable(self):
        code = demo_data.DEMO_SUP_ID
        path = os.path.join("data", f"target_boxes_{code}_2026_09.csv")
        demo_data.write_demo_caches(code, 9, 2026)
        with open(path, encoding="utf-8") as fh:
            first = fh.read()
        demo_data.write_demo_caches(code, 9, 2026)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), first)

    def test_loading_the_payload_writes_the_caches(self):
        """เส้นทางจริง: หน้าเว็บเรียก /data/employees แล้วต้องพร้อมกดคำนวณต่อได้เลย"""
        emp_svc.load_employees_payload(demo_data.DEMO_SUP_ID, 9, 2026)
        self.assertTrue(
            os.path.isfile(
                os.path.join("data", f"target_boxes_{demo_data.DEMO_SUP_ID}_2026_09.csv")
            )
        )


class TestDemoCannotBeSent(unittest.TestCase):
    def test_every_demo_team_is_blocked(self):
        for code in demo_data.DEMO_SUP_IDS:
            with self.subTest(code=code):
                with self.assertRaises(HTTPException) as cm:
                    deps.ensure_demo_team_not_sent(code)
                self.assertEqual(cm.exception.status_code, 403)

    def test_real_team_is_not_blocked(self):
        deps.ensure_demo_team_not_sent("SL346")   # ต้องไม่โยน

    def test_lowercase_and_spaces_still_blocked(self):
        with self.assertRaises(HTTPException):
            deps.ensure_demo_team_not_sent("  sldemo1 ")

    def test_guard_is_wired_into_every_send_entry_point(self):
        with open(os.path.join(REPO, "backend", "routers", "lakehouse.py"), encoding="utf-8") as fh:
            src = fh.read()
        for route in ("prepare-targetsun", "import-targetsun", "verify-send-batch"):
            with self.subTest(route=route):
                idx = src.index(route)
                nxt = src.find("@router.", idx)
                body = src[idx: nxt if nxt > 0 else len(src)]
                self.assertIn("ensure_demo_team_not_sent", body)


class TestDemoDoesNotLeakToRealUsers(unittest.TestCase):
    def test_real_managers_payload_has_no_demo_team(self):
        from backend.services import managers as msvc

        full = msvc.load_full_managers_payload()
        sups = set(map(str, full.get("supervisors") or []))
        self.assertFalse(sups & set(demo_data.DEMO_SUP_IDS))

    def test_injection_does_not_mutate_the_shared_payload(self):
        """payload ตัวจริงถูกแคชใช้ร่วมกันทั้งระบบ — แก้ทับ = ผู้ใช้จริงเห็นทีมสาธิต"""
        from backend.services import managers as msvc

        full = msvc.load_full_managers_payload()
        before = list(full.get("supervisors") or [])
        out = demo_data.inject_into_managers_payload(full)
        self.assertEqual(list(full.get("supervisors") or []), before)
        for code in demo_data.DEMO_SUP_IDS:
            self.assertIn(code, out["supervisors"])

    def test_injection_is_idempotent(self):
        payload = {"supervisors": [], "managers": [], "rows": []}
        once = demo_data.inject_into_managers_payload(payload)
        twice = demo_data.inject_into_managers_payload(once)
        self.assertEqual(len(twice["supervisors"]), len(demo_data.DEMO_SUP_IDS))
        self.assertEqual(len(twice["rows"]), len(demo_data.DEMO_SUP_IDS))


if __name__ == "__main__":
    unittest.main()
