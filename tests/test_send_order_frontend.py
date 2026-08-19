"""
ลำดับการส่ง Target Sun ในหน้าเว็บ: ต้อง "เตรียมครบทุกทีม → ถาม → ค่อยส่ง"

ทำไมต้องมี test นี้:
  เดิม doLakehouseUpload() วน prepare→import ทีละทีม พอทีมท้าย ๆ เจอ "เป้าจะขาด"
  แล้วเด้งถาม ทีมก่อนหน้าก็ส่งเข้า Target Sun ไปแล้ว กด "กลับไปแก้ไข" ก็ย้อนไม่ได้
  (เจอตอนผู้ใช้ทดสอบจริง)

ไม่มี test runner ฝั่ง JS ในโปรเจกต์นี้ จึงตรวจที่ source แบบเดียวกับ TestGateWiring
ใน test_send_target_gate.py — หยาบแต่จับ regression ที่สำคัญที่สุดได้
"""

from __future__ import annotations

import os
import re
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
APP_JS = os.path.join(REPO, "frontend", "app.js")


def _function_source(name: str) -> str:
    """ตัดตัวฟังก์ชันจาก app.js ด้วยการนับวงเล็บปีกกา (ไม่มี JS parser ให้ใช้)"""
    src = open(APP_JS, encoding="utf-8").read()
    m = re.search(rf"^async function {re.escape(name)}\(", src, re.MULTILINE)
    if not m:
        raise AssertionError(f"ไม่พบฟังก์ชัน {name} ใน app.js")
    start = src.index("{", m.end() - 1)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"หาปลายฟังก์ชัน {name} ไม่เจอ")


def _function_source_any(name: str) -> str:
    """เหมือน _function_source แต่รับทั้ง function ธรรมดาและ async"""
    src = open(APP_JS, encoding="utf-8").read()
    m = re.search(rf"^(?:async\s+)?function {re.escape(name)}\(", src, re.MULTILINE)
    if not m:
        raise AssertionError(f"ไม่พบฟังก์ชัน {name} ใน app.js")
    start = src.index("{", m.end() - 1)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"หาปลายฟังก์ชัน {name} ไม่เจอ")


def _loop_body_spans(src: str) -> list[tuple[int, int]]:
    """ช่วง [start, end) ของ body ของทุก for/while ในโค้ด (จับคู่วงเล็บปีกกาเอา)"""
    spans = []
    for m in re.finditer(r"\b(?:for|while)\s*\(", src):
        # ข้ามหัวลูปไปหา { ตัวเปิด body
        depth = 0
        i = m.end() - 1
        while i < len(src):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        j = src.find("{", i)
        if j < 0:
            continue
        depth = 0
        for k in range(j, len(src)):
            if src[k] == "{":
                depth += 1
            elif src[k] == "}":
                depth -= 1
                if depth == 0:
                    spans.append((j, k))
                    break
    return spans


ASKS = (
    "_confirmManualTopupBeforeSend",
    "_confirmServerMismatchBeforeSend",
    "_confirmUnverifiableTargetBeforeSend",
    "_confirmStaleTargetBeforeSend",
)
IMPORTS = ("_fetchTargetSunImport", "_importTargetSunForPayload")
VERIFY = "_verifySendBatchBeforeImport"
GATES = (
    ("send_target_shortfall", "pendingShortfall.push"),
    ("send_target_mismatch", "pendingMismatch.push"),
    ("send_target_unverifiable", "pendingUnverifiable.push"),
    ("send_target_stale", "pendingStale.push"),
)


