"""ตั้งปลายทาง Target Sun ให้เทสเองแทนการอ่าน config/app_runtime.json ของเครื่องที่รัน

เทสชุดตรวจหลังส่ง (readback / นับแถว / ตรวจเป้าซ้ำก่อนส่ง) ตั้งใจทดสอบกรณีที่อ่านกับส่ง
เป็นระบบเดียวกัน — ถ้าปล่อยให้อ่านไฟล์ตั้งค่าจริง เครื่อง dev ที่ตั้ง prod ไว้จะผ่าน แต่ CI
(ไม่มีไฟล์นี้ใน git → ค่าตั้งต้น "test": อ่าน Prod ส่ง UAT) ทุกฟังก์ชันจะตอบ cross_env
แล้วข้ามการตรวจไป เทส 14 ตัวจึงแดงบน CI ตลอด (QA 21 ต.ค. 2026)
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch


def use_same_system_endpoints(testcase, preset: str = "prod") -> None:
    """ชี้ APP_RUNTIME_SETTINGS_PATH ไปไฟล์ชั่วคราวที่อ่าน/ส่งระบบเดียวกัน — คืนค่าเองตอนจบเทส"""
    tmp = tempfile.TemporaryDirectory()
    testcase.addCleanup(tmp.cleanup)
    path = os.path.join(tmp.name, "app_runtime.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"target_read_source": "targetsun", "target_endpoint_preset": preset}, f)
    env = patch.dict(os.environ, {"APP_RUNTIME_SETTINGS_PATH": path})
    env.start()
    testcase.addCleanup(env.stop)
