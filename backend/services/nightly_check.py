"""
ตรวจรายคืนว่ามีใครไปแก้เป้าใน Target Sun หลังส่ง (เฟส F3, ผู้ใช้อนุมัติ 29 ก.ย. 2026)

เทียบ Target Sun ตอนนี้กับ sent ledger (F2 — สิ่งที่เราส่งไปจริง) ต่อทีม × งวด แล้วเก็บผลเป็นประวัติรายคืน
ใช้ดูพฤติกรรม "ส่งแล้วไปแก้มือต่อใน Target Sun" ซึ่งเป็นตัวชี้ว่าผลกระจายยังไม่ตรงใจ (ช่วงแรกแค่ดู ไม่ปรับอะไร)

กติกาความปลอดภัย (ห้ามหย่อน):
  - **อ่านอย่างเดียว** ผ่าน Read API เดิม ไม่มีทางเขียน Target Sun จากโมดูลนี้
  - **ค่าตั้งต้นปิด** — ค่าตั้งอยู่ใน data/nightly_check.json (ไม่ใช่ config/ ไม่ใช่ .env) เครื่อง dev ไม่มีไฟล์นี้
    จึงไม่มีทางวิ่งไปอ่าน Prod เอง · เปิดได้จากหน้าแอดมิน (dev เท่านั้น)
  - อ่านกับส่งคนละระบบ (cross_env) = ข้ามทั้งคืน · แหล่งอ่านเป้าไม่ใช่ Target Sun = ข้าม
  - วันที่ 1–14 ตารางเป้างวดถัดไปยังว่าง = ข้าม ไม่ถือว่าผิด · ตารางว่างทั้งทีม = ข้าม
  - ล็อกไฟล์ data/.nightly_check.lock กันรันซ้อน (หลาย worker / กดรันซ้ำ / restart)
  - อ่านทีละทีม หน่วงระหว่างทีม (fetch_target_rows แบ่งชุดละ 200 รหัสอยู่แล้ว)
  - อ่านล้ม → แจ้ง dev ผ่านกล่องแจ้งเตือน แล้วลองใหม่คืนถัดไป

รอบปิดงวด (ผู้ใช้สั่ง 1 ต.ค. 2026): วันสุดท้ายของเดือนรันเพิ่มอีกรอบตอน closing_hour (ค่าตั้งต้น 23 น.)
เพราะรอบตี 2 ของวันนั้นเป็นรอบสุดท้ายที่เห็นงวดถัดไป — แก้หลังตี 2 จะไม่มีวันถูกเห็น พอขึ้นวันที่ 1
ตารางก็ล้างตัวเอง · เปิด/ปิดตาม enabled ตัวเดียวกัน · ผลเก็บแยกไฟล์ <วันที่>_close.json ไม่ทับรอบตี 2
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ..core.atomic_io import _path_lock, atomic_write_json
from . import sent_ledger

logger = logging.getLogger("target_allocation")

_TZ = ZoneInfo("Asia/Bangkok")
DEFAULTS: dict[str, Any] = {"enabled": False, "hour": 2, "closing_hour": 23, "keep_months": 6,
                            "team_delay_sec": 2.0}
_TICK_SEC = 300
_MAX_DIFFS_SAVED = 500
_scheduler_started = False
_scheduler_guard = threading.Lock()


def _settings_path() -> str:
    return os.path.join("data", "nightly_check.json")


def _state_path() -> str:
    return os.path.join("data", "nightly_check_state.json")


def _history_dir() -> str:
    return os.path.join("data", "ts_nightly")


def _read_json(path: str, default: dict) -> dict:
    try:
        with _path_lock(path):
            if not os.path.isfile(path):
                return dict(default)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        return data if isinstance(data, dict) else dict(default)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("อ่าน %s ไม่ได้: %s", path, e)
        return dict(default)


def read_settings() -> dict[str, Any]:
    s = {**DEFAULTS, **_read_json(_settings_path(), {})}
    s["enabled"] = bool(s.get("enabled"))
    s["hour"] = max(0, min(23, int(s.get("hour") or 0)))
    s["closing_hour"] = max(0, min(23, int(s.get("closing_hour") if s.get("closing_hour") is not None
                                           else DEFAULTS["closing_hour"])))
    s["keep_months"] = max(1, min(24, int(s.get("keep_months") or DEFAULTS["keep_months"])))
    s["team_delay_sec"] = max(0.0, min(30.0, float(s.get("team_delay_sec") or 0)))
    return s


def write_settings(*, enabled: bool | None = None, hour: int | None = None,
                   keep_months: int | None = None, closing_hour: int | None = None,
                   updated_by: str = "") -> dict[str, Any]:
    cur = read_settings()
    if enabled is not None:
        cur["enabled"] = bool(enabled)
    if hour is not None:
        if not 0 <= int(hour) <= 23:
            raise ValueError("ชั่วโมงต้องอยู่ 0–23")
        cur["hour"] = int(hour)
    if closing_hour is not None:
        if not 0 <= int(closing_hour) <= 23:
            raise ValueError("ชั่วโมงรอบปิดงวดต้องอยู่ 0–23")
        cur["closing_hour"] = int(closing_hour)
    if keep_months is not None:
        if not 1 <= int(keep_months) <= 24:
            raise ValueError("เก็บประวัติได้ 1–24 เดือน")
        cur["keep_months"] = int(keep_months)
    cur["updated_by"] = updated_by
    cur["updated_at"] = datetime.now(_TZ).isoformat(timespec="seconds")
    os.makedirs("data", exist_ok=True)
    atomic_write_json(_settings_path(), cur, ensure_ascii=False, indent=2)
    return read_settings()


def read_state() -> dict[str, Any]:
    return _read_json(_state_path(), {"last_run": None, "teams": {}})


# ── เทียบ ───────────────────────────────────────────────────────────────


def compare(ledger_rows: dict[str, dict], live_qty_by_key: dict[str, int]) -> dict[str, list]:
    """
    เทียบเฉพาะสินค้าที่เราส่ง × พนักงานที่อยู่ในไฟล์ (สินค้าที่ไม่เคยส่งไม่ใช่เรื่องของเรา)
    changed = คีย์เดียวกันแต่หีบต่าง · missing = เราส่ง > 0 แต่ใน Target Sun ไม่มีแถว
    extra = ใน Target Sun มีแถว > 0 ที่เราไม่เคยส่ง (เช่นมีคนเพิ่มแถวคลังอื่นเข้าไปเอง)
    """
    sent = {k: int((v or {}).get("qty") or 0) for k, v in (ledger_rows or {}).items()}
    skus = {k.split("|")[0] for k in sent}
    emps = {(k.split("|") + [""])[1] for k in sent}
    live = {
        k: int(q) for k, q in (live_qty_by_key or {}).items()
        if k.split("|")[0] in skus and (k.split("|") + [""])[1] in emps
    }
    changed = [{"key": k, "sent": q, "now": live[k]} for k, q in sent.items() if k in live and live[k] != q]
    missing = [{"key": k, "sent": q, "now": None} for k, q in sent.items() if k not in live and q > 0]
    extra = [{"key": k, "sent": None, "now": q} for k, q in live.items() if k not in sent and q > 0]
    return {"changed": sorted(changed, key=lambda d: d["key"]),
            "missing": sorted(missing, key=lambda d: d["key"]),
            "extra": sorted(extra, key=lambda d: d["key"])}


def _live_qty(fetch: Callable, month: int, year: int, emp_codes: list[str]) -> tuple[dict | None, str]:
    from .lakehouse import _live_target_row_key

    try:
        result = fetch(int(year), int(month), emp_codes)
    except Exception as e:  # อ่านล้ม = ตรวจไม่ได้ ไม่ใช่ "ไม่มีแถว"
        return None, f"error: {e}"[:300]
    rows = result.get("rows") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return None, "bad_response"
    if result.get("complete") is False:
        return None, "incomplete_read"
    out: dict[str, int] = {}
    for r in rows:
        if not isinstance(r, dict) or not str(r.get("PRODUCTCODE") or "").strip():
            continue
        k = _live_target_row_key(r)
        try:
            q = int(float(r.get("QUANTITYCASE") or 0))
        except (TypeError, ValueError):
            q = 0
        out[k] = out.get(k, 0) + q
    return out, ""


# ── รันหนึ่งรอบ ─────────────────────────────────────────────────────────


def _try_file_lock(path: str):
    from ..core.runtime_checks import _try_lock

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fh = open(path, "a+")
    if _try_lock(fh):
        return fh
    fh.close()
    return None


def _save_history(sup: str, month: int, year: int, day: str, payload: dict) -> None:
    d = os.path.join(_history_dir(), f"{sup}_{int(year)}_{int(month):02d}")
    os.makedirs(d, exist_ok=True)
    atomic_write_json(os.path.join(d, f"{day}.json"), payload, ensure_ascii=False)


def _prune_history(keep_months: int, today: datetime) -> int:
    d = _history_dir()
    if not os.path.isdir(d):
        return 0
    cutoff = today.year * 12 + today.month - keep_months
    n = 0
    for name in os.listdir(d):
        parts = name.rsplit("_", 2)
        try:
            if len(parts) == 3 and int(parts[1]) * 12 + int(parts[2]) < cutoff:
                shutil.rmtree(os.path.join(d, name), ignore_errors=True)
                n += 1
        except ValueError:
            continue
    return n


def _notify_dev(title: str, message: str, context: dict) -> None:
    try:
        from . import notification_store
        from .send_alerts import _dev_emails
        from .user_access_store import read_rows

        notification_store.create(kind="nightly_check", title=title, message=message,
                                  recipients=_dev_emails(read_rows()), context=context)
    except Exception:
        logger.exception("แจ้งเตือน dev เรื่องตรวจรายคืนไม่สำเร็จ")


def run_once(*, now: datetime | None = None, fetch: Callable | None = None,
             sleep: Callable[[float], None] = time.sleep, force: bool = False,
             closing: bool = False) -> dict[str, Any]:
    """รันหนึ่งรอบ — คืนสรุป · ไม่ raise · force=True ข้ามเงื่อนไข "เปิดอยู่" (ปุ่มรันเดี๋ยวนี้ของ dev)
    closing=True = รอบปิดงวดคืนวันสุดท้ายของเดือน (ไฟล์ประวัติแยก ไม่ทับรอบตี 2)"""
    now = now or datetime.now(_TZ)
    settings = read_settings()
    if not settings["enabled"] and not force:
        return {"skipped": "disabled"}

    from . import targetsun_read as tsr
    from .targetsun_endpoints import targetsun_endpoints_summary

    if fetch is None:
        if str(targetsun_endpoints_summary().get("cross_env") or "") == "1":
            return _finish(now, {"skipped": "cross_env"}, {}, closing)
        if not tsr.is_enabled() or tsr.get_target_read_source() != "targetsun":
            return _finish(now, {"skipped": "read_source_not_targetsun"}, {}, closing)
        fetch = tsr.fetch_target_rows

    lock = _try_file_lock(os.path.join("data", ".nightly_check.lock"))
    if lock is None:
        return {"skipped": "busy"}
    try:
        day = now.strftime("%Y-%m-%d")
        cur = (now.year, now.month)
        teams: dict[str, dict] = {}
        failed: list[str] = []
        for i, item in enumerate(sent_ledger.list_ledgers()):
            sup, m, y = item["sup_id"], item["target_month"], item["target_year"]
            tag = f"{sup}|{y}-{m:02d}"
            if (y, m) <= cur:
                continue  # เลยงวดแล้ว — ตาราง "งวดถัดไป" ไม่มีแถวของงวดนี้แล้ว
            if now.day < 15:
                teams[tag] = {"status": "skipped", "reason": "table_not_ready"}
                continue
            led = sent_ledger.read_ledger(sup, m, y) or {}
            rows = led.get("rows") or {}
            emps = sorted({(k.split("|") + [""])[1] for k in rows} - {""})
            if not emps:
                continue
            if i and settings["team_delay_sec"]:
                sleep(settings["team_delay_sec"])
            live, err = _live_qty(fetch, m, y, emps)
            if live is None:
                teams[tag] = {"status": "error", "reason": err}
                failed.append(tag)
                continue
            if not live:
                teams[tag] = {"status": "skipped", "reason": "empty_table"}
                continue
            diff = compare(rows, live)
            summary = {k: len(v) for k, v in diff.items()}
            summary["boxes_changed"] = int(sum(abs((d["now"] or 0) - (d["sent"] or 0))
                                               for v in diff.values() for d in v))
            teams[tag] = {"status": "ok", **summary}
            _save_history(sup, m, y, f"{day}_close" if closing else day, {
                "sup_id": sup, "target_month": m, "target_year": y, "checked_at": now.isoformat(timespec="seconds"),
                "round": "closing" if closing else "nightly",
                "summary": summary, **{k: v[:_MAX_DIFFS_SAVED] for k, v in diff.items()},
            })
        pruned = _prune_history(settings["keep_months"], now)
        result = {"teams": len(teams), "errors": len(failed), "pruned": pruned}
        if closing:
            result["round"] = "closing"
        if failed:
            _notify_dev(
                "ตรวจ Target Sun รายคืนอ่านไม่สำเร็จ",
                (f"รอบปิดงวดอ่านไม่ได้ {len(failed)} ทีม: {', '.join(failed[:10])} — รอบนี้ลองใหม่ไม่ได้ (ขึ้นวันที่ 1 ตารางล้าง)"
                 if closing else
                 f"อ่านไม่ได้ {len(failed)} ทีม: {', '.join(failed[:10])} — จะลองใหม่คืนถัดไป"),
                {"failed": failed[:50], "date": day},
            )
        return _finish(now, result, teams, closing)
    except Exception as e:
        logger.exception("ตรวจรายคืนล้ม")
        _notify_dev("ตรวจ Target Sun รายคืนล้ม", str(e)[:500], {"date": now.strftime("%Y-%m-%d")})
        return _finish(now, {"error": str(e)[:300]}, {}, closing)
    finally:
        try:
            lock.close()
        except OSError:
            pass


def _finish(now: datetime, result: dict, teams: dict, closing: bool = False) -> dict:
    state = read_state()
    state["last_run"] = now.isoformat(timespec="seconds")
    if closing:
        state["last_closing_date"] = now.strftime("%Y-%m-%d")
    else:
        state["last_run_date"] = now.strftime("%Y-%m-%d")
    state["last_result"] = result
    if teams:
        state["teams"] = {**(state.get("teams") or {}),
                          **{k: {**v, "date": now.strftime("%Y-%m-%d")} for k, v in teams.items()}}
    try:
        os.makedirs("data", exist_ok=True)
        atomic_write_json(_state_path(), state, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("บันทึกสถานะตรวจรายคืนไม่ได้: %s", e)
    return result


def latest_diff(sup_id: str, month: int, year: int) -> dict | None:
    d = os.path.join(_history_dir(), f"{str(sup_id).strip().upper()}_{int(year)}_{int(month):02d}")
    if not os.path.isdir(d):
        return None
    files = sorted(f for f in os.listdir(d) if f.endswith(".json"))
    if not files:
        return None
    return _read_json(os.path.join(d, files[-1]), {})


# ── ตัวตั้งเวลาในแอป ────────────────────────────────────────────────────


def _due(settings: dict, state: dict, now: datetime) -> bool:
    return (settings["enabled"] and now.hour == settings["hour"]
            and state.get("last_run_date") != now.strftime("%Y-%m-%d"))


def _is_last_day_of_month(now: datetime) -> bool:
    return (now + timedelta(days=1)).day == 1


def _closing_due(settings: dict, state: dict, now: datetime) -> bool:
    return (settings["enabled"] and _is_last_day_of_month(now) and now.hour == settings["closing_hour"]
            and state.get("last_closing_date") != now.strftime("%Y-%m-%d"))


def _loop() -> None:
    while True:
        try:
            now = datetime.now(_TZ)
            settings, state = read_settings(), read_state()
            if _due(settings, state, now):
                logger.info("เริ่มตรวจ Target Sun รายคืน: %s", run_once(now=now))
            elif _closing_due(settings, state, now):
                logger.info("เริ่มตรวจ Target Sun รอบปิดงวด: %s", run_once(now=now, closing=True))
        except Exception:
            logger.exception("ตัวตั้งเวลาตรวจรายคืนผิดพลาด")
        time.sleep(_TICK_SEC)


def start_scheduler() -> None:
    """เรียกตอนเปิดแอป — thread เดียวต่อโปรเซส ตื่นทุก 5 นาที ทำงานเฉพาะเมื่อเปิดไว้และถึงเวลา"""
    global _scheduler_started
    with _scheduler_guard:
        if _scheduler_started:
            return
        threading.Thread(target=_loop, name="nightly-check", daemon=True).start()
        _scheduler_started = True
