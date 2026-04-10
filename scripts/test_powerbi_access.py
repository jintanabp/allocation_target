"""
ทดสอบว่า Service Principal เห็น workspace/dataset ใน Power BI REST API หรือไม่
(ไม่รัน DAX — ใช้แค่ GET)

จากรากโปรเจกต์:
  python scripts/test_powerbi_access.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.load_env import load_project_dotenv

load_project_dotenv()

from backend.fabric_dax_connector import FabricDAXConnector


def main() -> None:
    c = FabricDAXConnector()
    c.diagnose_powerbi_rest_access()


if __name__ == "__main__":
    main()
