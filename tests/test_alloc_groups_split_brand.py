"""
แยกแบรนด์เป็นกลุ่มสินค้า (ผู้ใช้ขอ 8 ต.ค. 2026) — เช่น มาม่า แยกตาม Section · แบรนด์อื่นเป็นแบรนด์เหมือนเดิม

- ไม่เลือกแยก = ไม่แตะอะไรเลย (พฤติกรรมเดิม)
- คีย์ "แบรนด์ · รหัสกลุ่ม" ตรงกับหน้าเว็บ · รหัสจาก CSV "702.0" = "702"
- วิธีกระจายรายหน่วย (brand_strategy_map) และการหมุนหีบ SKU เล็กใช้หน่วยกลุ่ม
- ค่าที่เลือกเก็บรายคนใน data/ · โหมดดูแทนเปลี่ยนไม่ได้ · ชื่อกลุ่มดึง Fabric ไม่ได้ก็ไม่ล้ม
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import pandas as pd
from fastapi import HTTPException

from backend.core import alloc_groups
from backend.OR_engine import _sku_brand_map, allocate_boxes
from backend.schemas import OptimizeRequest
from backend.services import alloc_group_prefs
from backend.services.optimize import _resolved_strategies_by_sku, _sku_brand_key

EMPS = [f"E{i}" for i in range(1, 7)]


def _skus():
    return pd.DataFrame([
        {"sku": "M1", "brand_name_thai": "มาม่า", "section": 702.0, "supervisor_target_boxes": 3, "price_per_box": 100.0},
        {"sku": "M2", "brand_name_thai": "มาม่า", "section": "702", "supervisor_target_boxes": 3, "price_per_box": 100.0},
        {"sku": "M3", "brand_name_thai": "มาม่า", "section": "704", "supervisor_target_boxes": 3, "price_per_box": 100.0},
        {"sku": "M4", "brand_name_thai": "มาม่า", "section": "", "supervisor_target_boxes": 3, "price_per_box": 100.0},
        {"sku": "B1", "brand_name_thai": "ไบโอนิค", "section": "847", "supervisor_target_boxes": 3, "price_per_box": 100.0},
    ])


class TestGroupKeys(unittest.TestCase):
    def test_no_split_leaves_df_untouched(self):
        df = _skus()
        self.assertIs(alloc_groups.add_group_column(df, []), df)
        self.assertNotIn("alloc_group", df.columns)

    def test_selected_brand_split_other_brands_unchanged(self):
        df = alloc_groups.add_group_column(_skus(), ["มาม่า"])
        self.assertEqual(
            list(df["alloc_group"]),
            ["มาม่า · 702", "มาม่า · 702", "มาม่า · 704", "มาม่า · -", "ไบโอนิค"],
        )

    def test_service_and_engine_use_group(self):
        df = alloc_groups.add_group_column(_skus(), ["มาม่า"])
        self.assertEqual(_sku_brand_key(df.iloc[2]), "มาม่า · 704")
        self.assertEqual(_sku_brand_key(_skus().iloc[2]), "มาม่า")  # ไม่แยก = แบรนด์เหมือนเดิม
        self.assertEqual(_sku_brand_map(df)["M3"], "มาม่า · 704")
        self.assertEqual(_sku_brand_map(_skus())["M3"], "มาม่า")

    def test_strategy_per_group(self):
        df = alloc_groups.add_group_column(_skus(), ["มาม่า"])
        got = _resolved_strategies_by_sku(df, {"มาม่า · 704": "EVEN", "มาม่า · 702": "L3M"}, "L3M")
        self.assertEqual(got, {"EVEN", "L3M"})

    def test_request_normalizes_split_brands(self):
        base = dict(sup_id="SL1", target_month=11, target_year=2026, yellowTargets=[])
        req = OptimizeRequest(**base, split_brands=[" มาม่า ", "มาม่า", ""])
        self.assertEqual(req.split_brands, ["มาม่า"])
        self.assertEqual(OptimizeRequest(**base).split_brands, [])


class TestRotationWithinGroup(unittest.TestCase):
    """SKU เล็ก 2 ตัวคนละกลุ่ม — แยกแล้วต้องหมุนภายในกลุ่ม ยอดต่อ SKU ยังเท่าเป้า"""

    def _run(self, split):
        skus = pd.DataFrame([
            {"sku": s, "brand_name_thai": "มาม่า", "section": sec, "supervisor_target_boxes": 3, "price_per_box": 100.0}
            for s, sec in (("A1", "702"), ("A2", "702"), ("C1", "704"), ("C2", "704"))
        ])
        skus = alloc_groups.add_group_column(skus, split)
        emp = pd.DataFrame([{"emp_id": e, "yellow_target": 1200.0} for e in EMPS])
        hist = pd.DataFrame([{"emp_id": e, "sku": s, "hist_boxes": 10 - i * 0.5}
                             for i, e in enumerate(EMPS) for s in skus["sku"]])
        return allocate_boxes(emp, skus, hist, strategy="L3M"), skus

    def test_totals_kept_and_groups_reported(self):
        for split in ([], ["มาม่า"]):
            with self.subTest(split=split):
                df, skus = self._run(split)
                per_sku = df.groupby("sku")["allocated_boxes"].sum().to_dict()
                self.assertEqual(per_sku, {"A1": 3, "A2": 3, "C1": 3, "C2": 3})
        # ไม่แยก = หมุนทั้งแบรนด์เป็นหน่วยเดียว · แยก = หมุนแยกสองกลุ่ม
        self.assertEqual(self._run([])[0].attrs["brand_rotation"]["brands"], 1)
        self.assertEqual(self._run(["มาม่า"])[0].attrs["brand_rotation"]["brands"], 2)


class TestPrefsStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {
            "ALLOC_GROUP_PREFS_JSON_PATH": os.path.join(self._tmp.name, "prefs.json"),
            "FABRIC_CACHE_DIR": os.path.join(self._tmp.name, "cache"),
        })
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_per_user_roundtrip(self):
        self.assertEqual(alloc_group_prefs.get_split_brands("a@x.com"), [])
        alloc_group_prefs.set_split_brands("A@X.com", ["มาม่า", " มาม่า", ""])
        self.assertEqual(alloc_group_prefs.get_split_brands("a@x.com"), ["มาม่า"])
        self.assertEqual(alloc_group_prefs.get_split_brands("b@x.com"), [])
        alloc_group_prefs.set_split_brands("a@x.com", [])
        with open(alloc_group_prefs.prefs_path(), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["users"], {})

    def test_corrupt_file_means_no_split(self):
        with open(alloc_group_prefs.prefs_path(), "w", encoding="utf-8") as f:
            f.write("{oops")
        self.assertEqual(alloc_group_prefs.get_split_brands("a@x.com"), [])

    def test_refresh_writes_cache_normalized_and_fails_open(self):
        fake = mock.Mock()
        fake.return_value.get_section_names.return_value = {"702.0": "มาม่าเส้นเหลือง", "0": "ไม่ใช่กลุ่ม"}
        with mock.patch("backend.fabric_dax_connector.FabricDAXConnector", fake):
            self.assertEqual(alloc_group_prefs.refresh_section_names(), {"702": "มาม่าเส้นเหลือง"})
        boom = mock.Mock(side_effect=RuntimeError("fabric down"))
        with mock.patch("backend.fabric_dax_connector.FabricDAXConnector", boom):
            self.assertEqual(alloc_group_prefs.refresh_section_names(), {})
        # ล้มแล้วแคชเดิมยังอยู่
        with mock.patch.object(alloc_group_prefs, "_refresh_in_background") as bg:
            self.assertEqual(alloc_group_prefs.section_names(), {"702": "มาม่าเส้นเหลือง"})
        bg.assert_not_called()  # แคชยังใหม่

    def test_section_names_never_waits_on_fabric(self):
        """แคชหาย/เก่า = คืนของที่มีทันที แล้วดึงใหม่เบื้องหลัง — GET ต้องไม่ค้างรอ Fabric"""
        with mock.patch.object(alloc_group_prefs, "_refresh_in_background") as bg, \
             mock.patch("backend.fabric_dax_connector.FabricDAXConnector") as fx:
            self.assertEqual(alloc_group_prefs.section_names(), {})
        bg.assert_called_once()
        fx.assert_not_called()

    def test_background_refresh_backs_off_after_attempt(self):
        started = []
        with mock.patch.object(alloc_group_prefs.threading, "Thread",
                               side_effect=lambda **kw: started.append(kw) or mock.Mock()), \
             mock.patch.dict(alloc_group_prefs._section_refresh_state, {"running": False, "last_attempt": 0.0}):
            alloc_group_prefs._refresh_in_background()
            alloc_group_prefs._section_refresh_state["running"] = False
            alloc_group_prefs._refresh_in_background()  # เพิ่งลองไป — ยังไม่ลองซ้ำ
        self.assertEqual(len(started), 1)

    def test_rejects_absurd_brand_names(self):
        with self.assertRaises(ValueError):
            alloc_group_prefs.set_split_brands("a@x.com", ["x" * 500])


class TestBlankSectionFromCsv(unittest.TestCase):
    """ช่องว่างในไฟล์เป้าถูกอ่านกลับเป็น 0 — ต้องได้คีย์เดียวกับหน้าเว็บ ("มาม่า · -") ไม่ใช่ "มาม่า · 0" """

    def test_blank_section_roundtrip_through_target_csv(self):
        from backend.core.targets import _read_sku_csv

        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.csv")
            _skus().to_csv(p, index=False)
            df = alloc_groups.add_group_column(_read_sku_csv(p), ["มาม่า"])
        self.assertEqual(
            list(df["alloc_group"]),
            ["มาม่า · 702", "มาม่า · 702", "มาม่า · 704", "มาม่า · -", "ไบโอนิค"],
        )
        self.assertEqual(alloc_groups.norm_section(0.0), "")
        self.assertEqual(alloc_groups.norm_section("0702"), "0702")


class TestEndpoints(unittest.TestCase):
    def test_view_as_cannot_change_someone_elses_setting(self):
        from backend.routers import data as data_router

        with mock.patch.object(alloc_group_prefs, "set_split_brands") as st:
            with self.assertRaises(HTTPException) as cm:
                data_router.put_alloc_groups(
                    data_router.SplitBrandsBody(split_brands=["มาม่า"]),
                    user={"email": "sup@x.com", "view_as_email": "sup@x.com"},
                )
        self.assertEqual(cm.exception.status_code, 403)
        st.assert_not_called()

    def test_put_and_get_for_self(self):
        from backend.routers import data as data_router

        with mock.patch.object(alloc_group_prefs, "set_split_brands", return_value=["มาม่า"]) as st, \
             mock.patch.object(alloc_group_prefs, "get_split_brands", return_value=["มาม่า"]), \
             mock.patch.object(alloc_group_prefs, "section_names", return_value={"702": "x"}):
            out = data_router.put_alloc_groups(data_router.SplitBrandsBody(split_brands=["มาม่า"]),
                                               user={"email": "Sup@X.com"})
            got = data_router.get_alloc_groups(user={})
        self.assertEqual(out, {"split_brands": ["มาม่า"]})
        self.assertEqual(st.call_args.args[0], "sup@x.com")
        self.assertEqual(got["section_names"], {"702": "x"})


if __name__ == "__main__":
    unittest.main()
