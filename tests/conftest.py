"""pytest โหลดไฟล์นี้อัตโนมัติ — ติดตั้งกันชนเน็ตก่อนเทสต์ตัวแรกจะรัน"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tests import _netguard  # noqa: E402

_netguard.install()
