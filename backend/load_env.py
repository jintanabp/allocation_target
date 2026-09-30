"""
โหลดตัวแปรสภาพแวดล้อม: config/.env ก่อน แล้ว .env ที่ราก

ลำดับความสำคัญจริง (load_dotenv ค่าเริ่มต้น override=False = ไม่ทับค่าที่มีอยู่แล้ว):
  1. environment ของโปรเซส (เช่นตั้งใน service/IIS)  ← ชนะเสมอ
  2. config/.env                                        ← โหลดก่อน จึงชนะ .env ที่ราก
  3. .env ที่ราก                                         ← เติมได้เฉพาะตัวแปรที่ยังไม่มี
เอกสารเดิมบอกว่า "ราก override" ซึ่งกลับกับโค้ด (ผลตรวจ §5.1-3) — แก้เอกสารให้ตรงโค้ด ไม่แก้โค้ด
เพราะถ้าสลับลำดับ server ที่มีค่าซ้ำสองไฟล์จะได้ค่าอีกชุดทันทีโดยไม่มีใครตั้งใจ
"""
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
