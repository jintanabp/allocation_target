"""Install repo root into portable Python sys.path via site-packages .pth file."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE_PACKAGES = ROOT / "runtime" / "python" / "Lib" / "site-packages"
PTH_NAME = "_allocation_repo_root.pth"


def install() -> bool:
    if not SITE_PACKAGES.is_dir():
        return False
    path = SITE_PACKAGES / PTH_NAME
    # Plain path line — portable Python .pth has no __file__ in import lines.
    path.write_text(f"{ROOT}\n", encoding="utf-8")
    return True


def main() -> int:
    if install():
        print(f"OK: {SITE_PACKAGES / PTH_NAME}")
        return 0
    print("Skip: portable runtime not found (runtime/python/Lib/site-packages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