class TestSendGuardIsTheOnlyEntryPoint(unittest.TestCase):
    """
    doLakehouseUpload = ตัวครอบบาง ๆ ที่กันกดซ้ำ แล้วเรียก pipeline จริง
    (_doLakehouseUploadInner) — เทสลำดับการส่งด้านล่างจึงอ่านตัวใน

    เดิมปุ่มถูก disable หลัง await หลายตัว ระหว่างนั้นดับเบิลคลิกยิงได้สองชุด
    แยกเป็นสองฟังก์ชันเพื่อให้ finally เคลียร์ธงได้ครบทุกทางออก รวม early return
    """

    def setUp(self):
        self.outer = _function_source("doLakehouseUpload")

    def test_outer_checks_the_flag_before_anything_else(self):
        body = self.outer.strip().lstrip("{").strip()
        self.assertTrue(
            body.startswith("if (_lakehouseSendInFlight)"),
            "ต้องเช็คธงเป็นอย่างแรกสุด ก่อน await ใด ๆ",
        )

    def test_outer_sets_and_always_clears_the_flag(self):
        self.assertIn("_lakehouseSendInFlight = true", self.outer)
        self.assertIn("finally", self.outer)
        self.assertRegex(
            self.outer,
            r"finally\s*\{[^}]*_lakehouseSendInFlight\s*=\s*false",
            "ต้องเคลียร์ธงใน finally ไม่งั้นพลาดครั้งเดียวปุ่มตายถาวร",
        )

    def test_outer_delegates_and_does_not_send_by_itself(self):
        self.assertIn("_doLakehouseUploadInner()", self.outer)
        for fn in IMPORTS + (VERIFY,):
            self.assertNotIn(fn, self.outer, f"ตัวครอบต้องไม่ทำงานส่งเอง ({fn})")

    def test_the_button_calls_the_guarded_function(self):
        """ปุ่มต้องยิงตัวครอบ ไม่ใช่ตัวในที่ไม่มีการกันกดซ้ำ"""
        with open(os.path.join(REPO, "frontend", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        with open(APP_JS, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("doLakehouseUpload()", html + src)
        self.assertNotIn("_doLakehouseUploadInner", html)
        # นับเฉพาะ "การเรียก" ไม่นับบรรทัดประกาศฟังก์ชัน
        calls = len(re.findall(r"(?<!function )_doLakehouseUploadInner\(\)", src))
        self.assertEqual(calls, 1, "ตัวในต้องถูกเรียกจากตัวครอบที่เดียวเท่านั้น")


class TestSendOrder(unittest.TestCase):
    def setUp(self):
        self.src = _function_source("_doLakehouseUploadInner")
        self.loops = _loop_body_spans(self.src)

    def _innermost_loop_around(self, idx: int):
        inner = [(a, b) for a, b in self.loops if a <= idx <= b]
        return min(inner, key=lambda s: s[1] - s[0]) if inner else None

    def test_every_import_happens_after_every_ask(self):
        """
        ไม่ให้ทีมไหนถูกส่งก่อนผู้ใช้ได้ตอบ — บั๊กเดิมคือ prepare→import วนทีละทีม
        พอทีมท้ายเจอปัญหาแล้วถาม ทีมแรก ๆ ก็ส่งไปแล้ว
        """
        last_ask = max(self.src.rfind(a) for a in ASKS)
        self.assertGreater(last_ask, -1, "ต้องยังมีจุดถามอยู่")
        for fn in IMPORTS:
            for m in re.finditer(re.escape(fn), self.src):
                self.assertGreater(m.start(), last_ask, f"{fn} ต้องอยู่หลังจุดถามทุกจุด")

    def test_loop_that_asks_never_imports(self):
        """
        การถามอยู่ในลูปได้ (ลูปเตรียมไฟล์วนซ้ำเพราะ server มี 2 ด่าน)
        แต่ลูปนั้นห้ามมีการส่ง — ถ้ามีเมื่อไหร่คือกลับไปเป็นบั๊กเดิมทันที
        """
        for ask in ASKS:
            idx = self.src.find(ask)
            if idx < 0:
                continue
            span = self._innermost_loop_around(idx)
            if span is None:
                continue   # อยู่นอกลูปยิ่งปลอดภัย
            body = self.src[span[0]:span[1]]
            for fn in IMPORTS:
                self.assertNotIn(
                    fn, body,
                    f"ลูปที่มี {ask} ต้องไม่เรียก {fn}",
                )

    def test_prepare_phase_does_not_import(self):
        first_ask = min(i for i in (self.src.find(a) for a in ASKS) if i > -1)
        prepare_phase = self.src[:first_ask]
        for fn in IMPORTS:
            self.assertNotIn(
                fn, prepare_phase,
                f"เฟสเตรียมไฟล์ต้องไม่เรียก {fn} — ต้องเตรียมให้ครบก่อนแล้วค่อยส่ง",
            )

    def test_both_gates_are_collected_not_decided_inline(self):
        """
        เจอ 409 ระหว่างเตรียม ต้อง "เก็บใส่ list" ไม่ใช่ถามทันที
        เพราะทีมถัด ๆ ไปอาจติดด่านเดียวกัน ต้องรวมแล้วถามทีเดียว
        """
        for code, bucket in GATES:
            idx = self.src.find(f'"{code}"')
            self.assertGreater(idx, -1, f"ต้องยังจับ {code} อยู่")
            self.assertIn(
                bucket, self.src[idx:idx + 300],
                f"{code} ต้องถูกเก็บใส่ {bucket} ไม่ใช่ตัดสินใจตรงนั้น",
            )

    def test_single_confirmation_per_gate(self):
        """ถามด่านละครั้งต่อการกดส่งหนึ่งครั้ง ไม่ถามซ้ำราย SL"""
        for ask in ASKS:
            self.assertEqual(self.src.count(ask), 1, f"{ask} ต้องมีจุดเรียกจุดเดียว")
        self.assertIn("_mergeShortfall", self.src, "ต้องรวม shortfall ของทุกทีมก่อนถาม")

    def test_legacy_fallback_does_not_swallow_business_404(self):
        """
        เส้นทางถอยไป "ส่งรวดเดียว" ข้ามประตูยืนยันทั้งสองด่าน จึงต้องเข้าเฉพาะตอน
        server ไม่มี endpoint จริง ๆ — ไม่ใช่ตอน prepare คืน 404 เพราะหาแบรนด์ไม่เจอ

        เดิม `return status === 405 || status === 404` เหมารวมทั้งสองกรณี
        """
        fn = _function_source_any("_targetSunPrepareUnsupported")
        self.assertRegex(
            fn, r"\bbody\b",
            "ต้องดู body ประกอบ ไม่ใช่ตัดสินจาก status อย่างเดียว",
        )
        self.assertNotRegex(
            re.sub(r"/\*.*?\*/|//[^\n]*", "", fn, flags=re.S),
            r"status\s*===\s*405\s*\|\|\s*status\s*===\s*404",
            "ห้ามกลับไปเหมา 404 = server เก่า",
        )
        # จุดเรียกต้องส่ง body ไปด้วย ไม่งั้นเช็คใหม่ไม่มีผล
        self.assertIn("_targetSunPrepareUnsupported(prepRes.status, prep)", self.src)

    def test_retry_loop_is_bounded(self):
        """ลูปเตรียมซ้ำต้องมีเพดาน ไม่งั้นถ้ายืนยันแล้วยังติดจะวนไม่จบ"""
        self.assertRegex(
            self.src, r"for\s*\(\s*let\s+round\s*=\s*0;\s*round\s*<\s*\d+",
            "ลูปเตรียมซ้ำต้องนับรอบแบบมีเพดาน",
        )

    def test_retry_bound_leaves_a_round_for_every_gate(self):
        """
        แต่ละรอบถามได้ด่านเดียว และยังต้องเหลือรอบให้ (ก) เตรียมใหม่หลังตัด SKU
        ระดับชุด และ (ข) เตรียมไฟล์จริงรอบสุดท้าย

        ถ้าเพิ่มด่านแล้วลืมขยายเพดาน ทีมที่ติดด่านสุดท้ายจะไม่มีรอบให้เตรียมไฟล์
        แล้วหลุดไปโยน "เตรียมไฟล์ไม่สำเร็จ" ทั้งที่ผู้ใช้ยืนยันครบแล้ว
        """
        m = re.search(r"for\s*\(\s*let\s+round\s*=\s*0;\s*round\s*<\s*(\d+)", self.src)
        self.assertIsNotNone(m)
        need = len(GATES) + 2
        self.assertGreaterEqual(
            int(m.group(1)), need,
            f"มี {len(GATES)} ด่าน + รอบตัด SKU ระดับชุด + รอบสุดท้าย = อย่างน้อย {need}",
        )

    def test_batch_verify_runs_before_every_import(self):
        """
        ด่านตรวจยอดรวมทั้งชุดต้องอยู่ก่อน import เสมอ — ถ้าตรวจหลังส่ง
        ทีมแรก ๆ ก็เข้า Target Sun ไปแล้ว ย้อนไม่ได้ (เหตุผลเดียวกับการแยกเตรียม/ส่ง)
        """
        idx = self.src.find(VERIFY)
        self.assertGreater(idx, -1, "ต้องยังมีด่านตรวจยอดรวมทั้งชุดอยู่")
        for fn in IMPORTS:
            for m in re.finditer(re.escape(fn), self.src):
                self.assertGreater(
                    m.start(), idx, f"{fn} ต้องอยู่หลังการตรวจยอดรวมทั้งชุด"
                )

    def test_loop_that_verifies_never_imports(self):
        idx = self.src.find(VERIFY)
        span = self._innermost_loop_around(idx)
        if span is None:
            return
        body = self.src[span[0]:span[1]]
        for fn in IMPORTS:
            self.assertNotIn(fn, body, f"ลูปที่ตรวจยอดรวมต้องไม่เรียก {fn}")

    def test_every_gate_sets_its_own_confirm_flag(self):
        """ยืนยันด่านหนึ่งต้องไม่ปลดล็อกอีกด่านที่ผู้ใช้ไม่เคยเห็น"""
        for flag in (
            "confirm_target_mismatch",
            "confirm_manual_topup",
            "confirm_unverifiable_target",
            "confirm_stale_target",
        ):
            self.assertIn(flag, self.src, f"ต้องตั้ง {flag} แยกกัน")


if __name__ == "__main__":
    unittest.main()
