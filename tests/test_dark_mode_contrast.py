"""
ตรึงโหมดมืดด้วยการคำนวณสีจริง ไม่ใช่การเดาจากชื่อ token

โหมดมืดทำด้วยการสลับค่า token ชุดเดียว (`:root[data-theme="dark"]`) ซึ่งแปลว่า
"สีตายตัวหนึ่งบรรทัด" ที่ใครเผลอเขียนลงไป จะไม่มีอะไรเตือนเลย — มันถูกในโหมดสว่าง
และเพี้ยนในโหมดมืดเงียบ ๆ จนกว่าจะมีคนเปิดเจอ

เทสนี้จึงคลี่ var() ตามชุดค่าของแต่ละโหมด แล้วคิด contrast ratio (WCAG 2.x) จริง
บั๊กที่กันไว้ (เจอจริงทั้งหมด 2026-08-20):

* `.btn-ms` พื้น #2f2f2f ตายตัว + `color: var(--white)` — โหมดมืด `--white`
  คือ "สีพื้นการ์ด" (กรมเข้ม) ไม่ใช่สีขาว ตัวหนังสือบนปุ่มล็อกอิน Microsoft
  จึงเหลือ contrast 1.27:1 = มองไม่เห็น และนั่นคือปุ่มแรกที่ผู้ใช้เจอ
* `.btn-realloc` ตัวหนังสือ `white` ตายตัวบนพื้น `var(--amber)` ซึ่งโหมดมืด
  สว่างจ้า → 1.67:1
* `.btn-banner-close` พื้น `white` ตายตัว → แผ่นขาวจ้าคาหน้าจอมืด
* token ที่ถูกเรียกใช้แต่ไม่เคยมีใครนิยาม (`--bg-card` `--bg-sub` ฯลฯ)
  → พื้นการ์ดหายไปเฉย ๆ หรือค้างสีสว่างจาก fallback
"""

from __future__ import annotations

import os
import re
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

CSS_PATH = os.path.join(REPO, "frontend", "style.css")

# สีพื้นหน้าจอของแต่ละโหมด — ใช้เป็นฉากหลังตอนผสมสีโปร่งแสง
PAGE_BG = {"light": "#F1F5F9", "dark": "#0B1220"}

# token ที่ตั้งค่าจาก JS ตอน runtime ไม่ใช่จาก CSS — ไม่ต้องมีนิยามในไฟล์
RUNTIME_TOKENS = {
    "--sup-band",          # สีประจำทีมในโหมดรวมภาค (app.js ใส่ inline)
    "--w",                 # ความกว้างแท่งกราฟอธิบายการกระจาย
    "--result-head-h",
    "--result-head-row1-h",
    "--result-foot-h",
    "--result-foot-row2-h",
    "--view-as-banner-h",
}

# ข้อยกเว้นที่จงใจ — ระบุเหตุผลไว้ทุกตัว ห้ามเติมโดยไม่มีเหตุผล
CONTRAST_ALLOW = {
    # ปุ่มดาวน์โหลดตอนถูกปิด — สถานะ disabled ต้องดูจางกว่าปกติจึงจะสื่อว่ากดไม่ได้
    # และ WCAG ยกเว้น control ที่ใช้งานไม่ได้ไว้อยู่แล้ว
    (".btn-dl--disabled", "light"),
    (".btn-dl--disabled", "dark"),
    (".btn-dl--disabled:hover", "light"),
    (".btn-dl--disabled:hover", "dark"),
}

# ปุ่ม/ป้ายที่ตั้งใจให้พื้นสว่างเด่นในโหมดมืด แม้จะเขียนสีตายตัว
# (ปกติควรว่าง — ถ้าต้องเติม ให้ถามก่อนว่าทำไมถึงใช้ token ไม่ได้)
LITERAL_BRIGHT_BG_ALLOW: set = set()


