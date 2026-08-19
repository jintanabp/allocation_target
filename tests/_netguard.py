"""กันชุดเทสต์ยิงเน็ตขึ้นระบบจริงของบริษัท

เจอมาแล้ว: การเพิ่ม "เทียบเป้าปัจจุบันก่อนส่ง" เข้าไปในตัวสร้างไฟล์ ทำให้เทสต์ที่
สร้างไฟล์ (ซึ่งไม่รู้เรื่องเน็ตเลย) ยิง query ขึ้น production read API ระหว่างรันชุดเทสต์

กันด้วย env อย่างเดียวไม่พอ — เทสต์ตัวอื่นตั้ง TARGETSUN_READ_ENABLED=1 แล้วไม่คืนค่า
ทำให้กันชนหลุดตามลำดับการรัน จึงบล็อกที่ชั้น HTTP ไปเลย ไม่ขึ้นกับ env ของใคร
(เทสต์ที่ mock requests เองยัง patch ทับได้ตามปกติ)

install() ถูกเรียกจาก run_tests.py, tests/__init__.py และ tests/conftest.py
เพราะแต่ละวิธีรัน (run_tests / unittest discover / pytest) โหลดไฟล์ไม่เหมือนกัน
"""

from __future__ import annotations

import os

BLOCKED_HOST_MARKERS = (
    "sahapat.com",
    "api.powerbi.com",
    "onelake.dfs.fabric.microsoft.com",
    "login.microsoftonline.com",
)


def install() -> None:
    os.environ.setdefault("TARGETSUN_READ_ENABLED", "0")
    try:
        import requests
        from requests.sessions import Session
    except Exception:  # ไม่มี requests ก็ไม่มีอะไรให้กัน
        return

    if getattr(Session.request, "_alloc_test_guard", False):
        return

    real_request = Session.request

    def guarded(self, method, url, *args, **kwargs):
        target = str(url or "")
        for marker in BLOCKED_HOST_MARKERS:
            if marker in target:
                raise RuntimeError(
                    "เทสต์พยายามเรียกระบบจริง — ห้ามเด็ดขาด ให้ mock แทน: "
                    f"{str(method).upper()} {target}"
                )
        return real_request(self, method, url, *args, **kwargs)

    guarded._alloc_test_guard = True
    Session.request = guarded
    requests.Session.request = guarded
