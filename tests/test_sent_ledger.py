"""
เฟส F2: sent ledger — สิ่งที่ส่งเข้า Target Sun จริง ต่อทีม × งวด (ออฟไลน์ล้วน)
"""

from __future__ import annotations

import inspect
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import sent_ledger as sl  # noqa: E402
from backend.services import targetsun_import as ti  # noqa: E402


def _row(sku, emp, wh, qty):
    return {"PRODUCTCODE": sku, "SALESTYPE": "S", "DIVISIONCODE": "B", "SALESMANCODE": emp,
            "AREACODE": "10", "PROVINCECODE": "P1", "WAREHOUSECODE": wh, "QUANTITYCASE": qty}


class TestSentLedger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._p = patch.object(sl, "ledger_dir", return_value=self._tmp.name)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def test_later_partial_send_overwrites_only_its_keys(self):
        sl.record_send("SLA", 10, 2026, [_row("A", "E1", "", 5), _row("B", "E1", "W1", 3)],
                       token="t1", user="U1", send_status="ok")
        sl.record_send("SLA", 10, 2026, [_row("A", "E1", "", 7)], token="t2", user="U1", send_status="ok")
        led = sl.read_ledger("SLA", 10, 2026)
        qty = {k: v["qty"] for k, v in led["rows"].items()}
        self.assertEqual(sorted(qty.values()), [3, 7])
        self.assertEqual([s["token"] for s in led["sends"]], ["t1", "t2"])
        self.assertEqual(led["sends"][0]["boxes"], 8)

    def test_failed_send_not_recorded_unknown_is(self):
        self.assertFalse(sl.record_send("SLA", 10, 2026, [_row("A", "E1", "", 5)], send_status="failed"))
        self.assertIsNone(sl.read_ledger("SLA", 10, 2026))
        self.assertTrue(sl.record_send("SLA", 10, 2026, [_row("A", "E1", "", 5)], send_status="unknown"))

    def test_never_raises(self):
        self.assertFalse(sl.record_send("SLA", 10, 2026, [{"bad": 1}], send_status="ok"))

    def test_list(self):
        sl.record_send("SLA", 10, 2026, [_row("A", "E1", "", 5)], send_status="ok")
        self.assertEqual([(x["sup_id"], x["target_year"], x["target_month"]) for x in sl.list_ledgers()],
                         [("SLA", 2026, 10)])


class TestWiredIntoBothSendPaths(unittest.TestCase):
    def test_prepared_and_one_shot(self):
        self.assertIn("sent_ledger.record_send(", inspect.getsource(ti._keep_sent_record))
        self.assertIn('_keep_sent_record(token, meta, str(out.get("send_status") or ""))',
                      inspect.getsource(ti.import_prepared_targetsun))
        self.assertIn("sent_ledger.record_send(", inspect.getsource(ti._import_allocations_one_shot))


if __name__ == "__main__":
    unittest.main()
