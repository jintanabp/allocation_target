"""
เขียนไฟล์แบบ atomic — กัน reader อ่านไฟล์ที่เขียนยังไม่จบ (torn read)

ทำไมต้องมี lock ในนี้ (อย่าถอดออก):
  บน Windows os.replace จะพังเป็น PermissionError [WinError 5] สองกรณี — พิสูจน์แล้วทั้งคู่
  ใน tests/test_atomic_io.py:
    1. writer ชน writer — os.replace สองตัวไปไฟล์เดียวกันพร้อมกัน (8 threads → พัง 7)
    2. writer ชน reader — Windows replace ไฟล์ที่มีคนเปิด handle ค้างไม่ได้
  POSIX ไม่มีปัญหานี้เพราะ rename() atomic จริง

  โมดูลนี้จึงล็อกต่อ path ให้ และ **reader ต้องใช้ read_locked() ด้วย** ไม่งั้นกรณี 2 ยังพังอยู่
  retry เป็นแค่กันเหนียวสำหรับตัวกวนนอกโปรเซส (antivirus / ตัวทำ index ของ Windows)

ข้อสมมติที่แบกอยู่ (สำคัญ):
  atomicity + lock ที่นี่กันได้แค่ "อ่านไฟล์ครึ่งใบ" กับ "replace ชนกัน" ในโปรเซสเดียว
  การกันสองคนแก้ทับกัน (read-modify-write) ยังต้องใช้ lock ของ store นั้น ๆ ครอบเอง
  และทั้งหมดนี้ถูกต้อง *เฉพาะตอนรัน uvicorn 1 worker* — เพิ่ม worker เมื่อไหร่ lock ไร้ผลทันที
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from typing import Any

# ล็อกต่อ path — จำนวนถูกจำกัดโดยธรรมชาติ (ราย SL × งวด × ชนิดไฟล์) จึงไม่โตไม่หยุด
# RLock: กันเผลอ deadlock ถ้าโค้ดใน thread เดิมซ้อน read ใน write callback
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()

_REPLACE_RETRIES = 5
_REPLACE_BACKOFF_SEC = 0.02


def _path_lock(path: str) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(path))
    with _LOCKS_GUARD:
        lk = _LOCKS.get(key)
        if lk is None:
            lk = threading.RLock()
            _LOCKS[key] = lk
        return lk


@contextlib.contextmanager
def read_locked(path: str):
    """
    อ่านไฟล์ที่อาจถูกเขียนพร้อมกัน — ครอบด้วยตัวนี้เสมอ

        with read_locked(p):
            df = pd.read_csv(p)

    บน Windows ถ้า reader ถือ handle ไว้ตอน writer เรียก os.replace → writer พัง PermissionError
    lock ตัวนี้ทำให้ reader กับ writer ของ path เดียวกันไม่ทับเวลากัน
    """
    with _path_lock(path):
        yield


def _replace_with_retry(tmp: str, path: str) -> None:
    """
    เผื่อ PermissionError ที่ lock ในโปรเซสกันไม่ได้ — เช่น antivirus/ตัวทำ index ของ Windows
    เปิดไฟล์ค้างชั่วขณะ หรือมี reader ถือ handle อยู่พอดี
    """
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_BACKOFF_SEC * (2**attempt))


def _atomic_replace(path: str, write_fn) -> None:
    """
    เขียนผ่านไฟล์ temp ในโฟลเดอร์เดียวกันแล้ว os.replace
    (temp ต้องอยู่โฟลเดอร์เดียวกัน ไม่งั้น os.replace ข้าม volume ไม่ atomic)
    """
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    with _path_lock(path):
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        os.close(fd)
        try:
            write_fn(tmp)
            _replace_with_retry(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def atomic_write_bytes(path: str, data: bytes) -> None:
    def _w(tmp: str) -> None:
        with open(tmp, "wb") as f:
            f.write(data)

    _atomic_replace(path, _w)


def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    def _w(tmp: str) -> None:
        with open(tmp, "w", encoding=encoding, newline="") as f:
            f.write(text)

    _atomic_replace(path, _w)


def atomic_write_json(
    path: str,
    obj: Any,
    *,
    indent: int | None = None,
    ensure_ascii: bool = False,
) -> None:
    def _w(tmp: str) -> None:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=ensure_ascii, indent=indent)

    _atomic_replace(path, _w)


def atomic_write_csv(path: str, df, **to_csv_kwargs) -> None:
    """df.to_csv แบบ atomic — ใช้แทน df.to_csv(path) ทุกที่ที่มีคนอ่านไฟล์นั้นพร้อมกันได้"""
    to_csv_kwargs.setdefault("index", False)

    def _w(tmp: str) -> None:
        df.to_csv(tmp, **to_csv_kwargs)

    _atomic_replace(path, _w)