def _strip_comments(css: str) -> str:
    """
    ลบคอมเมนต์แต่คงจำนวนบรรทัดไว้เท่าเดิม เพื่อให้เลขบรรทัดที่รายงานยังตรงกับไฟล์จริง

    จำเป็นจริง ๆ ไม่ใช่ความสวยงาม: ไฟล์นี้อธิบายเหตุผลไว้เหนือ token แทบทุกตัว และ
    คอมเมนต์ภาษาไทยมี ":" อยู่ข้างใน พอ split(";") แล้วคอมเมนต์จะติดมากับ token
    ตัวถัดไป ทำให้ตัวนั้น "หายไป" จากสายตาของตัวตรวจเงียบ ๆ — ตัวตรวจที่มองไม่เห็น
    ของบางชิ้นอันตรายกว่าไม่มีตัวตรวจ เพราะมันรายงานว่าผ่าน
    """
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), css, flags=re.S)


def _css() -> str:
    with open(CSS_PATH, encoding="utf-8") as fh:
        return _strip_comments(fh.read())


def _token_block(css: str, selector: str) -> dict:
    out: dict = {}
    for m in re.finditer(re.escape(selector) + r"\s*\{([^}]*)\}", css):
        for decl in m.group(1).split(";"):
            if ":" not in decl:
                continue
            name, value = decl.split(":", 1)
            name = name.strip()
            if name.startswith("--"):
                out[name] = value.strip()
    return out


def _resolve(value: str, tokens: dict, depth: int = 0) -> str:
    """คลี่ var() ซ้อนกันจนได้ค่าจริงของโหมดนั้น"""
    if depth > 14 or not value:
        return value
    m = re.search(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*(?:\([^()]*\))?[^()]*)\s*)?\)", value)
    if not m:
        return value
    replacement = tokens.get(m.group(1))
    if replacement is None:
        replacement = m.group(2) or ""
    return _resolve(value[: m.start()] + replacement + value[m.end():], tokens, depth + 1)


def _to_rgb(raw: str):
    c = raw.strip().lower()
    m = re.match(r"^#([0-9a-f]{3})$", c)
    if m:
        return tuple(int(ch * 2, 16) for ch in m.group(1))
    m = re.match(r"^#([0-9a-f]{6})$", c)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = re.match(r"^#([0-9a-f]{8})$", c)
    if m:
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16) / 255.0)
    m = re.match(r"^rgba?\(([^)]+)\)$", c)
    if m:
        parts = [p for p in re.split(r"[,\s]+", m.group(1)) if p]
        try:
            rgb = [int(float(x)) for x in parts[:3]]
        except (ValueError, IndexError):
            return None
        alpha = float(parts[3]) if len(parts) > 3 else 1.0
        return (rgb[0], rgb[1], rgb[2], alpha)
    return {"white": (255, 255, 255), "black": (0, 0, 0)}.get(c)


def _luminance(rgb) -> float:
    def channel(v: float) -> float:
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2])


def _flatten(colour, backdrop):
    """ผสมสีโปร่งแสงลงบนฉากหลัง เพื่อให้คิด contrast ได้จริง"""
    if len(colour) == 4:
        a = colour[3]
        return tuple(colour[i] * a + backdrop[i] * (1 - a) for i in range(3))
    return colour[:3]


