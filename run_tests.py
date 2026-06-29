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


def main(argv: list[str] | None = None) -> int:
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
    suite = build_suite(args.module)
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
