"""
เครื่องนับของรายงาน "สรุปการใช้งาน"

ตัวเลขชุดนี้ผิดแล้วดูไม่ออก — เปอร์เซ็นต์ยังสมเหตุสมผลอยู่เสมอ จึงตรึงทุกกฎ
ที่เคยพลาดได้ไว้ตรงนี้:
  - คนที่ได้ 0 หีบไม่ใช่ "ถูกกระจายเป้า"
  - ทีมที่กดกระจายแล้วได้ 0 ทั้งทีม ไม่ใช่ "เข้ามาใช้"
  - รหัสพนักงานซ้ำข้ามทีมต้องนับสองครั้ง (invariant I7)
  - บันทึกการส่งรุ่นเก่าไม่มีฟิลด์งวด ต้องแกะจากข้อความ "งวด YYYY-MM" ได้
  - ผลรวมรายภาคต้องเท่ากับตัวเลขรวมเป๊ะ ๆ (หลักฐานว่าไม่มีการนับซ้ำ)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import fabric_cache as fc  # noqa: E402
from backend.services import usage_summary as us  # noqa: E402

M, Y = 9, 2026


def _ua(userpl, kind="supervisor_acc", region="เหนือ", **kw):
    row = {
        "email": f"{userpl.lower()}@x.com", "userpl": userpl, "login_kind": kind,
        "acc_region": region, "acc_division": "Div.B", "acc_unit": "credit",
        "full_name": f"ทีม {userpl}",
    }
    row.update(kw)
    return row


USERS = [
    _ua("SL100"),
    _ua("SL200"),
    _ua("SL300", kind="manager_acc", region="ใต้", manager_level="regional"),
    _ua("SL400", kind="manager_acc", region="none", manager_level="division"),
    {"email": "admin@x.com", "userpl": "none", "login_kind": "standard", "role": "admin"},
]

# E2 อยู่ทั้ง SL100 และ SL200 — คนละคนที่บังเอิญรหัสซ้ำ ต้องนับสองครั้ง
ROSTER = [
    {"emp_id": "E1", "emp_name": "หนึ่ง", "super_code": "SL100"},
    {"emp_id": "E2", "emp_name": "สอง", "super_code": "SL100"},
    {"emp_id": "E3", "emp_name": "สาม", "super_code": "SL100"},
    {"emp_id": "V9", "emp_name": "รถเงินสด", "super_code": "SL100"},
    {"emp_id": "E2", "emp_name": "สองของอีกทีม", "super_code": "SL200"},
    {"emp_id": "E9", "emp_name": "เก้า", "super_code": "SL200"},
    {"emp_id": "E4", "emp_name": "สี่", "super_code": "SL300"},
    {"emp_id": "E5", "emp_name": "ห้า", "super_code": "SL400"},
    {"emp_id": "E8", "emp_name": "แปด", "super_code": "SL999"},   # รหัสที่กระจายไม่ได้
]


def _snap(sup, rows, status="optimized", sent_at=""):
    doc = {
        "sup_id": sup, "target_month": M, "target_year": Y, "status": status,
        "allocations": [
            {"emp_id": e, "sku": "S1", "allocated_boxes": b} for e, b in rows
        ],
        "updated_at": "2026-09-01T03:00:00+00:00", "updated_by": f"{sup.lower()}@x.com",
    }
    if sent_at:
        doc["target_sun_sent_at"] = sent_at
    return doc


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name
        self.allocs = os.path.join(root, "allocations")
        self.logs = os.path.join(root, "logs")
        self.cache = os.path.join(root, "cache")
        for d in (self.allocs, self.logs, self.cache):
            os.makedirs(d)
        self.ua = os.path.join(root, "user_access.json")
        self.sl = os.path.join(root, "sl_links.json")
        with open(self.ua, "w", encoding="utf-8") as fh:
            json.dump(USERS, fh, ensure_ascii=False)
        with open(self.sl, "w", encoding="utf-8") as fh:
            json.dump({"links": []}, fh)

        self._env = {
            "ALLOCATIONS_DATA_DIR": self.allocs,
            "USAGE_LOGS_DIR": self.logs,
            "FABRIC_CACHE_DIR": self.cache,
            "USER_ACCESS_JSON_PATH": self.ua,
            "SL_LINKS_JSON_PATH": self.sl,
        }
        self._old = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)

        self.write_snap(_snap("SL100", [("E1", 5), ("E2", 3), ("E7", 0)],
                              sent_at="2026-09-02T04:00:00+00:00"))
        self.write_snap(_snap("SL200", [("E2", 4), ("E9", 2)], status="draft"))
        self.write_snap(_snap("SL300", [("E4", 0)]))          # กระจายแล้วได้ 0 ทั้งทีม
        self.write_roster(ROSTER)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def write_snap(self, doc):
        name = f"{doc['sup_id']}_{doc['target_year']}_{doc['target_month']:02d}.json"
        with open(os.path.join(self.allocs, name), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)

    def write_roster(self, rows):
        from backend.services import company_roster as cr
        fc.write_salesman_roster(cr._normalize(rows))

    def write_log(self, day, rows):
        path = os.path.join(self.logs, f"usage_{day}.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    def send_row(self, sup, *, emp_ids=None, with_period=True, ok=True, detail=None):
        row = {
            "ts": "2026-09-02T04:00:00Z", "level": "info" if ok else "error",
            "action": "send_targetsun", "sup_id": sup,
            "message": "ส่งเข้า Target Sun สำเร็จ" if ok else "ส่งเข้า Target Sun ไม่สำเร็จ",
            "detail": detail if detail is not None else f"งวด {Y}-{M:02d} · ส่ง 10 แถว",
        }
        if with_period:
            row["target_month"], row["target_year"] = M, Y
        ctx = {"ok": ok}
        if emp_ids is not None:
            ctx.update({"emp_ids": list(emp_ids), "emp_count": len(emp_ids),
                        "emp_ids_truncated": False})
        row["context"] = ctx
        return row

    def summary(self, **kw):
        return us.build_usage_summary(month=M, year=Y, force=True, **kw)


class TestTeamCounts(_Base):
    def test_denominator_is_allocating_login_codes(self):
        s = self.summary()
        self.assertEqual(s["teams"]["total"], 4)          # SL100/200/300/400
        self.assertEqual(s["scope"]["sl_codes_count"], 4)

    def test_zero_box_team_is_not_used(self):
        s = self.summary()
        self.assertEqual(s["teams"]["used"], 2)           # SL100, SL200
        self.assertEqual(s["teams"]["opened_no_boxes"], 1)  # SL300
        by = {t["sup_id"]: t for t in s["teams_detail"]}
        self.assertFalse(by["SL300"]["used"])
        self.assertTrue(by["SL300"]["has_snapshot"])
        self.assertFalse(by["SL400"]["has_snapshot"])

    def test_sent_uses_the_permanent_stamp_not_the_status(self):
        """SL100 กลับไปเป็น draft ได้ แต่ target_sun_sent_at อยู่ถาวร"""
        self.write_snap(_snap("SL100", [("E1", 5), ("E2", 3)], status="draft",
                              sent_at="2026-09-02T04:00:00+00:00"))
        s = self.summary()
        self.assertEqual(s["teams"]["sent"], 1)
        self.assertEqual({t["sup_id"] for t in s["teams_detail"] if t["ever_sent"]}, {"SL100"})


class TestEmployeeCounts(_Base):
    def test_denominator_excludes_van_and_non_allocating_teams(self):
        s = self.summary()
        # SL100=3 (V9 ถูกตัด) + SL200=2 + SL300=1 + SL400=1
        self.assertEqual(s["employees"]["total"], 7)
        self.assertEqual(s["employees"]["not_under_allocating_team"], 1)   # E8 ใต้ SL999

    def test_zero_box_employee_is_not_allocated(self):
        s = self.summary()
        by = {t["sup_id"]: t for t in s["teams_detail"]}
        self.assertEqual(by["SL100"]["allocated"], 2)      # E7 ได้ 0 ไม่นับ
        self.assertEqual(by["SL300"]["allocated"], 0)

    def test_same_emp_id_in_two_teams_counts_twice(self):
        """ถ้ายุบเป็นเซ็ตรหัสล้วน E2 จะหายไปหนึ่ง แล้วยอดรวมต่ำกว่าจริง"""
        s = self.summary()
        self.assertEqual(s["employees"]["allocated"], 4)   # SL100:{E1,E2} + SL200:{E2,E9}
        self.assertEqual(s["employees"]["duplicate_emp_ids_across_teams"], 1)

    def test_allocated_outside_the_roster_is_reported(self):
        """คนที่ถูกย้ายข้ามทีมมาเกลี่ยเป้า — ไม่ทำให้ % เกิน 100"""
        self.write_snap(_snap("SL300", [("E4", 1), ("E77", 9)]))
        s = self.summary()
        by = {t["sup_id"]: t for t in s["teams_detail"]}
        self.assertEqual(by["SL300"]["allocated"], 2)
        self.assertEqual(by["SL300"]["allocated_not_in_roster"], 1)
        for r in s["by_region"]:
            if r["allocated_pct"] is not None:
                self.assertLessEqual(r["allocated_pct"], 100.0)


class TestSentMethod(_Base):
    def test_team_approx_when_no_per_person_log(self):
        s = self.summary()
        by = {t["sup_id"]: t for t in s["teams_detail"]}
        self.assertEqual(by["SL100"]["method"], us.METHOD_TEAM_APPROX)
        self.assertEqual(by["SL100"]["sent"], 2)           # เหมาว่าคนที่ได้หีบถูกส่งหมด

    def test_exact_when_the_log_names_people(self):
        self.write_log("2026-09-02", [self.send_row("SL100", emp_ids=["E1"])])
        s = self.summary()
        by = {t["sup_id"]: t for t in s["teams_detail"]}
        self.assertEqual(by["SL100"]["method"], us.METHOD_EXACT)
        self.assertEqual(by["SL100"]["sent"], 1)           # E2 ไม่ได้อยู่ในไฟล์ที่ส่ง

    def test_mixed_when_only_some_teams_have_names(self):
        self.write_log("2026-09-02", [
            self.send_row("SL100", emp_ids=["E1"]),
            self.send_row("SL200"),                         # แถวรุ่นเก่า ไม่มีรายชื่อ
        ])
        s = self.summary()
        self.assertEqual(s["employees"]["method"], "mixed")
        by = {t["sup_id"]: t for t in s["teams_detail"]}
        self.assertEqual(by["SL200"]["method"], us.METHOD_TEAM_APPROX)
        self.assertEqual(by["SL200"]["sent"], 2)
        self.assertEqual(s["teams"]["sent"], 2)

    def test_old_log_without_period_field_is_matched_from_detail(self):
        self.write_log("2026-09-02", [
            self.send_row("SL200", emp_ids=["E9"], with_period=False)
        ])
        s = self.summary()
        by = {t["sup_id"]: t for t in s["teams_detail"]}
        self.assertEqual(by["SL200"]["method"], us.METHOD_EXACT)
        self.assertEqual(by["SL200"]["sent"], 1)

    def test_other_period_and_failed_sends_are_ignored(self):
        self.write_log("2026-09-02", [
            self.send_row("SL200", emp_ids=["E9"], with_period=False,
                          detail="งวด 2026-08 · ส่ง 3 แถว"),
            self.send_row("SL300", emp_ids=["E4"], ok=False),
        ])
        s = self.summary()
        by = {t["sup_id"]: t for t in s["teams_detail"]}
        self.assertFalse(by["SL200"]["ever_sent"])
        self.assertFalse(by["SL300"]["ever_sent"])

    def test_log_outside_the_window_is_ignored(self):
        self.write_log("2025-01-05", [self.send_row("SL200", emp_ids=["E9"])])
        s = self.summary()
        by = {t["sup_id"]: t for t in s["teams_detail"]}
        self.assertFalse(by["SL200"]["ever_sent"])


class TestRegionRollUp(_Base):
    def test_region_totals_equal_the_headline_numbers(self):
        """แถวรวมรายภาคต้องเท่าการ์ดตัวเลข — หลักฐานว่าไม่มีทีมไหนถูกนับซ้ำ"""
        s = self.summary()
        self.assertEqual(sum(r["teams"] for r in s["by_region"]), s["teams"]["total"])
        self.assertEqual(sum(r["used"] for r in s["by_region"]), s["teams"]["used"])
        self.assertEqual(sum(r["sent_teams"] for r in s["by_region"]), s["teams"]["sent"])
        self.assertEqual(sum(r["employees"] for r in s["by_region"]), s["employees"]["total"])
        self.assertEqual(sum(r["allocated"] for r in s["by_region"]), s["employees"]["allocated"])
        self.assertEqual(sum(r["sent"] for r in s["by_region"]), s["employees"]["sent"])

    def test_division_manager_without_region_keeps_its_own_bucket(self):
        s = self.summary()
        names = [r["region"] for r in s["by_region"]]
        self.assertIn(us.NO_REGION_LABEL, names)
        self.assertEqual(names[-1], us.NO_REGION_LABEL, "กองที่ไม่ระบุภาคควรอยู่ท้ายสุด")
        no_region = next(r for r in s["by_region"] if r["region"] == us.NO_REGION_LABEL)
        self.assertEqual(no_region["teams"], 1)            # SL400

    def test_excel_region_sheet_has_a_matching_total_row(self):
        s = self.summary()
        rows = us.region_rows(s)
        total = rows[-1]
        self.assertEqual(total["region"], "รวมทั้งหมด")
        self.assertEqual(total["teams"], s["teams"]["total"])
        self.assertEqual(sum(r["teams"] for r in rows[:-1]), total["teams"])


class TestRosterMissing(_Base):
    def test_report_still_opens_without_the_roster_cache(self):
        os.unlink(fc._roster_path())
        s = self.summary()
        self.assertFalse(s["roster"]["available"])
        self.assertIsNone(s["employees"]["total"])
        self.assertIsNone(s["employees"]["allocated_pct"])
        # ตัวเลขระดับทีมต้องยังครบ — นั่นคือเหตุผลที่หน้านี้ต้องเปิดได้ตอน Fabric ล่ม
        self.assertEqual(s["teams"]["total"], 4)
        self.assertEqual(s["teams"]["used"], 2)
        self.assertEqual(s["employees"]["allocated"], 4)

    def test_unknown_headcount_is_dash_everywhere_not_zero(self):
        """รายภาคขึ้น 0 แต่แถวรวมขึ้น — คนอ่านจะนึกว่าตัวเลขขัดกันเอง"""
        os.unlink(fc._roster_path())
        s = self.summary()
        self.assertTrue(all(r["employees"] is None for r in s["by_region"]))
        self.assertTrue(all(t["employees"] is None for t in s["teams_detail"]))
        self.assertTrue(all(r["employees"] == "—" for r in us.region_rows(s)))
        self.assertTrue(all(r["employees"] == "—" for r in us.team_rows(s)))


class TestCacheSignature(_Base):
    def test_second_call_uses_the_cache(self):
        first = us.build_usage_summary(month=M, year=Y)
        self.assertFalse(first["cached"])
        second = us.build_usage_summary(month=M, year=Y)
        self.assertTrue(second["cached"])
        self.assertEqual(first["employees"]["allocated"], second["employees"]["allocated"])

    def test_a_new_snapshot_invalidates_it(self):
        us.build_usage_summary(month=M, year=Y)
        self.write_snap(_snap("SL400", [("E5", 7)]))
        again = us.build_usage_summary(month=M, year=Y)
        self.assertFalse(again["cached"])
        self.assertEqual(again["teams"]["used"], 3)
        self.assertEqual(again["employees"]["allocated"], 5)

    def test_a_new_send_log_invalidates_it(self):
        us.build_usage_summary(month=M, year=Y)
        self.write_log("2026-09-03", [self.send_row("SL200", emp_ids=["E9"])])
        again = us.build_usage_summary(month=M, year=Y)
        self.assertFalse(again["cached"])
        self.assertEqual(again["teams"]["sent"], 2)


class TestExcelRows(_Base):
    def test_sheets_are_built_without_blowing_up(self):
        s = self.summary()
        kv = us.summary_kv_rows(s)
        self.assertTrue(any(r["topic"] == "ทีมที่กระจายเป้าได้" for r in kv))
        self.assertTrue(all({"topic", "value", "note"} <= set(r) for r in kv))
        teams = us.team_rows(s)
        self.assertEqual(len(teams), 4, "ทีมที่ยังไม่เคยใช้ต้องอยู่ในรายงานด้วย")
        by = {r["sup_id"]: r for r in teams}
        self.assertEqual(by["SL400"]["allocated"], 0)
        self.assertEqual(by["SL300"]["login_kind"], "Manager")
        self.assertEqual(by["SL400"]["acc_region"], us.NO_REGION_LABEL)


if __name__ == "__main__":
    unittest.main()
