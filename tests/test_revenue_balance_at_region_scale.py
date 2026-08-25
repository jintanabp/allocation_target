"""
ตัวเกลี่ยเป้าเงินต้องทำงานได้ที่ขนาด "รวมทั้งภาค" ไม่ใช่แค่ขนาดทีมเดียว

เจอของจริง: กระจายรวมภาคแล้วผลออกมาห่างจากเป้าเหลืองหลักแสนถึงหลักล้านทุกคน
ทั้งที่ทีมเดียวห่างแค่หลักร้อย · ต้นเหตุคือ _greedy_revenue_balancer ย้ายหีบ
รอบละ 1 ใบ ระหว่างคนที่เกินเป้ามากที่สุดกับคนที่ขาดมากที่สุด ทีมเดียว ~15-25 คน
ปิดช่องว่างได้ในไม่กี่พันรอบจึงไม่มีใครเห็นปัญหา แต่รวมภาคมีเป็นร้อยคนและช่องว่าง
ตั้งต้นหลักสิบล้านบาท ต้องย้ายหลายหมื่นใบ พอชนเพดาน max_iters (50,000) ก็เลิก
กลางคัน ค้างห่างเป้าพร้อมกันทั้งภาค

เกณฑ์ที่ผู้ใช้ยอมรับ: ดิฟรายคนระดับหลักพัน (= revenue_tolerance_baht) ไม่ใช่หลักแสน
"""

import random
import unittest

import pandas as pd

from backend.OR_engine import _greedy_revenue_balancer

TOLERANCE = 1000.0
PRICES = (300.0, 450.0, 590.0, 720.0, 1050.0)


def _scenario(n_emp: int, n_sku: int, seed: int = 7):
    """
    เป้าหีบต่อ SKU ก้อนหนึ่ง เกลี่ยเท่า ๆ กันเป็นจุดตั้งต้น แล้วให้เป้าเงินรายคน
    ต่างกันมาก (0.4x - 1.9x) เหมือนของจริง โดยผลรวมเป้าเงิน = มูลค่าหีบรวมพอดี
    (scale = 1) เพื่อแยกปัญหาของตัวเกลี่ยออกจากปัญหาราคาไม่ตรงกันข้ามทีม
    """
    rnd = random.Random(seed)
    skus = [f"S{i:04d}" for i in range(n_sku)]
    prices = {s: float(rnd.choice(PRICES)) for s in skus}
    emps = [f"E{i:03d}" for i in range(n_emp)]
    boxes = {s: rnd.randint(n_emp, n_emp * 12) for s in skus}

    rows = []
    for s in skus:
        base, extra = divmod(boxes[s], n_emp)
        for i, e in enumerate(emps):
            rows.append({"emp_id": e, "sku": s,
                         "allocated_boxes": base + (1 if i < extra else 0)})

    df_sku = pd.DataFrame([
        {"sku": s, "price_per_box": prices[s], "supervisor_target_boxes": boxes[s]}
        for s in skus
    ])
    total = sum(prices[s] * boxes[s] for s in skus)
    weights = [rnd.uniform(0.4, 1.9) for _ in emps]
    wsum = sum(weights)
    df_emp = pd.DataFrame([
        {"emp_id": e, "yellow_target": total * w / wsum} for e, w in zip(emps, weights)
    ])
    return pd.DataFrame(rows), df_emp, df_sku, prices


def _run(n_emp: int, n_sku: int, seed: int = 7):
    df_out, df_emp, df_sku, prices = _scenario(n_emp, n_sku, seed)
    res = _greedy_revenue_balancer(
        df_out, df_emp, df_sku, tolerance_baht=TOLERANCE
    )
    rev = (
        res.assign(v=res["allocated_boxes"] * res["sku"].map(prices))
        .groupby("emp_id")["v"].sum()
    )
    gap = (rev - df_emp.set_index("emp_id")["yellow_target"]).abs()
    return res, df_sku, gap


class TestRevenueBalanceAtScale(unittest.TestCase):
    """
    เคสใหญ่รันครั้งเดียวแล้วตรวจหลายอย่าง — ชุดเทสทั้งชุดต้องไม่ช้าลงเพราะเรื่องนี้
    (150 คน x 600 SKU กินราว 12 วินาที ถ้ารันซ้ำทุกเทสจะบวกเข้าไปเป็นนาที)
    """

    @classmethod
    def setUpClass(cls):
        cls.big = _run(150, 600)          # ขนาดที่พังของเดิม
        cls.alt = _run(90, 300, seed=42)   # อีก seed กันบังเอิญ (เล็กกว่าเพื่อไม่ให้ชุดเทสช้า)

    def assert_boxes_match_target(self, res: pd.DataFrame, df_sku: pd.DataFrame):
        """ประตู I1 — ยอดหีบต่อ SKU ต้องตรงเป้าเป๊ะ ห้ามพังเพื่อให้เงินเข้าเป้า"""
        got = res.groupby("sku")["allocated_boxes"].sum()
        want = df_sku.set_index("sku")["supervisor_target_boxes"]
        pd.testing.assert_series_equal(
            got.reindex(want.index).fillna(0).astype(int),
            want.astype(int),
            check_names=False,
        )

    def test_single_team_scale(self):
        res, df_sku, gap = _run(15, 200)
        self.assertLessEqual(gap.max(), TOLERANCE)
        self.assert_boxes_match_target(res, df_sku)

    def test_region_scale_reaches_tolerance(self):
        """ขนาดที่พังของเดิม — ดิฟมัธยฐานเคยอยู่ที่ 175,956 บาท สูงสุด 192,649"""
        _res, _df_sku, gap = self.big
        self.assertLessEqual(gap.max(), TOLERANCE)

    def test_region_scale_keeps_boxes_on_target(self):
        res, df_sku, _gap = self.big
        self.assert_boxes_match_target(res, df_sku)

    def test_region_scale_survives_another_seed(self):
        res, df_sku, gap = self.alt
        self.assertLessEqual(gap.max(), TOLERANCE)
        self.assert_boxes_match_target(res, df_sku)

    def test_never_moves_below_zero(self):
        """ย้ายทีละหลายใบต้องไม่ทำให้เซลล์ไหนติดลบ"""
        res, _df_sku, _gap = self.big
        self.assertGreaterEqual(int(res["allocated_boxes"].min()), 0)


if __name__ == "__main__":
    unittest.main()
