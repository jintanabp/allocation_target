"""
เส้น API ของกล่องข้อเสนอแนะ — ใครส่งได้ ใครอ่านได้ และอีเมลมาจากไหน

จุดที่พังง่ายที่สุดคือ **dict ของผู้ใช้ทั่วไปไม่มีคีย์ `role`** (มีแต่ของแอดมิน)
ถ้าเผลออ่าน user["role"] ตรง ๆ เส้นส่งจะ 500 เฉพาะกับผู้ใช้จริง ซึ่งเราจะไม่เจอเลย
ตอนกดทดสอบด้วยบัญชีแอดมินของตัวเอง
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

from fastapi import HTTPException

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.routers import admin as admin_router  # noqa: E402
from backend.services import feedback_store as store  # noqa: E402

ADMIN = {"email": "boss@spc.co.th", "role": "head_admin", "is_admin": False}
# ผู้ใช้ทั่วไปหน้าตาแบบนี้จริง ๆ — ไม่มี "role" ไม่มี "sup_id" เดี่ยว ๆ
SUPERVISOR = {
    "email": "sup@spc.co.th",
    "is_admin": False,
    "is_marketing": False,
    "home_supervisor_codes": {"SL359"},
    "allowed_supervisor_codes": {"SL359"},
}


def _read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


class FeedbackApiTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="feedback_api_")
        self._prev_fb = os.environ.get("FEEDBACK_DIR")
        self._prev_logs = os.environ.get("USAGE_LOGS_DIR")
        os.environ["FEEDBACK_DIR"] = os.path.join(self._tmpdir, "feedback")
        os.environ["USAGE_LOGS_DIR"] = os.path.join(self._tmpdir, "logs")

    def tearDown(self):
        for key, prev in (("FEEDBACK_DIR", self._prev_fb), ("USAGE_LOGS_DIR", self._prev_logs)):
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _body(self, **kw):
        kw.setdefault("message", "ตารางขั้นที่ 3 โหลดช้ามากตอนเช้า")
        return admin_router.FeedbackSubmitBody(**kw)

    def test_a_normal_supervisor_can_send_and_the_email_comes_from_the_token(self):
        res = admin_router.admin_submit_feedback_from_user(
            self._body(sup_id="SL359", category="problem"),
            user=SUPERVISOR,
            user_agent="Mozilla/5.0 ทดสอบ",
        )
        self.assertTrue(res["ok"])
        row = store.read_items()[0]
        self.assertEqual(row["email"], "sup@spc.co.th")
        self.assertEqual(row["role_hint"], "supervisor")
        self.assertEqual(row["sup_id"], "SL359")
        self.assertIn("ทดสอบ", row["user_agent"])

    def test_the_browser_cannot_choose_whose_name_is_on_the_message(self):
        """body ไม่มีช่องอีเมลเลย — ถ้าใครเพิ่มเข้ามาทีหลัง เทสนี้จะแดง"""
        self.assertNotIn("email", admin_router.FeedbackSubmitBody.model_fields)

    def test_dev_machine_without_entra_can_still_send(self):
        res = admin_router.admin_submit_feedback_from_user(
            self._body(), user={"auth_disabled": True, "email": None}, user_agent=None
        )
        self.assertTrue(res["ok"])
        self.assertEqual(store.read_items()[0]["email"], "")

    def test_view_as_keeps_both_names(self):
        user = dict(SUPERVISOR, view_as_email="sup@spc.co.th", acting_admin_email="dev@spc.co.th")
        admin_router.admin_submit_feedback_from_user(self._body(), user=user, user_agent=None)
        self.assertEqual(store.read_items()[0]["acting_admin_email"], "dev@spc.co.th")

    def test_sending_also_leaves_one_line_in_the_usage_log(self):
        from backend.services.usage_log_store import read_logs

        admin_router.admin_submit_feedback_from_user(self._body(), user=SUPERVISOR, user_agent=None)
        actions = [r.get("action") for r in read_logs(limit=50)]
        self.assertIn("feedback_submit", actions)

    def test_spamming_is_answered_with_a_thai_message_not_a_crash(self):
        for _ in range(store.RATE_LIMIT_PER_EMAIL):
            admin_router.admin_submit_feedback_from_user(self._body(), user=SUPERVISOR, user_agent=None)
        with self.assertRaises(HTTPException) as ctx:
            admin_router.admin_submit_feedback_from_user(self._body(), user=SUPERVISOR, user_agent=None)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("ถี่เกินไป", ctx.exception.detail)

    def test_admin_sees_the_message_and_can_mark_it_handled(self):
        admin_router.admin_submit_feedback_from_user(self._body(), user=SUPERVISOR, user_agent=None)
        listed = admin_router.admin_list_feedback(status="", category="", limit=200, _admin=ADMIN)
        self.assertEqual(listed["counts"]["new"], 1)
        fid = listed["items"][0]["id"]

        out = admin_router.admin_set_feedback_status(
            fid,
            admin_router.FeedbackStatusBody(status="done", admin_note="โทรคุยแล้ว"),
            admin=ADMIN,
        )
        self.assertEqual(out["item"]["status"], "done")
        self.assertEqual(out["item"]["handled_by"], "boss@spc.co.th")
        self.assertEqual(out["item"]["admin_note"], "โทรคุยแล้ว")

    def test_a_stale_screen_gets_409_instead_of_overwriting_the_other_admin(self):
        admin_router.admin_submit_feedback_from_user(self._body(), user=SUPERVISOR, user_agent=None)
        fid = store.read_items()[0]["id"]
        admin_router.admin_set_feedback_status(
            fid, admin_router.FeedbackStatusBody(status="read", expected_rev=0), admin=ADMIN
        )
        with self.assertRaises(HTTPException) as ctx:
            admin_router.admin_set_feedback_status(
                fid, admin_router.FeedbackStatusBody(status="done", expected_rev=0), admin=ADMIN
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_unknown_id_is_404(self):
        with self.assertRaises(HTTPException) as ctx:
            admin_router.admin_set_feedback_status(
                "ไม่มีจริง", admin_router.FeedbackStatusBody(status="done"), admin=ADMIN
            )
        self.assertEqual(ctx.exception.status_code, 404)


class FeedbackWiringTest(unittest.TestCase):
    """
    เส้น "ส่ง" กับเส้น "อ่าน" ใช้คนละ gate — ถ้ามีใครรวม path ให้เหมือนกันทีหลัง
    กล่องข้อเสนอแนะทั้งกล่องจะเปิดให้ทุกคนอ่าน เทสชุดนี้อ่าน source ตรง ๆ เพื่อกันข้อนั้น
    """

    def setUp(self):
        self.src = _read("backend/routers/admin.py")

    def test_submit_route_is_open_to_any_signed_in_user(self):
        block = self.src.split('@router.post("/feedback/submit")')[1].split("@router.")[0]
        self.assertIn("Depends(require_authenticated_user)", block)
        self.assertNotIn("require_capability", block)

    def test_reading_and_changing_status_need_the_capability(self):
        for marker in ('@router.get("/feedback")', '@router.post("/feedback/{feedback_id}/status")'):
            block = self.src.split(marker)[1].split("@router.")[0]
            self.assertIn('require_capability("feedback")', block, marker)

    def test_the_reason_for_living_under_admin_is_written_down(self):
        self.assertIn("prefix", self.src.split("ข้อเสนอแนะจากผู้ใช้")[1][:1200])

    def test_the_frontend_still_points_at_the_same_paths(self):
        app_js = _read("frontend/app.js")
        self.assertIn("/admin/feedback/submit", app_js)
        self.assertIn('data-tab="feedback"', _read("frontend/index.html"))

    def test_the_audit_action_name_is_stable(self):
        self.assertIn('"admin_feedback_status"', self.src)


if __name__ == "__main__":
    unittest.main()
