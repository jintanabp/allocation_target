"""
ไฟล์ที่ส่งเข้า Target Sun ครบหรือไม่ครบตรงไหนบ้าง — พิสูจน์ด้วยการสร้างไฟล์จริง

ตอบสองคำถามที่ผู้ใช้ถามเมื่อ 10 ก.ย. 2026:

  1. "คู่พนักงาน×สินค้าที่ Target Sun ไม่เคยมี — เราส่งไปไม่ได้ใช่ไหม"
     ตอบ: **ส่งได้** ระบบสร้างแถวใหม่ให้ โดยอนุมานเขต/ดิวิชัน/พื้นที่จากแถวอื่น
     ของพนักงานคนเดียวกัน · ที่ส่งไม่ได้จริง ๆ คือคนที่ **ไม่มีแถวไหนเลยใน Target Sun**
     (หรือแถวของเขาขัดกันเอง) เพราะไม่มีอะไรให้อนุมาน

  2. "คนที่ถูกกันไม่ให้ตั้งเป้า ถ้า Target Sun มีเป้าของเขาอยู่ จะเป็นยังไง"
     ตอบ: **เป็นไปได้จริง** แคช grain มาจากรายชื่อพนักงานทั้งทีมก่อนกรอง
     แถวของเขาจึงอยู่ในแคช · ระบบจะส่งหีบ 0 ไปทับให้เอง

และล็อกผลข้างเคียงที่ผู้ใช้ห่วงไว้เป็นตัวเลข: **SKU ที่ถูกตัดทั้งตัว จะไม่มีแถวไหนของมัน
ถูกส่งเลย รวมทั้งแถวหีบ 0 ที่ตั้งใจไปล้างเป้าเดิม** → เลขงวดก่อนของ SKU นั้นค้างอยู่ปลายทาง

รันออฟไลน์ล้วนใน temp dir ไม่แตะ Fabric ไม่แตะ Target Sun (ท่าเดียวกับ test_emp_assignment.py)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from backend.services import lakehouse as lh  # noqa: E402

SUP = "SL509"
YEAR = 2026
MONTH = 10


def grain_row(emp, sku, qty=10, st="S", div="D1", area="20", prov="P2", wh=""):
    return {
        "emp_id": emp,
        "sku": sku,
        "qty": qty,
        "salestype": st,
        "divisioncode": div,
        "areacode": area,
        "provincecode": prov,
        "warehouse_code": wh,
    }


class _SendHarness(unittest.TestCase):
    """เขียนแคชขั้นที่ 1 ลง temp dir แล้วเรียกตัวสร้างไฟล์ส่งจริง"""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp(prefix="send_complete_")
        os.chdir(self._tmpdir)
        os.makedirs("data", exist_ok=True)
        self._prev_nt = os.environ.get("NO_TARGET_EMPLOYEES_JSON_PATH")
        self._nt_path = os.path.join(self._tmpdir, "no_target_employees.json")
        os.environ["NO_TARGET_EMPLOYEES_JSON_PATH"] = self._nt_path

        # แถวที่ยังไม่มี dim จะไหลไปชั้น "เติมจาก Fabric" ซึ่งเป็นการเรียกระบบจริง
        # เทสต์ต้องไม่แตะระบบจริงเด็ดขาด — ตัดชั้นนั้นออก แล้วปล่อยให้แถวไม่มี dim
        # ถูกตัดตามกฎเดิม ซึ่งเป็นพฤติกรรมเดียวกับตอน Fabric เติมให้ไม่ได้
        self._real_enrich = lh._enrich_emp_dimensions
        lh._enrich_emp_dimensions = lambda df, rows_raw, skip_emp_sku_dim_merge=False: df

    def tearDown(self):
        lh._enrich_emp_dimensions = self._real_enrich
        os.chdir(self._cwd)
        if self._prev_nt is None:
            os.environ.pop("NO_TARGET_EMPLOYEES_JSON_PATH", None)
        else:
            os.environ["NO_TARGET_EMPLOYEES_JSON_PATH"] = self._prev_nt
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def write_grain(self, rows):
        pd.DataFrame(rows).to_csv(
            f"data/tga_lines_{SUP}_{YEAR}_{MONTH:02d}.csv", index=False
        )

    def write_targets(self, by_sku):
        pd.DataFrame(
            [
                {"sku": s, "supervisor_target_boxes": b, "price_per_box": 1.0}
                for s, b in by_sku.items()
            ]
        ).to_csv(f"data/target_boxes_{SUP}_{YEAR}_{MONTH:02d}.csv", index=False)

    def block(self, emp_ids):
        with open(self._nt_path, "w", encoding="utf-8") as f:
            json.dump(
                {"employees": [{"super_code": SUP, "emp_id": e} for e in emp_ids]}, f
            )

    def send(self, allocs):
        from backend.schemas import LakehouseUploadRequest

        req = LakehouseUploadRequest(
            sup_id=SUP,
            target_month=MONTH,
            target_year=YEAR,
            upload_user_code="T",
            allocations=[
                {"emp_id": e, "sku": s, "allocated_boxes": b} for e, s, b in allocs
            ],
            allow_new_targetsun_rows=True,
        )
        return lh._build_tga_upload_dataframe(req, drop_incomplete_rows=True)

    @staticmethod
    def rows_of(df):
        """{(รหัสพนักงาน, รหัสสินค้า): หีบ} จากไฟล์ที่จะส่งจริง"""
        if df is None or df.empty:
            return {}
        out = {}
        for _, r in df.iterrows():
            key = (str(r["SALESMANCODE"]).strip(), str(r["PRODUCTCODE"]).strip())
            out[key] = out.get(key, 0) + int(r["QUANTITYCASE"])
        return out


class PairsNotYetInTargetSun(_SendHarness):
    """คำถามที่ 1 — คู่ที่ Target Sun ยังไม่มี ส่งได้หรือไม่ได้"""

    def test_new_pair_is_sent_by_inferring_the_territory(self):
        """
        C001 เคยมีเป้าสินค้า A อยู่แล้ว แต่ไม่เคยมีสินค้า B
        → แถวสินค้า B **ถูกส่ง** โดยยกเขต/ดิวิชัน/พื้นที่มาจากแถวสินค้า A ของเขาเอง
        """
        self.write_grain([grain_row("C001", "A")])
        self.write_targets({"A": 10, "B": 5})
        out, dropped, _preview, shortfall = self.send(
            [("C001", "A", 10), ("C001", "B", 5)]
        )
        rows = self.rows_of(out)
        self.assertEqual(rows.get(("C001", "B")), 5, f"แถวใหม่ต้องถูกส่ง — ได้ {rows}")
        self.assertEqual(dropped, 0)
        self.assertEqual(shortfall, [])
        sent = out[out["PRODUCTCODE"].astype(str).str.strip() == "B"].iloc[0]
        self.assertEqual(str(sent["AREACODE"]).strip(), "20", "ต้องยกเขตของ C001 มา")

    def test_person_with_no_rows_at_all_cannot_be_sent(self):
        """
        C777 ไม่มีแถวไหนเลยใน Target Sun → ไม่มีอะไรให้อนุมานเขต จึงส่งไม่ได้
        นี่คือกรณีเดียวที่ "ส่งไม่ได้จริง ๆ"

        และเมื่อ SKU ที่ถูกตัดคือทุก SKU ในไฟล์ ระบบ **ไม่ส่งไฟล์เปล่า** แต่ตีกลับ 400
        พร้อมบอกว่า SKU ไหนถูกตัด — ดีกว่าปล่อยให้ส่งสำเร็จโดยไม่มีอะไรถึงปลายทาง
        """
        from fastapi import HTTPException

        self.write_grain([grain_row("C001", "A")])
        self.write_targets({"A": 15})
        with self.assertRaises(HTTPException) as cm:
            self.send([("C001", "A", 10), ("C777", "A", 5)])
        self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual(cm.exception.detail.get("excluded_skus"), ["A"])

    def test_whole_sku_is_dropped_including_its_zero_rows(self):
        """
        ผลข้างเคียงที่ผู้ใช้ห่วง — SKU ที่ถูกตัด **แถวหีบ 0 ของมันก็ไม่ถูกส่งด้วย**

        C002 เคยมีเป้าสินค้า A 10 หีบใน Target Sun · งวดนี้เขาได้ 0
        ปกติระบบจะส่ง 0 ไปล้างเป้าเดิมให้ แต่พอ SKU A ถูกตัดทั้งตัวเพราะ C777
        แถว 0 นั้นก็หายไปด้วย → **เป้าเดิม 10 หีบของ C002 ค้างอยู่ปลายทาง**
        ทั้งที่ผู้ใช้เห็นบนจอว่าเขาได้ 0 — ยอดรวมปลายทางจึงเกินไฟล์ที่ส่ง 10 หีบ
        (สินค้า B ใส่ไว้ให้ไฟล์ไม่ว่าง ไม่งั้นจะโดนตีกลับ 400 ตามเทสต์ก่อนหน้า)
        """
        self.write_grain(
            [
                grain_row("C001", "A"),
                grain_row("C002", "A", qty=10),
                grain_row("C001", "B"),
            ]
        )
        self.write_targets({"A": 15, "B": 7})
        out, _dropped, _preview, shortfall = self.send(
            [("C001", "A", 15), ("C002", "A", 0), ("C777", "A", 5), ("C001", "B", 7)]
        )
        rows = self.rows_of(out)
        self.assertNotIn(("C002", "A"), rows, "แถวล้างค่าหายไปพร้อม SKU ที่ถูกตัด")
        self.assertEqual(rows, {("C001", "B"): 7}, f"เหลือแต่สินค้า B — ได้ {rows}")
        self.assertTrue(shortfall, "ต้องมีรายงานบอกผู้ใช้ว่า SKU ไหนไม่ถูกส่ง")
        self.assertEqual(str(shortfall[0]["sku"]).strip(), "A")

    def test_report_says_what_target_sun_is_holding_right_now(self):
        """
        ตัวเลขที่ผู้ใช้ต้องใช้ไปแก้มือ — **ปลายทางถืออยู่เท่าไรตอนนี้**

        รู้แค่ "ขาดกี่หีบ" ไม่พอ เพราะแถวเก่าของ SKU ที่ถูกตัดยังอยู่ครบ
        คนที่ไปเติมแต่ส่วนที่ขาดจะได้ยอดรวมเกินเป้าเสมอ
        กรณีนี้: ปลายทางถือ 10 + 4 = 14 หีบ ขณะที่เป้าทีมคือ 15
        """
        self.write_grain(
            [
                grain_row("C001", "A", qty=10),
                grain_row("C002", "A", qty=4),
                grain_row("C001", "B"),
            ]
        )
        self.write_targets({"A": 15, "B": 7})
        _out, _dropped, _preview, shortfall = self.send(
            [("C001", "A", 15), ("C777", "A", 5), ("C001", "B", 7)]
        )
        item = next(s for s in shortfall if str(s["sku"]).strip() == "A")
        self.assertEqual(item["current_targetsun_boxes"], 14, f"ได้ {item}")
        self.assertEqual(item["expected_boxes"], 15)

    def test_current_holding_is_none_when_the_cache_cannot_say(self):
        """ไม่มีแคชให้อ่าน = ไม่รู้ ต้องเป็น None ไม่ใช่ 0 (0 แปลว่า 'ปลายทางว่าง' ซึ่งคนละเรื่อง)"""
        self.write_grain([grain_row("C001", "B")])
        self.write_targets({"B": 7})
        now = lh._targetsun_boxes_now_by_sku(SUP, MONTH, YEAR)
        self.assertIsNone(now.get("A"))
        self.assertEqual(now.get("B"), 10)

    def test_a_clean_sku_still_goes_through_when_another_one_is_dropped(self):
        """ตัดเป็นราย SKU ไม่ใช่ทั้งไฟล์ — SKU ที่ไม่มีปัญหาต้องยังส่งได้ตามปกติ"""
        self.write_grain([grain_row("C001", "A"), grain_row("C001", "B")])
        self.write_targets({"A": 15, "B": 7})
        out, _dropped, _preview, _shortfall = self.send(
            [("C001", "A", 10), ("C777", "A", 5), ("C001", "B", 7)]
        )
        rows = self.rows_of(out)
        self.assertEqual(rows, {("C001", "B"): 7}, f"ได้ {rows}")


class NoTargetEmployeeStaleRows(_SendHarness):
    """คำถามที่ 2 — คนที่ถูกกันไม่ให้ตั้งเป้า แต่ Target Sun มีเป้าของเขาอยู่"""

    def test_stale_target_is_cleared_with_a_zero_row(self):
        """
        C444 อยู่ในรายชื่อ「ไม่ต้องตั้งเป้า」จึงไม่มีแถวในผลกระจายเลย
        แต่ Target Sun ยังถือเป้าเดิมของเขา 10 หีบอยู่ (แคช grain มีแถวของเขา)
        → ไฟล์ที่ส่งต้องมีแถวหีบ 0 ของ C444 ไปทับ
        """
        self.block(["C444"])
        self.write_grain([grain_row("C001", "A"), grain_row("C444", "A", qty=10)])
        self.write_targets({"A": 15})
        out, _dropped, _preview, _shortfall = self.send([("C001", "A", 15)])
        rows = self.rows_of(out)
        self.assertEqual(rows.get(("C444", "A")), 0, f"ต้องส่ง 0 ไปทับ — ได้ {rows}")
        self.assertEqual(rows.get(("C001", "A")), 15)

    def test_nothing_added_when_targetsun_has_nothing_for_them(self):
        """ไม่เคยมีเป้าอยู่ปลายทาง = ไม่มีอะไรต้องล้าง ห้ามไปสร้างแถวใหม่ให้เขา"""
        self.block(["C444"])
        self.write_grain([grain_row("C001", "A")])
        self.write_targets({"A": 15})
        out, _dropped, _preview, _shortfall = self.send([("C001", "A", 15)])
        emps = {k[0] for k in self.rows_of(out)}
        self.assertNotIn("C444", emps)

    def test_clearing_does_not_disturb_the_sku_total_that_is_sent(self):
        """แถวล้างค่าเป็นหีบ 0 จึงต้องไม่ทำให้ยอดรวมต่อ SKU ที่ส่งเปลี่ยน"""
        self.block(["C444"])
        self.write_grain([grain_row("C001", "A"), grain_row("C444", "A", qty=10)])
        self.write_targets({"A": 15})
        out, _dropped, _preview, _shortfall = self.send([("C001", "A", 15)])
        self.assertEqual(int(out["QUANTITYCASE"].sum()), 15)


class FrontendShowsTheHolding(unittest.TestCase):
    """ตัวเลขที่ backend ส่งมาต้องไปโผล่บนจอจริง ไม่ใช่มีแต่ใน payload"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as f:
            cls.src = f.read()
        i = cls.src.index("function _showShortfallModal(")
        cls.body = cls.src[i : i + 4000]

    def test_modal_renders_the_current_holding(self):
        self.assertIn("current_targetsun_boxes", self.body)
        self.assertIn("ตอนนี้ Target Sun ถืออยู่", self.body)
        self.assertIn("staleLine", self.body)

    def test_only_for_skus_that_were_dropped_whole(self):
        """SKU ที่ส่งไปครบแล้วไม่ต้องมีบรรทัดนี้ — ปลายทางถูกทับด้วยเลขใหม่ไปแล้ว"""
        i = self.body.index("const staleLine")
        self.assertIn("s.excluded_whole_sku", self.body[i : i + 200])

    def test_missing_value_is_not_printed_as_zero(self):
        """ไม่รู้ กับ ปลายทางว่าง คนละเรื่อง — ห้ามโชว์ 0 เมื่ออ่านค่าไม่ได้"""
        i = self.body.index("const staleLine")
        seg = self.body[i : i + 300]
        self.assertIn("!= null", seg)
        self.assertIn("Number.isFinite(nowTs)", seg)

    def test_it_tells_the_user_the_number_to_fix_it_to(self):
        """บอกแค่ 'ถืออยู่เท่าไร' ยังไม่พอ ต้องบอกด้วยว่าต้องแก้ให้เป็นเท่าไร"""
        i = self.body.index("const staleLine")
        self.assertIn("ต้องแก้ให้เป็น", self.body[i : i + 700])


if __name__ == "__main__":
    unittest.main()
