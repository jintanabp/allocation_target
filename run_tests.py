#!/usr/bin/env python3
"""Run unit tests from repo root.

Portable Python under runtime/ does not put the repo on sys.path, so
``python -m unittest tests.test_wh_split`` fails with ``No module named 'tests'``.
Use this script (or scripts/dev/run_tests.bat) instead.
"""

from __future__ import annotations

import argparse
import os
import sys
import unittest


def repo_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def ensure_utf8_console() -> None:
    """
    โค้ดที่เทสต์เรียกมี print อีโมจิอยู่ (📡/✅) — คอนโซลไทย (cp874) เข้ารหัสไม่ได้
    แล้วเทสต์ตกด้วย UnicodeEncodeError ทั้งที่ตรรกะถูก · แอปจริงตั้ง utf-8 ให้แล้ว
    ใน backend/main.py แต่เทสต์ไม่ได้ผ่านทางนั้น
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def ensure_repo_on_path() -> None:
    root = repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)


def normalize_module(name: str) -> str:
    n = (name or "").strip().replace("\\", "/").removesuffix(".py")
    if n.startswith("tests/"):
        n = n.removeprefix("tests/")
    if n.startswith("test_"):
        return n
    if n:
        return f"test_{n}"
    return ""


def build_suite(module: str | None) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    tests_dir = os.path.join(repo_root(), "tests")
    if not module:
        return loader.discover(tests_dir, pattern="test_*.py")
    stem = normalize_module(module)
    path = os.path.join(tests_dir, f"{stem}.py")
    if not os.path.isfile(path):
        raise SystemExit(f"ไม่พบไฟล์ทดสอบ: tests/{stem}.py")
    return loader.loadTestsFromName(f"tests.{stem}")


# ไฟล์ config ที่เทสต์ "ห้ามแตะ" — ค่าในนี้ตัดสินว่าข้อมูลจริงถูกส่งไปที่ไหน
# เคยพบว่า config/app_runtime.json ถูกเปลี่ยน test -> uat ระหว่างรันชุดเต็ม
# (เกิดเป็นครั้งคราว ยังหาตัวการไม่เจอ) ถ้าเกิดบนเซิร์ฟเวอร์คือส่งข้อมูลผิดปลายทาง
# ตัวกันนี้จับได้ทุกเทสต์ไม่ว่าตัวไหนเป็นคนเขียน แล้วกู้คืนให้อัตโนมัติ
_PROTECTED_CONFIGS = (
    "config/app_runtime.json",
    "config/user_access.json",
    "config/sl_links.json",
    "config/sku_links.json",
    "config/access_hierarchy.json",
    # persist_hierarchy เขียนคู่กับ access_hierarchy.json เสมอ แต่ path มาจาก
    # _repo_root() ไม่มี env คุม — ถ้าเทสต์ลืม patch จะค้างข้อมูลปลอมจนกว่าจะ rebuild
    "data/managers_cache.json",
    # ย้ายพนักงานข้ามทีม — เทสที่ลืมตั้ง env จะทำให้ของจริงเปลี่ยนสังกัดเงียบ ๆ
    "config/emp_assignments.json",
)


def _snapshot_protected() -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for rel in _PROTECTED_CONFIGS:
        p = os.path.join(repo_root(), rel)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                out[rel] = f.read()
    return out


def _restore_protected(before: dict[str, bytes]) -> list[str]:
    """คืนค่าไฟล์ที่ถูกแก้ คืนรายชื่อไฟล์ที่โดน"""
    dirty: list[str] = []
    for rel, data in before.items():
        p = os.path.join(repo_root(), rel)
        try:
            with open(p, "rb") as f:
                now = f.read()
        except OSError:
            continue
        if now != data:
            dirty.append(rel)
            with open(p, "wb") as f:
                f.write(data)
    return dirty


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_console()
    ensure_repo_on_path()
    parser = argparse.ArgumentParser(
        description="Run Target Allocation unit tests",
        epilog="ตัวอย่าง: python run_tests.py wh_split",
    )
    parser.add_argument(
        "module",
        nargs="?",
        help="โมดูลทดสอบ (เช่น wh_split) — ไม่ระบุ = รันทั้งหมด",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    # กันเทสต์ยิงเน็ตขึ้นระบบจริง — ต้องติดตั้งก่อนโหลดเทสต์
    # (unittest discover โหลดไฟล์ในโฟลเดอร์ tests แบบ top-level จึงไม่ผ่าน tests/__init__.py)
    from tests import _netguard

    _netguard.install()

    suite = build_suite(args.module)
    protected_before = _snapshot_protected()
    try:
        result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    finally:
        # ต้องกู้คืนแม้ถูก Ctrl+C หรือเทสต์เรียก SystemExit ไม่งั้น config ค้างสภาพที่ถูกแก้
        dirty = _restore_protected(protected_before)
    if dirty:
        print(
            "\n"
            + "!" * 70
            + "\nเทสต์ไปแก้ไฟล์ config จริง (กู้คืนให้แล้ว):\n  - "
            + "\n  - ".join(dirty)
            + "\n\nเทสต์ต้องเขียนลงไฟล์ชั่วคราวเสมอ — ตั้ง env ของ store นั้น"
            "\n(เช่น APP_RUNTIME_SETTINGS_PATH) ชี้ไป tempdir ก่อนเรียกฟังก์ชันที่เขียนไฟล์"
            "\n" + "!" * 70,
            file=sys.stderr,
        )
        return 1

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
