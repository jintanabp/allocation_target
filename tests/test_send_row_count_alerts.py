"""
นับแถว Target Sun ก่อน/หลังส่ง + แจ้งเตือน (ผู้ใช้ตัดสิน 29 ก.ย. 2026)

  - หลังส่ง = ก่อนส่ง + คู่ใหม่ในไฟล์ ต่างแม้แถวเดียว = แจ้ง
  - ตรวจไม่ได้ (อ่านไม่ได้ / อ่านไม่ครบ / อ่านกับส่งคนละระบบ) = แจ้งเหมือนกัน
  - ผู้รับ: คนกดส่ง + เจ้าของ SL (รวมภาค = ทุก SL ในรอบ) + dev + แอดมินในขอบเขต
  - แจ้งผ่านกล่องในแอป

ไม่มีเทสไหนยิง Target Sun จริง — requests ถูก mock ทั้งหมด
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from fastapi import HTTPException  # noqa: E402

from backend.services import lakehouse as lh  # noqa: E402
from backend.services import notification_store as ns  # noqa: E402
from backend.services import send_alerts as sa  # noqa: E402
from backend.services import targetsun_import as ti  # noqa: E402
from backend.services import targetsun_read as tsr  # noqa: E402

logging.disable(logging.CRITICAL)

_BASES = ("https://read.example.test/spc/targetsun", "https://import.example.test/spc/targetsun")


def _resp(result: dict):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"success": True, "result": result, "resultMsg": "ok"}
    return r


def _row(emp: str, sku: str, qty: int = 1) -> dict:
    return {
        "PRODUCTCODE": sku, "SALESMANCODE": emp, "QUANTITYCASE": qty, "SALESTYPE": "S",
        "DIVISIONCODE": "B", "AREACODE": "1", "PROVINCECODE": "", "WAREHOUSECODE": None,
    }


class TestReadIsCompleteOrFlagged(unittest.TestCase):
    def _fetch(self, responses, codes):
        with patch("backend.services.targetsun_read.requests.request", side_effect=responses) as m, \
                patch("backend.services.targetsun_endpoints.resolve_endpoint_bases", return_value=_BASES):
            out = tsr.fetch_target_rows(2026, 9, codes)
        return out, m

    def test_more_than_200_codes_are_split(self):
        codes = [f"{i:05d}" for i in range(1, 251)]
        out, m = self._fetch(
            [_resp({"rowCount": 1, "rows": [_row("00001", "A")]}),
             _resp({"rowCount": 1, "rows": [_row("00201", "A")]})],
            codes,
        )
        self.assertEqual(m.call_count, 2)
        sent = [len(c.kwargs["json"]["salesmanCodes"]) for c in m.call_args_list]
        self.assertEqual(sent, [200, 50])
        self.assertEqual(out["rowCount"], 2)
        self.assertTrue(out["complete"])

    def test_follows_has_more_pages(self):
        out, m = self._fetch(
            [_resp({"rows": [_row("00001", "A")], "hasMore": True, "totalRows": 2}),
             _resp({"rows": [_row("00001", "B")], "hasMore": False, "totalRows": 2})],
            ["1"],
        )
        self.assertEqual(m.call_args_list[1].kwargs["json"]["page"], 2)
        self.assertEqual(out["rowCount"], 2)
        self.assertTrue(out["complete"])

    def test_row_count_disagreeing_with_rows_is_incomplete(self):
        out, _ = self._fetch([_resp({"rowCount": 5, "rows": [_row("00001", "A")]})], ["1"])
        self.assertFalse(out["complete"])

    def test_server_ignoring_page_param_is_incomplete_not_double_counted(self):
        page = {"rows": [_row("00001", "A")], "hasMore": True}
        out, _ = self._fetch([_resp(page), _resp(page)], ["1"])
        self.assertFalse(out["complete"])
        self.assertEqual(out["rowCount"], 1, "หน้าเดิมซ้ำต้องไม่ถูกนับสองรอบ")

    def test_snapshot_refuses_to_count_an_incomplete_read(self):
        with patch.object(tsr, "is_enabled", return_value=True), \
                patch.object(tsr, "get_target_read_source", return_value="targetsun"), \
                patch.object(tsr, "fetch_target_rows",
                             return_value={"rows": [_row("00001", "A")], "complete": False}):
            self.assertIsNone(lh._live_target_snapshot("SLA", 9, 2026, ["00001"]))


class TestRowCountVerifier(unittest.TestCase):
    def test_cross_env_is_reported_as_unverifiable(self):
        with patch("backend.services.targetsun_endpoints.targetsun_endpoints_summary",
                   return_value={"cross_env": "1"}):
            rc = lh.verify_row_count_after_send(
                "SLA", 9, 2026, emp_codes=["1"],
                before_snapshot={"row_count": 3, "keys": set()}, file_keys=set(),
            )
        self.assertEqual(rc, {"checked": False, "reason": "cross_env"})


class TestAlertDecision(unittest.TestCase):
    def test_exact_count_is_quiet(self):
        self.assertIsNone(sa.row_count_alert({"checked": True, "ok": True}))

    def test_one_row_off_alerts(self):
        a = sa.row_count_alert({
            "checked": True, "ok": False, "before_count": 10, "after_count": 13,
            "expected_new_rows": 2, "unexpected_extra_rows": 1,
        })
        self.assertEqual(a["status"], "mismatch")
        self.assertIn("เกิน 1 แถว", a["text"])

    def test_every_unverifiable_reason_alerts(self):
        for reason in ("before_unavailable", "after_unavailable", "cross_env", "error", ""):
            with self.subTest(reason=reason):
                a = sa.row_count_alert({"checked": False, "reason": reason})
                self.assertEqual(a["status"], "unverified")

    def test_missing_result_alerts(self):
        self.assertEqual(sa.row_count_alert(None)["status"], "unverified")

    def test_failed_send_does_not_alert(self):
        self.assertIsNone(sa.row_count_alert({"checked": False, "reason": "send_failed"}))


_ROWS = [
    {"email": "owner.a@x.co", "userpl": "SLA", "login_kind": "supervisor_acc"},
    {"email": "owner.b@x.co", "userpl": "SLB", "login_kind": "supervisor_acc"},
    {"email": "other@x.co", "userpl": "SLZ", "login_kind": "supervisor_acc"},
    {"email": "mgr@x.co", "userpl": "SLA", "login_kind": "manager_acc"},
    {"email": "admin.in@x.co", "userpl": "", "role": "admin", "login_kind": "standard"},
    {"email": "admin.out@x.co", "userpl": "", "role": "admin", "login_kind": "standard"},
    {"email": "dev.row@x.co", "userpl": "", "role": "dev", "login_kind": "standard"},
]


def _scope(email):
    return {"sl_codes": {"SLA", "SLB"} if email == "admin.in@x.co" else {"SLZ"}}


class TestRecipients(unittest.TestCase):
    def setUp(self):
        self._p = [
            patch.object(sa, "read_rows", return_value=_ROWS),
            patch.object(sa, "admin_scope_for_email", side_effect=_scope),
            patch.object(sa, "parse_allocation_admin_emails", return_value={"dev.env@x.co"}),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()

    def test_single_team(self):
        self.assertEqual(
            sa.recipients_for("sender@x.co", ["SLA"]),
            ["admin.in@x.co", "dev.env@x.co", "dev.row@x.co", "owner.a@x.co", "sender@x.co"],
        )

    def test_batch_reaches_every_team_owner(self):
        got = sa.recipients_for("sender@x.co", ["SLA", "SLB"])
        self.assertIn("owner.a@x.co", got)
        self.assertIn("owner.b@x.co", got)
        self.assertNotIn("other@x.co", got)
        self.assertNotIn("admin.out@x.co", got, "แอดมินนอกขอบเขตไม่ต้องได้")


class TestNotificationStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTIFICATIONS_DIR"] = self._tmp.name

    def tearDown(self):
        os.environ.pop("NOTIFICATIONS_DIR", None)
        self._tmp.cleanup()

    def test_create_list_ack(self):
        item = ns.create(kind="k", title="t", message="m", recipients=["A@x.co", "b@x.co"])
        self.assertEqual(ns.unread_count("a@x.co"), 1)
        self.assertEqual(ns.list_for("b@x.co")[0]["id"], item["id"])
        self.assertNotIn("recipients", ns.list_for("b@x.co")[0], "ไม่เปิดเผยรายชื่อผู้รับคนอื่น")
        self.assertTrue(ns.acknowledge("a@x.co", item["id"]))
        self.assertEqual(ns.unread_count("a@x.co"), 0)
        self.assertEqual(ns.unread_count("b@x.co"), 1, "คนหนึ่งรับทราบ อีกคนยังต้องเห็น")
        self.assertTrue(ns.list_for("a@x.co", include_acked=True)[0]["acked"])

    def test_non_recipient_cannot_ack_or_see(self):
        item = ns.create(kind="k", title="t", message="m", recipients=["a@x.co"])
        self.assertFalse(ns.acknowledge("c@x.co", item["id"]))
        self.assertEqual(ns.list_for("c@x.co"), [])

    def test_no_recipients_is_not_stored(self):
        self.assertIsNone(ns.create(kind="k", title="t", message="m", recipients=["", "no-at"]))

    def test_store_lives_under_data_not_config(self):
        os.environ.pop("NOTIFICATIONS_DIR", None)
        p = ns.notifications_json_path().replace("\\", "/")
        self.assertIn("/data/notifications/", p)
        os.environ["NOTIFICATIONS_DIR"] = self._tmp.name

    def test_notify_creates_one_item_for_everyone(self):
        with patch.object(sa, "read_rows", return_value=_ROWS), \
                patch.object(sa, "admin_scope_for_email", side_effect=_scope), \
                patch.object(sa, "parse_allocation_admin_emails", return_value=set()):
            item = sa.notify_row_count_issue(
                user={"email": "sender@x.co"}, sup_id="SLA", target_month=9, target_year=2026,
                row_count={"checked": True, "ok": False, "before_count": 1, "after_count": 3,
                           "expected_new_rows": 1, "unexpected_extra_rows": 1},
                batch_id="run1", batch_sup_ids=["SLA", "SLB"],
            )
        self.assertIsNotNone(item)
        for em in ("sender@x.co", "owner.a@x.co", "owner.b@x.co", "dev.row@x.co", "admin.in@x.co"):
            self.assertEqual(ns.unread_count(em), 1, em)
        self.assertEqual(item["context"]["send_batch_id"], "run1")

    def test_view_as_sender_names_the_dev_too(self):
        with patch.object(sa, "read_rows", return_value=[]), \
                patch.object(sa, "parse_allocation_admin_emails", return_value=set()):
            item = sa.notify_row_count_issue(
                user={"email": "sup@x.co", "view_as_email": "sup@x.co", "acting_admin_email": "dev@x.co"},
                sup_id="SLA", target_month=9, target_year=2026,
                row_count={"checked": False, "reason": "after_unavailable"},
            )
        self.assertIn("dev@x.co (ดูแทน sup@x.co)", item["message"])
        self.assertEqual(ns.unread_count("dev@x.co"), 1)

    def test_normal_send_creates_nothing(self):
        self.assertIsNone(sa.notify_row_count_issue(
            user={"email": "s@x.co"}, sup_id="SLA", target_month=9, target_year=2026,
            row_count={"checked": True, "ok": True},
        ))
        self.assertEqual(ns.unread_count("s@x.co"), 0)

    def test_notify_never_raises(self):
        with patch.object(sa, "recipients_for", side_effect=RuntimeError("boom")):
            self.assertIsNone(sa.notify_row_count_issue(
                user={"email": "s@x.co"}, sup_id="SLA", target_month=9, target_year=2026,
                row_count={"checked": False, "reason": "error"},
            ))


class TestTeamSendLock(unittest.TestCase):
    def test_second_send_of_same_team_and_period_is_refused(self):
        key = ti._claim_team_send("sla", 9, 2026)
        try:
            with self.assertRaises(HTTPException) as ctx:
                ti._claim_team_send("SLA", 9, 2026)
            self.assertEqual(ctx.exception.detail["code"], "team_send_in_progress")
            ti._release_team_send(ti._claim_team_send("SLA", 10, 2026))  # คนละงวดได้
        finally:
            ti._release_team_send(key)
        ti._release_team_send(ti._claim_team_send("SLA", 9, 2026))  # ปล่อยแล้วส่งได้


class TestRouterWiring(unittest.TestCase):
    def test_import_route_alerts_after_logging(self):
        import inspect

        from backend.routers import lakehouse as rl

        src = inspect.getsource(rl.import_targetsun_from_allocations)
        self.assertLess(src.index("_log_targetsun_send("), src.index("_alert_row_count("))

    def test_notification_routes_live_under_data_prefix(self):
        from backend.routers import data as rd

        paths = {r.path for r in rd.router.routes}
        self.assertIn("/data/notifications", paths)
        self.assertIn("/data/notifications/{item_id}/ack", paths)


if __name__ == "__main__":
    unittest.main()
