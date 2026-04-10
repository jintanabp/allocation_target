"""โหลดตัวแปรสภาพแวดล้อม: config/.env ก่อน แล้ว .env ที่ราก (ราก override ค่าซ้ำได้)."""
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_project_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = project_root()
    for rel in ("config/.env", ".env"):
        p = root / rel
        if p.is_file():
            load_dotenv(p)
