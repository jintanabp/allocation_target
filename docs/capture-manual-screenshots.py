#!/usr/bin/env python3
"""จับภาพหน้าจอประกอบคู่มือ -> docs/images/*.png

ใช้หน้า mock ใน docs/manual-shots/ (โครงสร้างตรงแอป, ตัวเลขเป็นตัวอย่าง)
ถ้าต้องการรูปจาก UAT จริง: แทนที่ไฟล์ใน docs/images/ แล้วรัน build-manual-html.py --pdf
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"
LOGIN = ROOT / "docs" / "manual-shots" / "login-mock.html"
MOCK = ROOT / "docs" / "manual-shots" / "ui-mock.html"

# (filename, html, query, width, height, virtual_time_ms)
SHOTS: list[tuple[str, Path, str, int, int, int]] = [
    ("01-login.png", LOGIN, "", 1280, 900, 4000),
    ("02-step1-data.png", MOCK, "step=2", 1280, 920, 3000),
    ("03-step2-target.png", MOCK, "step=3", 1280, 860, 3000),
    ("04-step3-allocate.png", MOCK, "step=4", 1280, 980, 3000),
    ("05-result-table.png", MOCK, "step=5", 1280, 940, 3000),
    ("06-export.png", MOCK, "step=6", 1280, 820, 3000),
]


def find_edge() -> Path | None:
    for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", "")):
        if not base:
            continue
        p = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if p.is_file():
            return p
    return None


def file_url(path: Path, query: str = "") -> str:
    url = path.resolve().as_uri()
    if query:
        url += ("&" if "?" in url else "?") + query
    return url


def capture(edge: Path, url: str, out: Path, width: int, height: int, virtual_ms: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(edge),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--window-size={width},{height}",
        f"--virtual-time-budget={virtual_ms}",
        f"--screenshot={out}",
        url,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> int:
    edge = find_edge()
    if not edge:
        print("ไม่พบ Microsoft Edge — ติดตั้ง Edge หรือจับภาพด้วยมือแล้วใส่ใน docs/images/", file=sys.stderr)
        return 1
    for name, html_path, query, w, h, vms in SHOTS:
        if not html_path.is_file():
            print(f"skip {name}: missing {html_path}", file=sys.stderr)
            continue
        dest = OUT / name
        url = file_url(html_path, query)
        print(f"Capturing {name} ...")
        capture(edge, url, dest, w, h, vms)
        print(f"  -> {dest} ({dest.stat().st_size:,} bytes)")
    print("Done. Run: python docs/build-manual-html.py --pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