def _contrast(a, b) -> float:
    la, lb = _luminance(a), _luminance(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


def _rules(css: str):
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector, body = m.group(1).strip(), m.group(2)
        if selector.startswith("@") or selector.startswith("--") or ":root" in selector:
            continue
        line = css[: m.start()].count("\n") + 1
        decls: dict = {}
        for decl in body.split(";"):
            if ":" in decl:
                k, v = decl.split(":", 1)
                decls.setdefault(k.strip().lower(), v.strip())
        yield line, " ".join(selector.split()), decls


def _background_of(decls: dict):
    return decls.get("background-color") or decls.get("background")


def _solid_background(raw: str, tokens: dict, mode: str):
    value = _resolve(raw, tokens).replace("!important", "").strip()
    if "gradient" in value:
        # ไล่ระดับ: ใช้จุดสีแรกเป็นตัวแทน (จุดที่ตัวหนังสือมักทับอยู่)
        m = re.search(r"gradient\([^)]*?(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))", value)
        value = m.group(1) if m else ""
    if "color-mix" in value:
        return None
    rgb = _to_rgb(value)
    if not rgb:
        return None
    return _flatten(rgb, _to_rgb(PAGE_BG[mode]))


class TestTokensAllDefined(unittest.TestCase):
    """var(--x) ที่ไม่มีนิยาม = คืนค่าว่าง พื้นหลังหายไปทั้งใบโดยไม่มี error"""

    def test_no_undefined_custom_property(self):
        css = _css()
        used = set(re.findall(r"var\(\s*(--[\w-]+)", css))
        defined = set(re.findall(r"(?m)^\s*(--[\w-]+)\s*:", css))
        missing = sorted(used - defined - RUNTIME_TOKENS)
        self.assertEqual(
            missing,
            [],
            "token ถูกเรียกใช้แต่ไม่เคยมีนิยาม — โหมดมืดจะไม่มีอะไรให้สลับ: "
            + ", ".join(missing),
        )


class TestDarkTokensCoverColours(unittest.TestCase):
    """token สีทุกตัวต้องมีคู่ในโหมดมืด ไม่งั้นค่าโหมดสว่างจะค้างอยู่"""

    def test_every_colour_token_has_a_dark_value(self):
        css = _css()
        light = _token_block(css, ":root")
        dark = _token_block(css, ':root[data-theme="dark"]')
        stale = []
        for name, value in light.items():
            if "var(" in value:  # alias — ตามตัวจริงไปเอง
                continue
            if _to_rgb(value.replace("!important", "").strip()) is None:
                continue  # ไม่ใช่ token สี (ขนาด/ฟอนต์/มุมโค้ง)
            if name not in dark:
                stale.append(name)
        self.assertEqual(
            stale, [], "token สีที่ไม่มีค่าโหมดมืด: " + ", ".join(sorted(stale))
        )

    def test_shadows_are_deeper_in_dark(self):
        """เงาสีกรมบนพื้นมืดแทบไม่เหลืออะไร การ์ดจะจมหายไปกับพื้นหลัง"""
        dark = _token_block(_css(), ':root[data-theme="dark"]')
        for name in ("--shadow-sm", "--shadow-md", "--shadow-lg"):
            self.assertIn(name, dark, f"{name} ต้องถูกกำหนดใหม่ในโหมดมืด")


class TestContrast(unittest.TestCase):
    """ทุกกฎที่กำหนดทั้งสีตัวอักษรและพื้นหลัง ต้องอ่านออกในทั้งสองโหมด"""

    MIN_RATIO = 3.0

    def test_text_is_readable_in_both_modes(self):
        css = _css()
        light = _token_block(css, ":root")
        dark = dict(light)
        dark.update(_token_block(css, ':root[data-theme="dark"]'))

        failures = []
        for line, selector, decls in _rules(css):
            if "color" not in decls:
                continue
            bg_raw = _background_of(decls)
            if not bg_raw:
                continue
            for mode, tokens in (("light", light), ("dark", dark)):
                if (selector, mode) in CONTRAST_ALLOW:
                    continue
                bg = _solid_background(bg_raw, tokens, mode)
                if bg is None:
                    continue
                fg_raw = _resolve(decls["color"], tokens).replace("!important", "").strip()
                if "color-mix" in fg_raw:
                    continue
                fg = _to_rgb(fg_raw)
                if not fg:
                    continue
                ratio = _contrast(_flatten(fg, bg), bg)
                if ratio < self.MIN_RATIO:
                    failures.append(
                        f"  {ratio:.2f}:1 [{mode}] style.css:{line} {selector}"
                        f"  (color:{decls['color']} · bg:{bg_raw})"
                    )
        self.assertEqual(
            failures,
            [],
            "ตัวหนังสืออ่านไม่ออก — ถ้าเป็นสีตายตัว ให้เปลี่ยนเป็น token:\n"
            + "\n".join(failures),
        )


class TestNoBrightPatchesInDark(unittest.TestCase):
    """
    พื้นสว่างที่หลงเหลือ = แผ่นขาวคาหน้าจอมืด ซึ่งคือสิ่งที่ผู้ใช้เรียกว่า "สีเพี้ยน"

    ตรวจเฉพาะกฎที่เขียน "สีตายตัว" ลงไปตรง ๆ (รวมค่า fallback ใน var(--x, #fff))
    เพราะพื้นสว่างที่มาจาก token เป็นการตัดสินใจที่ตั้งใจ — ปุ่มหลักสีม่วงหรือป้าย
    สถานะสีเหลืองก็ควรเด่นออกมาจากพื้นมืดอยู่แล้ว ส่วนสีตายตัวคือของที่ "ลืมไว้"
    ไม่มีทางตามโหมดได้เลยไม่ว่าจะตั้งใจหรือไม่
    """

    MAX_LUMINANCE = 0.35
    # (?<![-\w]) กัน "white" ที่อยู่ในชื่อ token อย่าง var(--white) ซึ่งไม่ใช่สีตายตัว
    LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|(?<![-\w])rgba?\(|(?<![-\w])(?:white|black)\b")

    def test_no_hardcoded_light_background(self):
        css = _css()
        dark = _token_block(css, ":root")
        dark.update(_token_block(css, ':root[data-theme="dark"]'))

        failures = []
        for line, selector, decls in _rules(css):
            bg_raw = _background_of(decls)
            if not bg_raw or selector in LITERAL_BRIGHT_BG_ALLOW:
                continue
            if not self.LITERAL.search(bg_raw):
                continue  # มาจาก token ล้วน — สลับตามโหมดอยู่แล้ว
            bg = _solid_background(bg_raw, dark, "dark")
            if bg is None:
                continue
            lum = _luminance(bg)
            if lum > self.MAX_LUMINANCE:
                failures.append(f"  L={lum:.2f} style.css:{line} {selector}  (bg:{bg_raw})")
        self.assertEqual(
            failures,
            [],
            "พื้นหลังเขียนสีตายตัวไว้ และยังสว่างอยู่ในโหมดมืด:\n" + "\n".join(failures),
        )


class TestThemeAppliedBeforeFirstPaint(unittest.TestCase):
    """
    เดิม initTheme() ทำงานตอน DOMContentLoaded ซึ่งเกิด "หลัง" เบราว์เซอร์วาดจอครั้งแรก
    คนที่เลือกโหมดมืดจึงเห็นจอขาววาบทุกครั้งที่เปิดหรือรีเฟรช
    """

    def _index_html(self) -> str:
        with open(os.path.join(REPO, "frontend", "index.html"), encoding="utf-8") as fh:
            return fh.read()

    def _app_js(self) -> str:
        with open(os.path.join(REPO, "frontend", "app.js"), encoding="utf-8") as fh:
            return fh.read()

    def test_inline_script_sits_inside_head(self):
        html = self._index_html()
        head = html[: html.index("</head>")]
        self.assertIn(
            "data-theme",
            head,
            "ต้องตั้ง data-theme ใน <head> ก่อนหน้าจอถูกวาด ไม่ใช่ตอน DOMContentLoaded",
        )

    def test_storage_key_matches_app_js(self):
        html = self._index_html()
        head = html[: html.index("</head>")]
        keys = set(re.findall(r'localStorage\.getItem\("([^"]+)"\)', head))
        m = re.search(r'THEME_KEY\s*=\s*"([^"]+)"', self._app_js())
        self.assertIsNotNone(m, "หา THEME_KEY ใน app.js ไม่เจอ")
        self.assertIn(
            m.group(1),
            keys,
            "key ของสคริปต์ใน <head> ต้องตรงกับ THEME_KEY ใน app.js "
            "ไม่งั้นจอจะกระพริบเป็นโหมดผิดทุกครั้งที่โหลด",
        )

    def test_default_is_light_not_system_preference(self):
        """ผู้ใช้กลุ่มนี้คุ้นกับจอสว่าง — จอต้องไม่เปลี่ยนเองตามการตั้งค่าเครื่อง"""
        html = self._index_html()
        head = html[: html.index("</head>")]
        self.assertNotIn("prefers-color-scheme", head)
        self.assertNotIn("prefers-color-scheme", _css())


if __name__ == "__main__":
    unittest.main()
