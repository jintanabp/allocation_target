"""
กันชื่อที่ "ใช้แต่ไม่ได้ import" ในฝั่ง backend

บั๊กแบบนี้เทสปกติจับไม่ได้ เพราะ Python ไม่บ่นตอน import — มันระเบิดเป็น
NameError ตอนรันถึงบรรทัดนั้นพอดี ถ้าบรรทัดนั้นอยู่ในเส้นทางที่เทสไม่ได้เดิน
(เช่น เส้นทางของแอดมินรายภาคโดยเฉพาะ) ก็จะหลุดขึ้น production ไปเงียบ ๆ

ของจริงที่เคยหลุด: `ROLE_REGION_ADMIN` ถูกใช้ใน backend/routers/admin.py
สามจุด (supervisor-codes / supervisor-team / usage-logs) แต่ไม่มีใน import
→ แอดมินรายภาคเรียกทีไรได้ 500 ทุกครั้ง ส่วน dev ไม่เจอเพราะไม่เข้าเงื่อนไข

วิธีตรวจ: เดิน bytecode หาโอปโค้ด LOAD_GLOBAL (= การอ่านชื่อระดับโมดูล)
แล้วเทียบกับ globals จริงของโมดูลหลัง import + builtins
เทียบเท่า scripts/dev/check_js_undef.js ที่ทำกับ app.js ฝั่งหน้าเว็บ
"""
from __future__ import annotations

import builtins
import dis
import importlib
import pkgutil
import types
import unittest

import backend

_BUILTINS = frozenset(dir(builtins))


def _all_code_objects(code: types.CodeType):
    """ตัวโมดูลเอง + ทุกฟังก์ชัน/คลาส/comprehension ที่ซ้อนอยู่ข้างใน"""
    stack = [code]
    while stack:
        cur = stack.pop()
        yield cur
        for const in cur.co_consts:
            if isinstance(const, types.CodeType):
                stack.append(const)


def undefined_globals(module: types.ModuleType) -> list[tuple[str, str, int]]:
    """คืน (ชื่อฟังก์ชัน, ชื่อที่หาไม่เจอ, บรรทัด) ของโมดูลนี้"""
    with open(module.__file__, encoding="utf-8") as fh:
        code = compile(fh.read(), module.__file__, "exec")
    known = set(vars(module))
    found: list[tuple[str, str, int]] = []
    for co in _all_code_objects(code):
        for ins in dis.get_instructions(co):
            if ins.opname != "LOAD_GLOBAL":
                continue
            name = ins.argval
            if name in known or name in _BUILTINS:
                continue
            line = (ins.positions.lineno if ins.positions else None) or 0
            found.append((co.co_name, name, line))
    return found


class TestNoUndefinedGlobals(unittest.TestCase):
    def test_every_backend_module_resolves_its_names(self):
        problems: list[str] = []
        scanned = 0
        for mod in pkgutil.walk_packages(backend.__path__, "backend."):
            try:
                m = importlib.import_module(mod.name)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{mod.name}: import ไม่ผ่าน — {type(exc).__name__}: {exc}")
                continue
            if not getattr(m, "__file__", None):
                continue
            scanned += 1
            for fn, name, line in sorted(set(undefined_globals(m))):
                problems.append(f"{mod.name}:{line} ใน {fn}() ใช้ '{name}' ที่ไม่ได้ import")

        self.assertGreater(scanned, 20, "ต้องสแกนได้จริงหลายโมดูล ไม่ใช่เทสหลอกที่ผ่านเพราะไม่เจออะไร")
        self.assertEqual([], problems, "\n" + "\n".join(problems))

    def test_detector_actually_catches_a_missing_name(self):
        """
        พิสูจน์ว่าตัวตรวจไม่ได้ผ่านเพราะมันตรวจไม่เป็น — ประกอบโมดูลปลอม
        ที่จงใจใช้ชื่อซึ่งไม่มีอยู่จริง แล้วต้องถูกจับได้
        """
        import tempfile
        import os

        src = "def handler():\n    return SOME_NAME_THAT_IS_NOT_IMPORTED\n"
        fd, path = tempfile.mkstemp(suffix=".py")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(src)
            fake = types.ModuleType("fake_module")
            fake.__file__ = path
            hits = undefined_globals(fake)
            self.assertEqual(
                [("handler", "SOME_NAME_THAT_IS_NOT_IMPORTED", 2)], hits
            )
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
