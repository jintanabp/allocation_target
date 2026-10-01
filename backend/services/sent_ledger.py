"""
บันทึก "สิ่งที่ส่งเข้า Target Sun จริง" แบบถาวร ต่อทีม × งวด (เฟส F2, ผู้ใช้อนุมัติ 29 ก.ย. 2026)

ทำไมต้องมี: snapshot ผลกระจายเก็บแค่สถานะล่าสุดบนจอ (แก้ต่อหลังส่งได้) และไฟล์ที่ส่งถูกลบหลัง
14 วัน — จึงตอบไม่ได้ว่า "เราส่งอะไรไปจริง" เพื่อเทียบกับ Target Sun ภายหลัง (เฟส F3 ตรวจรายคืนว่า
มีใครไปแก้ใน Target Sun หลังส่ง)

รูปแบบ: data/sent_ledger/{SUP}_{YYYY}_{MM}.json
  rows  — คีย์เต็มของแถว (sku|emp|salestype|division|area|province|warehouse เหมือน
          import_row_key_series) → {qty, sent_at, token} · ส่งรอบหลังทับเฉพาะคีย์ที่อยู่ในไฟล์รอบนั้น
          (ส่งเฉพาะบางแบรนด์ = คีย์ของแบรนด์อื่นคงค่าจากรอบก่อน) จึงเท่ากับ "สิ่งที่ควรอยู่ใน Target Sun"
  sends — ประวัติการส่งทุกรอบ (ใคร เมื่อไร กี่แถว ผลส่ง)

ห้ามทำให้การส่งพัง — ทุกฟังก์ชันที่ถูกเรียกจากเส้นทางส่งจับ exception เอง
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from ..core.atomic_io import _path_lock, atomic_write_json

logger = logging.getLogger("target_allocation")

_MAX_SENDS_KEPT = 200


def ledger_dir() -> str:
    # ระบบ path เดียวกับไฟล์ที่ส่ง (data/ts_sent, data/ts_prepare) และแคชราย SL ใน core/paths.py
    # — อิงโฟลเดอร์ที่รันแอป (ตรวจความต่างกับ data/ ของ repo ได้ที่ /health runtime)
    return os.path.join("data", "sent_ledger")


def ledger_path(sup_id: str, month: int, year: int) -> str:
    sid = "".join(ch for ch in str(sup_id or "").strip().upper() if ch.isalnum() or ch in "-_")
    return os.path.join(ledger_dir(), f"{sid}_{int(year)}_{int(month):02d}.json")


def _read(path: str) -> dict[str, Any] | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("อ่าน sent ledger ไม่ได้ %s: %s", path, e)
        return None


class LedgerUnreadable(RuntimeError):
    """ไฟล์ ledger มีอยู่แต่อ่านไม่ได้ — ห้ามเขียนทับ (จะลบประวัติการส่งถาวรทั้งไฟล์)"""


def _read_for_update(path: str) -> dict[str, Any] | None:
    """
    อ่านก่อนเขียน (ผลตรวจ 1 ต.ค. 2026 ก1) — แยก "ไม่มีไฟล์" ออกจาก "อ่านไม่ได้"

    เดิมใช้ _read ซึ่งคืน None ทั้งสองแบบ แล้วเริ่ม ledger ใหม่ทับไฟล์เดิม: แค่ antivirus/backup
    เปิดไฟล์ค้างบน Windows ตอนส่ง ประวัติการส่งทุกรอบของทีม×งวดนั้นหายถาวร
      - ไม่มีไฟล์ → None (เริ่มใหม่ได้)
      - OSError ชั่วคราว → ลองใหม่ 3 ครั้ง ยังไม่ได้ → LedgerUnreadable (ไม่เขียน)
      - JSON เสีย → เก็บสำเนาไว้ข้าง ๆ (.corrupt-<เวลา>) แล้วเริ่มใหม่ — ไฟล์เสียแล้วอ่านต่อไม่ได้อยู่ดี
    """
    last: Exception | None = None
    for attempt in range(3):
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise json.JSONDecodeError("ไม่ใช่ object", "", 0)
            return data
        except json.JSONDecodeError as e:
            aside = f"{path}.corrupt-{int(time.time())}"
            try:
                os.replace(path, aside)
            except OSError as e2:
                raise LedgerUnreadable(f"ledger เสียและย้ายออกไม่ได้: {e2}") from e
            logger.error("sent ledger เสีย (%s) — เก็บสำเนาไว้ที่ %s แล้วเริ่มใหม่", e, aside)
            return None
        except OSError as e:
            last = e
            time.sleep(0.2 * (attempt + 1))
    raise LedgerUnreadable(f"อ่าน ledger ไม่ได้: {last}")


def read_ledger(sup_id: str, month: int, year: int) -> dict[str, Any] | None:
    path = ledger_path(sup_id, month, year)
    with _path_lock(path):
        return _read(path)


def _qty_by_key(rows: list[dict]) -> dict[str, int]:
    import pandas as pd

    from .lakehouse import import_row_key_series

    if not rows:
        return {}
    df = pd.DataFrame(rows)
    keys = import_row_key_series(df)
    qty = pd.to_numeric(df["QUANTITYCASE"], errors="coerce").fillna(0).astype(int)
    out: dict[str, int] = {}
    for k, q in zip(keys, qty):
        out[k] = out.get(k, 0) + int(q)
    return out


def record_send(
    sup_id: str,
    month: int,
    year: int,
    rows: list[dict],
    *,
    token: str = "",
    user: str = "",
    send_status: str = "",
    send_batch_id: str | None = None,
    import_url: str = "",
) -> bool:
    """
    บันทึกการส่งหนึ่งรอบ — คืน True ถ้าบันทึกได้ · ไม่ raise

    ส่งไม่สำเร็จ (failed) ไม่บันทึกแถว เพราะไม่มีอะไรลงไป · ส่งแล้วยังยืนยันผลไม่ได้ (unknown)
    บันทึกไว้ เพราะของอาจลงไปแล้วจริง — F3 จะเทียบกับ Target Sun เองว่าลงหรือไม่
    """
    try:
        status = str(send_status or "").strip().lower()
        if status == "failed":
            return False
        by_key = _qty_by_key(rows)
        if not by_key:
            return False
        sid = str(sup_id or "").strip().upper()
        now = time.time()
        path = ledger_path(sid, month, year)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _path_lock(path):
            data = _read_for_update(path) or {
                "sup_id": sid, "target_month": int(month), "target_year": int(year),
                "rows": {}, "sends": [],
            }
            # ปลายทาง (ผลตรวจ 1 ต.ค. 2026 ข2): ledger ใช้เทียบกับ Target Sun ที่อ่านได้ตอนนี้ — แถวที่ส่ง UAT
            # ปนกับ Prod จะฟ้องผิดทุกทีมหลังสลับ preset · ปลายทางเปลี่ยน = เริ่มชุดแถวใหม่ (ประวัติการส่งยังอยู่)
            url = str(import_url or "")
            if url and data.get("import_url") and data.get("import_url") != url:
                logger.warning("sent ledger %s %s-%02d: ปลายทางเปลี่ยน (%s → %s) — เริ่มชุดแถวใหม่",
                               sid, year, month, data.get("import_url"), url)
                data["rows"] = {}
            if url:
                data["import_url"] = url
            # ส่งได้บางส่วน / ยังไม่รู้ผล (ผลตรวจ 1 ต.ค. 2026 ข4) — ไม่รู้ว่าแถวไหนลง จึงจดเป็น "ยังไม่ยืนยัน"
            # ตรวจรายคืนแยกแถวพวกนี้ไว้อีกกลุ่ม ไม่นับเป็น「มีคนแก้หลังส่ง」
            unconfirmed = status in ("partial", "unknown")
            for k, q in by_key.items():
                entry = {"qty": int(q), "sent_at": now, "token": token}
                if unconfirmed:
                    entry["unconfirmed"] = True
                data["rows"][k] = entry
            data["sends"] = (data.get("sends") or [])[-(_MAX_SENDS_KEPT - 1):] + [{
                "token": token, "sent_at": now, "user": user, "rows": len(by_key),
                "boxes": int(sum(by_key.values())), "send_status": status or None,
                "send_batch_id": send_batch_id or None, "import_url": url or None,
            }]
            data["updated_at"] = now
            atomic_write_json(path, data, ensure_ascii=False)
        return True
    except LedgerUnreadable as e:
        logger.error("ไม่บันทึก sent ledger (%s %s-%02d) — ไฟล์เดิมอ่านไม่ได้ ไม่เขียนทับ: %s",
                     sup_id, year, month, e)
        _notify_dev_unrecorded(sup_id, month, year, token, str(e))
        return False
    except Exception:
        logger.exception("บันทึก sent ledger ไม่สำเร็จ (%s %s-%02d)", sup_id, year, month)
        return False


def _notify_dev_unrecorded(sup_id: str, month: int, year: int, token: str, why: str) -> None:
    try:
        from . import notification_store
        from .send_alerts import _dev_emails
        from .user_access_store import read_rows

        notification_store.create(
            kind="sent_ledger",
            title="บันทึกการส่งไม่ได้ (sent ledger)",
            message=f"ทีม {sup_id} งวด {int(month):02d}/{int(year)} — ไฟล์เดิมอ่านไม่ได้ จึงไม่เขียนทับ: {why[:200]}",
            recipients=_dev_emails(read_rows()),
            context={"sup_id": sup_id, "token": token},
        )
    except Exception:
        logger.exception("แจ้ง dev เรื่อง sent ledger ไม่สำเร็จ")


def list_ledgers() -> list[dict[str, Any]]:
    """ทีม × งวดที่มี ledger — [{sup_id, target_month, target_year, path}]"""
    out = []
    d = ledger_dir()
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        parts = name[:-5].rsplit("_", 2)
        if len(parts) != 3:
            continue
        try:
            out.append({"sup_id": parts[0], "target_year": int(parts[1]), "target_month": int(parts[2]),
                        "path": os.path.join(d, name)})
        except ValueError:
            continue
    return out
