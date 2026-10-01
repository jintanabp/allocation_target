"""กันชุดเทสต์ยิงเน็ตขึ้นระบบจริงของบริษัท

เจอมาแล้ว: การเพิ่ม "เทียบเป้าปัจจุบันก่อนส่ง" เข้าไปในตัวสร้างไฟล์ ทำให้เทสต์ที่
สร้างไฟล์ (ซึ่งไม่รู้เรื่องเน็ตเลย) ยิง query ขึ้น production read API ระหว่างรันชุดเทสต์

กันด้วย env อย่างเดียวไม่พอ — เทสต์ตัวอื่นตั้ง TARGETSUN_READ_ENABLED=1 แล้วไม่คืนค่า
ทำให้กันชนหลุดตามลำดับการรัน จึงบล็อกที่ชั้น HTTP ไปเลย ไม่ขึ้นกับ env ของใคร
(เทสต์ที่ mock requests เองยัง patch ทับได้ตามปกติ)

**allowlist ไม่ใช่ denylist** (ออดิต 26 ส.ค. 2026 — แก้ 1 ต.ค. 2026): เดิมบล็อกแค่ 4 โฮสต์และดักแค่
requests.Session.request · config/app_runtime.json ตั้ง base เป็นโฮสต์อะไรก็ได้ซึ่งกันชนไม่จับ และ
urllib / httpx / socket ดิบก็ไม่ถูกคุมเลย — ตอนนี้ยอมแค่เครื่องตัวเอง (loopback) ทั้งสองชั้น:
  1. requests.Session.request — ข้อความชัดว่า URL ไหน
  2. socket.connect / connect_ex — ชั้นล่างสุด จับทุก library (urllib, httpx, msal, socket ดิบ)

install() ถูกเรียกจาก run_tests.py, tests/__init__.py และ tests/conftest.py
เพราะแต่ละวิธีรัน (run_tests / unittest discover / pytest) โหลดไฟล์ไม่เหมือนกัน
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

# ชื่อโฮสต์ที่ไม่ออกนอกเครื่อง — testserver = ชื่อปลอมของ starlette TestClient (ไม่เปิด socket)
ALLOWED_HOSTNAMES = frozenset({"localhost", "testserver"})


def is_allowed_host(host: str | None) -> bool:
    h = str(host or "").strip().strip("[]").lower()
    if not h:
        return False
    if h in ALLOWED_HOSTNAMES or h.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _blocked(what: str) -> RuntimeError:
    return RuntimeError(f"เทสต์พยายามออกนอกเครื่อง — ห้ามเด็ดขาด ให้ mock แทน: {what}")


def _install_requests_guard() -> None:
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
        if not is_allowed_host(urlsplit(target).hostname):
            raise _blocked(f"{str(method).upper()} {target}")
        return real_request(self, method, url, *args, **kwargs)

    guarded._alloc_test_guard = True
    Session.request = guarded
    requests.Session.request = guarded


def _address_allowed(address) -> bool:
    if isinstance(address, (str, bytes)):  # AF_UNIX
        return True
    if isinstance(address, tuple) and address:
        return is_allowed_host(address[0])
    return False


def _install_socket_guard() -> None:
    if getattr(socket.socket.connect, "_alloc_test_guard", False):
        return
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(self, address):
        if not _address_allowed(address):
            raise _blocked(f"socket {address!r}")
        return real_connect(self, address)

    def connect_ex(self, address):
        if not _address_allowed(address):
            raise _blocked(f"socket {address!r}")
        return real_connect_ex(self, address)

    connect._alloc_test_guard = True
    connect_ex._alloc_test_guard = True
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex


def install() -> None:
    os.environ.setdefault("TARGETSUN_READ_ENABLED", "0")
    _install_requests_guard()
    _install_socket_guard()
