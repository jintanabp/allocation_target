#!/usr/bin/env python3
"""จับภาพหน้าจอประกอบคู่มือ -> docs/images/*.png (ใช้ Edge headless)"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"
MOCK = ROOT / "docs" / "manual-shots" / "ui-mock.html"
LOGIN = ROOT / "frontend" / "index.html"

SHOTS: list[tuple[str, Path, str, int, int]] = [
    ("01-login.png", LOGIN, "", 1280, 900),
    ("02-step1-data.png", MOCK, "step=2", 1280, 780),
    ("03-step2-target.png", MOCK, "step=3", 1280, 620),
    ("04-step3-allocate.png", MOCK, "step=4", 1280, 720),
    ("05-result-table.png", MOCK, "step=5", 1280, 820),
    ("06-export.png", MOCK, "step=6", 1280, 680),
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


def capture(edge: Path, url: str, out: Path, width: int, height: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(edge),
        "--headless",
        "--disable-gpu",
        f"--window-size={width},{height}",
        f"--screenshot={out}",
        url,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> int:
    edge = find_edge()
    if not edge:
        print("ไม่พบ Microsoft Edge — ติดตั้ง Edge หรือจับภาพด้วยมือแล้วใส่ใน docs/images/", file=sys.stderr)
        return 1
    for name, html_path, query, w, h in SHOTS:
        if not html_path.is_file():
            print(f"skip {name}: missing {html_path}", file=sys.stderr)
            continue
        dest = OUT / name
        url = file_url(html_path, query)
        print(f"Capturing {name} ...")
        capture(edge, url, dest, w, h)
        print(f"  -> {dest} ({dest.stat().st_size:,} bytes)")
    print("Done. Run: python docs/build-manual-html.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
