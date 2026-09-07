"""
ชุดข้อมูลสำหรับพัฒนาในเครื่อง — รวม config + แคชของ server เป็น .zip ไฟล์เดียว

ทำไมต้องมี: เวลาพัฒนา/ไล่ปัญหาในเครื่อง ต้องใช้ข้อมูลชุดเดียวกับที่ server ใช้จริง
ไม่งั้นผลที่เห็นในเครื่องกับบนเซิร์ฟเวอร์ไม่ตรงกันโดยไม่มีใครรู้ตัว — เคยเจอจริง:
ซุปคนหนึ่งเห็น 3 ทีมในเครื่องแต่ 4 ทีมบนเซิร์ฟเวอร์ เพราะ `codes_with_own_salesmen()`
อ่าน **ชื่อไฟล์** ใน data/ เป็น input ทางอ้อม

ของหลายอย่างในนี้ **ไม่มีปุ่มดาวน์โหลดของตัวเอง** — `sl_links` / `sku_links` /
`access_hierarchy` / `admin_permissions` / `emp_assignments` และแคช `tga_lines_*` /
`emp_cache_*` เดิมต้องขอ IT ก๊อปจากเครื่อง server ให้ทีละรอบ

อ่านอย่างเดียวล้วน ๆ ไม่เขียนไฟล์ ไม่แตะ Fabric ไม่แตะ Target Sun
"""

from __future__ import annotations

import glob
import io
import os
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


# (ชื่อส่วน, glob สัมพัทธ์กับรากโปรเจกต์, อยู่ในชุดเบาไหม, คำอธิบาย)
#
# ระบุเป็น glob ตายตัวทั้งหมด ไม่รับ path จากผู้เรียก — กันการหลุดออกนอกโฟลเดอร์
# ที่ตั้งใจ และกัน `.env` (ซึ่งมี secret) ติดไปด้วยเด็ดขาด
BUNDLE_PARTS: tuple[tuple[str, str, bool, str], ...] = (
    ("config", "config/*.json", True, "สิทธิ์ผู้ใช้ · ผูกรหัส · ลำดับชั้น · สิทธิ์หน้าแอดมิน"),
    ("grain", "data/tga_lines_*.csv", True, "เป้า grain จาก Target Sun (ตัวที่ทำให้ขอบเขตทีมต่างกัน)"),
    ("emp_cache", "data/emp_cache_*.csv", True, "รายชื่อพนักงานต่อทีม×งวด"),
    ("target_boxes", "data/target_boxes_*.csv", True, "เป้าหีบต่อ SKU ของแต่ละทีม"),
    ("allocations", "data/allocations/*.json", False, "ผลกระจายที่บันทึกไว้ (ก้อนใหญ่)"),
    ("baselines", "data/baselines/*.json", False, "เป้าตั้งต้นของงวด"),
)

#: ส่วนที่ถูกตัดออกเมื่อขอชุดเบา — ประกาศไว้ให้เทสอ่านได้ ไม่ต้องเดาจากตาราง
HEAVY_PARTS = tuple(name for name, _g, light, _d in BUNDLE_PARTS if not light)


def _bangkok_stamp() -> str:
    return datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y%m%d_%H%M")


def bundle_filename(light: bool = False) -> str:
    return f"dev_bundle{'_light' if light else ''}_{_bangkok_stamp()}.zip"


def _iter_files(pattern: str) -> list[str]:
    root = _repo_root()
    return sorted(p for p in glob.glob(os.path.join(root, pattern)) if os.path.isfile(p))


def build_dev_bundle(light: bool = False) -> tuple[bytes, list[dict]]:
    """
    คืน (ไบต์ของ zip, manifest)

    light=True ตัดส่วนที่เป็นก้อนใหญ่ออก (ผลกระจาย + เป้าตั้งต้น) เหลือเฉพาะ config
    กับแคชโครงสร้าง ซึ่งเป็นชุดที่ใช้บ่อยที่สุดตอนไล่เรื่องสิทธิ์/การจับกลุ่มทีม
    """
    root = _repo_root()
    manifest: list[dict] = []
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name, pattern, in_light, desc in BUNDLE_PARTS:
            if light and not in_light:
                manifest.append(
                    {"part": name, "files": 0, "bytes": 0, "desc": desc, "skipped": "ชุดเบา"}
                )
                continue
            files = _iter_files(pattern)
            total = 0
            for path in files:
                rel = os.path.relpath(path, root).replace("\\", "/")
                zf.write(path, rel)
                total += os.path.getsize(path)
            manifest.append({"part": name, "files": len(files), "bytes": total, "desc": desc})

        zf.writestr("MANIFEST.txt", _manifest_text(manifest, light))

    return buf.getvalue(), manifest


def _manifest_text(manifest: list[dict], light: bool) -> str:
    lines = [
        "ชุดข้อมูลสำหรับพัฒนาในเครื่อง",
        f"สร้างเมื่อ {datetime.now(ZoneInfo('Asia/Bangkok')).strftime('%d/%m/%Y %H:%M')} (เวลาไทย)",
        f"ชนิด: {'เบา (ไม่มีผลกระจาย/เป้าตั้งต้น)' if light else 'เต็ม'}",
        "",
        "สิ่งที่อยู่ในไฟล์นี้",
    ]
    for m in manifest:
        if m.get("skipped"):
            lines.append(f"  - {m['part']:<13} ข้าม ({m['skipped']}) — {m['desc']}")
        else:
            mb = m["bytes"] / 1024 / 1024
            lines.append(f"  - {m['part']:<13} {m['files']:>5} ไฟล์ · {mb:>7.2f} MB — {m['desc']}")
    lines += [
        "",
        "⚠ มีอีเมลพนักงานทั้งองค์กรอยู่ใน config/user_access.json — เก็บไว้ในเครื่องเท่านั้น",
        "",
        "วิธีใช้: แตกลง data/server_snapshot/ (อยู่ใน .gitignore แล้ว)",
        "**อย่าแตกทับ config/ ตรง ๆ** เพราะ user_access.json ถูก git track อยู่",
        "เผลอ commit แล้วจะทับค่าที่แอดมินตั้งไว้บนเซิร์ฟเวอร์ (เกิดจริง 25-26 ส.ค. 2026)",
        "ตอนรันทดสอบให้ชี้ store ไปที่ snapshot ด้วย env ในเครื่อง เช่น",
        "  USER_ACCESS_JSON_PATH=data/server_snapshot/config/user_access.json",
        "",
        "ไม่มี .env อยู่ในชุดนี้โดยตั้งใจ (มี secret)",
    ]
    return "\n".join(lines) + "\n"
