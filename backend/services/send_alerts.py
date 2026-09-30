"""
แจ้งเตือนเมื่อการส่ง Target Sun ผิดปกติ — ตัดสินว่าอะไรต้องแจ้ง และแจ้งใคร

กติกา (ผู้ใช้ตัดสิน 29 ก.ย. 2026):
  จำนวนแถวใน Target Sun หลังส่ง = ก่อนส่ง + คู่ใหม่ในไฟล์ — ต่างแม้แถวเดียว ต้องแจ้ง
  ตรวจไม่ได้ (อ่านก่อน/หลังไม่ได้, อ่านกับส่งคนละระบบ, อ่านไม่ครบ) ก็ต้องแจ้งเหมือนกัน
  ไม่ใช่ปล่อยผ่านเงียบ ๆ

ผู้รับ: คนที่กดส่ง + เจ้าของ SL (ส่งรวมภาค = เจ้าของทุก SL ในรอบ) + dev ทุกคน
       + แอดมินที่ขอบเขตครอบทีมนั้น

ทุกอย่างในนี้ห้ามทำให้การส่งที่สำเร็จแล้วกลายเป็นล้มเหลว — ผิดพลาดตรงไหน log แล้วไปต่อ
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from . import notification_store
from .access_control import (
    ROLE_ADMIN,
    ROLE_DEV,
    ROLE_HEAD_ADMIN,
    admin_scope_for_email,
    parse_allocation_admin_emails,
)
from .user_access_store import normalized_email, read_rows

logger = logging.getLogger("target_allocation")

#: เหตุผลที่ตรวจจำนวนแถวไม่ได้ → ข้อความไทย
_UNCHECKED_REASONS = {
    "before_unavailable": "อ่านจำนวนแถวก่อนส่งไม่ได้",
    "after_unavailable": "อ่านจำนวนแถวหลังส่งไม่ได้",
    "cross_env": "ระบบอ่านกับระบบที่ส่งเป็นคนละที่ (เช่นอ่าน Prod ส่ง UAT) ผลนับจึงใช้ไม่ได้",
    "incomplete_read": "อ่านข้อมูลจาก Target Sun ได้ไม่ครบ",
    "error": "ตรวจจำนวนแถวไม่สำเร็จ",
}


def row_count_alert(row_count: dict | None) -> dict | None:
    """
    ผลนับแถวนี้ต้องแจ้งไหม — คืน {"status", "text"} หรือ None ถ้าปกติ

    status: "mismatch" = นับได้แต่ไม่ตรง · "unverified" = ตรวจไม่ได้
    """
    rc = row_count or {}
    if rc.get("checked"):
        if rc.get("ok") is False:
            extra = int(rc.get("unexpected_extra_rows") or 0)
            parallel = int(rc.get("parallel_rows_count") or 0)
            text = (
                f"ก่อนส่ง {rc.get('before_count')} แถว · หลังส่ง {rc.get('after_count')} แถว · "
                f"คาดว่าจะเพิ่ม {rc.get('expected_new_rows')} แถว · "
                + (f"{'เกิน' if extra > 0 else 'ขาด'} {abs(extra)} แถว" if extra else "จำนวนแถวตรง")
            )
            if parallel:
                ex = (rc.get("parallel_rows_sample") or [{}])[0]
                text += (
                    f" · แถวใหม่ซ้อนคู่เดิม {parallel} แถว (คลังไม่ตรงแถวเดิม เป้าอาจเบิ้ล)"
                    + (f" เช่น {ex.get('emp_id')} × {ex.get('sku')} คลังเดิม "
                       f"{'/'.join(w or 'ว่าง' for w in ex.get('old_warehouses') or [])} "
                       f"→ ส่งไป {ex.get('new_warehouse') or 'ว่าง'}" if ex else "")
                )
            return {"status": "mismatch", "text": text}
        return None
    reason = str(rc.get("reason") or "")
    if reason == "send_failed":
        return None  # ส่งไม่สำเร็จ = ไม่มีอะไรลงไป ผู้ส่งเห็นข้อผิดพลาดบนจอแล้ว
    return {"status": "unverified", "text": _UNCHECKED_REASONS.get(reason, "ตรวจจำนวนแถวไม่ได้")}


def _dev_emails(rows: list[dict[str, Any]]) -> set[str]:
    out = set(parse_allocation_admin_emails())
    out |= {normalized_email(r.get("email")) for r in rows if str(r.get("role") or "") == ROLE_DEV}
    return {e for e in out if "@" in e}


def _owner_emails(rows: list[dict[str, Any]], sup_ids: set[str]) -> set[str]:
    return {
        normalized_email(r.get("email"))
        for r in rows
        if str(r.get("login_kind") or "") == "supervisor_acc"
        and str(r.get("userpl") or "").strip().upper() in sup_ids
    }


def _scoped_admin_emails(rows: list[dict[str, Any]], sup_ids: set[str]) -> set[str]:
    out: set[str] = set()
    admins = {
        normalized_email(r.get("email"))
        for r in rows
        if str(r.get("role") or "") in (ROLE_ADMIN, ROLE_HEAD_ADMIN)
    }
    for em in admins:
        try:
            codes = admin_scope_for_email(em).get("sl_codes") or set()
        except Exception as e:  # ขอบเขตคำนวณไม่ได้ = ไม่ส่งให้คนนี้ ดีกว่าล้มทั้งชุด
            logger.warning("หาขอบเขตแอดมิน %s ไม่ได้: %s", em, e)
            continue
        if codes & sup_ids:
            out.add(em)
    return out


def recipients_for(sender_email: str, sup_ids: Iterable[str]) -> list[str]:
    sids = {str(s or "").strip().upper() for s in sup_ids if str(s or "").strip()}
    rows = read_rows()
    out = {normalized_email(sender_email)}
    out |= _owner_emails(rows, sids)
    out |= _dev_emails(rows)
    out |= _scoped_admin_emails(rows, sids)
    return sorted(e for e in out if "@" in e)


def notify_row_count_issue(
    *,
    user: dict,
    sup_id: str,
    target_month: int,
    target_year: int,
    row_count: dict | None,
    batch_id: str | None = None,
    batch_sup_ids: Iterable[str] | None = None,
) -> dict | None:
    """ถ้าผลนับแถวผิดปกติ สร้างแจ้งเตือนหนึ่งรายการให้ทุกคนที่ต้องรู้ — ไม่ raise"""
    try:
        alert = row_count_alert(row_count)
        if alert is None:
            return None
        sid = str(sup_id or "").strip().upper()
        sender = normalized_email(user.get("email") or user.get("view_as_email"))
        acting = normalized_email(user.get("acting_admin_email"))
        teams = {sid} | {str(s).strip().upper() for s in (batch_sup_ids or []) if str(s).strip()}
        recipients = recipients_for(sender, teams)
        if acting:
            recipients = sorted(set(recipients) | {acting})
        who = sender or "-"
        if acting and acting != sender:
            who = f"{acting} (ดูแทน {sender or '-'})"
        head = "จำนวนแถวใน Target Sun ไม่ตรงที่คาด" if alert["status"] == "mismatch" else "ตรวจจำนวนแถวใน Target Sun ไม่ได้"
        batch_note = f" · ส่งรวม {len(teams)} ทีม" if len(teams) > 1 else ""
        return notification_store.create(
            kind=f"send_row_count_{alert['status']}",
            title=f"{head} — ทีม {sid} งวด {int(target_month):02d}/{int(target_year)}",
            message=f"{alert['text']} · ผู้ส่ง {who}{batch_note}",
            recipients=recipients,
            sup_id=sid,
            created_by=acting or sender,
            context={
                "target_month": int(target_month),
                "target_year": int(target_year),
                "row_count": dict(row_count or {}),
                "send_batch_id": batch_id or None,
                "batch_sup_ids": sorted(teams),
                "sender_email": sender or None,
                "acting_admin_email": acting or None,
            },
        )
    except Exception:
        logger.exception("สร้างแจ้งเตือนผลนับแถวไม่สำเร็จ (%s)", sup_id)
        return None
