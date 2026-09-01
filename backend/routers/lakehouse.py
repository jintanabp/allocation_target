from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ..deps import (
    ensure_own_supervisor_write,
    ensure_supervisor_allowed,
    ensure_demo_team_not_sent,
    ensure_targetsun_import_allowed,
    require_admin_user,
    require_authenticated_user,
)
from ..schemas import LakehouseUploadRequest, VerifySendBatchRequest
from ..services.lakehouse import (
    export_allocations_excel,
    upload_allocations_to_lakehouse,
    verify_send_batch,
)
from ..services.targetsun_import import (
    import_allocations_to_targetsun,
    import_prepared_targetsun,
    load_prepare_batch,
    prepare_targetsun_import,
)
from ..services.usage_log_store import log_from_user

router = APIRouter(tags=["lakehouse"])


# รายชื่อที่เก็บลง log ต่อการส่งหนึ่งครั้ง — ทีมปกติมีหลักสิบ ตัวเลขนี้เผื่อไว้มาก
# ทีมที่ชนเพดานจะมีธง emp_ids_truncated ให้รายงานถอยไปใช้ค่าประมาณระดับทีมแทน
_MAX_LOGGED_EMP_IDS = 500


def _log_targetsun_send(user: dict, req: LakehouseUploadRequest, result: Any) -> None:
    """
    บันทึกทุกครั้งที่กดส่ง Target Sun — สำเร็จหรือไม่ก็ตาม
    นี่คือหลักฐานเดียวที่บอกได้ว่าทีมไหนส่งอะไรไปเมื่อไหร่ (snapshot เก็บแค่ครั้งล่าสุดต่องวด)

    ต้องส่ง target_month/target_year เสมอ — เดิมไม่ได้ส่ง ทุกแถว send_targetsun
    จึงไม่มีงวดเป้าติดมาเลย แล้วการกรอง /admin/usage-logs?target_month= กลายเป็น
    กรองตาม "วันที่ของไฟล์ log" (วันที่กด) ไม่ใช่งวดที่ส่ง · ทางเดียวที่เหลือคือ
    ไปแกะข้อความ detail เอา ซึ่งยังต้องคงไว้เป็น fallback ของแถวเก่าที่มีอยู่แล้ว
    """
    try:
        res = result if isinstance(result, dict) else {}
        ts = res.get("targetsun") or {}
        r = ts.get("result") or {}
        ok = ts.get("success") is not False
        # ยอดที่ "ลงจริง" ไม่ตรงไฟล์ = ส่งผ่านแต่ปลายทางกินไม่ครบ ต้องเห็นใน audit
        rb = res.get("readback") or {}
        rb_note = ""
        level = "info" if ok else "error"
        if rb.get("checked") and rb.get("ok") is False:
            rb_note = (
                f" · ⚠ ยอดลงจริงไม่ตรงไฟล์ {rb.get('diff_count', 0)} SKU "
                f"({rb.get('diff_boxes', 0):+,} หีบ)"
            )
            level = "error"
        elif rb.get("checked") is False and ok:
            rb_note = f" · ตรวจยอดหลังส่งไม่ได้ ({rb.get('reason') or '-'})"
        emp_ids = [str(e).strip() for e in (res.get("emp_codes") or []) if str(e).strip()]
        log_from_user(
            user,
            level=level,
            sup_id=req.sup_id,
            action="send_targetsun",
            message=("ส่งเข้า Target Sun สำเร็จ" if ok else "ส่งเข้า Target Sun ไม่สำเร็จ"),
            detail=(
                f"งวด {req.target_year}-{req.target_month:02d} · "
                f"ส่ง {res.get('rows_sent', 0)} แถว · "
                f"เพิ่ม {r.get('inserted', 0)} · แก้ {r.get('updated', 0)} · ข้าม {r.get('skipped', 0)}"
                + ("" if ok else f" · {ts.get('resultMsg') or ''}")
                + rb_note
            ),
            target_month=int(req.target_month),
            target_year=int(req.target_year),
            context={
                "rows_sent": res.get("rows_sent", 0),
                "inserted": r.get("inserted", 0),
                "updated": r.get("updated", 0),
                "skipped": r.get("skipped", 0),
                "emp_count": len(emp_ids),
                "emp_ids": emp_ids[:_MAX_LOGGED_EMP_IDS],
                "emp_ids_truncated": len(emp_ids) > _MAX_LOGGED_EMP_IDS,
                "readback_checked": rb.get("checked"),
                "readback_ok": rb.get("ok"),
                "ok": ok,
            },
        )
    except Exception:  # log ต้องไม่ทำให้การส่งพัง
        pass


@router.post("/lakehouse/export-csv")
def export_lakehouse_csv(
    req: LakehouseUploadRequest,
    user: dict = Depends(require_authenticated_user),
):
    """ดาวน์โหลด Excel (.xlsx) รูปแบบ tga_target_salesman_next (รวม QUANTITYCASE=0)"""
    ensure_supervisor_allowed(user, req.sup_id)
    ensure_own_supervisor_write(user, req.sup_id)
    out = export_allocations_excel(req)
    return Response(
        content=out["content"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{out["filename"]}"',
            "X-Export-Rows": str(out["rows"]),
            "X-Export-Zero-Rows": str(out["zero_rows"]),
            "X-Export-Dropped-Missing-Dims": str(out.get("dropped_missing_dims", 0)),
        },
    )


@router.post("/lakehouse/upload")
def upload_to_lakehouse(
    req: LakehouseUploadRequest,
    user: dict = Depends(require_admin_user),
):
    """
    เขียนไฟล์ผลกระจายขึ้น OneLake — **dev เท่านั้น** และไม่มีปุ่มใน UI

    เตรียมไว้เผื่อเปิด ingest อัตโนมัติในอนาคต แต่เป็นการเขียนขึ้นระบบภายนอกจริง
    เดิมเปิดให้ผู้ใช้ที่ล็อกอินคนไหนก็ได้ที่มีสิทธิ์เขียนทีมตัวเอง โดยไม่ผ่าน
    ด่านสิทธิ์ส่งรายคน (`can_import_targetsun`) ต่างจากเส้นทางส่ง Target Sun จริง
    — จำกัดเป็น dev เพื่อไม่ให้เป็นทางลัดออกนอกระบบ

    หมายเหตุ: ด่านตรวจยอด (S1/S3/S3.5) ทำงานอยู่แล้ว เพราะไฟล์ถูกสร้างผ่าน
    `_build_tga_upload_dataframe` ตัวเดียวกับเส้นทางส่งจริง
    """
    ensure_supervisor_allowed(user, req.sup_id)
    out = upload_allocations_to_lakehouse(req)
    try:
        log_from_user(
            user,
            level="warn",
            sup_id=req.sup_id,
            action="onelake_upload",
            message=f"เขียนไฟล์ผลกระจายขึ้น OneLake ({req.sup_id})",
            detail=f"{out.get('rows')} แถว → {out.get('remote_path')}",
        )
    except Exception:  # log ต้องไม่ทำให้งานหลักพัง
        pass
    return out


@router.post("/lakehouse/prepare-targetsun")
def prepare_targetsun_from_allocations(
    req: LakehouseUploadRequest,
    user: dict = Depends(require_authenticated_user),
):
    """ขั้นที่ 1: สร้าง Excel TGA เก็บชั่วคราว — คืน prepare_token สำหรับขั้นส่ง"""
    ensure_supervisor_allowed(user, req.sup_id)
    ensure_own_supervisor_write(user, req.sup_id)
    ensure_targetsun_import_allowed(user)
    ensure_demo_team_not_sent(req.sup_id)
    return prepare_targetsun_import(req)


@router.get("/lakehouse/send-env")
def get_send_environment(user: dict = Depends(require_authenticated_user)):
    """
    ปลายทางที่จะส่งจริงคือที่ไหน — ให้หน้าจอยืนยันการส่งบอกความจริง

    เดิมกล่องยืนยันเขียนตายตัวว่า "สภาพแวดล้อมทดสอบ (UAT)" ไม่ว่าแอดมินจะสลับ
    ไป Production แล้วหรือยัง ซึ่งเป็นบรรทัดเดียวที่ผู้ใช้ใช้ประเมินว่ากดแล้วกระทบอะไร

    คืนแค่ป้ายชื่อ host ไม่ใช่ URL เต็ม (URL เต็มยังเป็นของ dev ที่ /admin/settings)
    """
    from ..services.targetsun_endpoints import targetsun_endpoints_summary

    s = targetsun_endpoints_summary()
    _ = user
    return {
        "import_host_label": s.get("import_host_label"),
        "read_host_label": s.get("read_host_label"),
        "cross_env": s.get("cross_env") == "1",
    }


@router.post("/lakehouse/verify-send-batch")
def verify_send_batch_before_import(
    req: VerifySendBatchRequest,
    user: dict = Depends(require_authenticated_user),
):
    """
    ขั้นระหว่างเตรียมกับส่ง: ตรวจยอดรวมของไฟล์ที่เตรียมไว้ "ทั้งชุด" ก่อนส่งทีมแรก

    ต้องอยู่ก่อน import เสมอ — ถ้าตรวจหลังส่ง ทีมแรก ๆ ก็เข้า Target Sun ไปแล้ว
    ย้อนไม่ได้ ซึ่งเป็นบั๊กเดิมที่การแยกเตรียม/ส่งออกจากกันตั้งใจแก้
    """
    metas = load_prepare_batch(req.tokens)
    for meta in metas:
        sup_id = str(meta.get("sup_id") or "").strip()
        ensure_supervisor_allowed(user, sup_id)
        ensure_own_supervisor_write(user, sup_id)
        ensure_demo_team_not_sent(sup_id)
    ensure_targetsun_import_allowed(user)
    return verify_send_batch(metas)


@router.post("/lakehouse/import-targetsun")
def import_targetsun_from_allocations(
    req: LakehouseUploadRequest,
    user: dict = Depends(require_authenticated_user),
):
    """
    ส่งเข้า importTargetSalesmanNextFromExcel
    - มี prepare_token: POST ไฟล์ที่เตรียมแล้ว (ขั้นที่ 2)
    - ไม่มี: สร้าง Excel + POST ในคำขอเดียว (เดิม)
    """
    ensure_supervisor_allowed(user, req.sup_id)
    ensure_own_supervisor_write(user, req.sup_id)
    ensure_targetsun_import_allowed(user)
    ensure_demo_team_not_sent(req.sup_id)
    try:
        if (req.prepare_token or "").strip():
            result = import_prepared_targetsun(req)
        else:
            result = import_allocations_to_targetsun(req)
    except Exception as e:
        log_from_user(
            user,
            level="error",
            sup_id=req.sup_id,
            action="send_targetsun",
            message="ส่งเข้า Target Sun ไม่สำเร็จ",
            detail=f"งวด {req.target_year}-{req.target_month:02d} · {type(e).__name__}: {e}",
            target_month=int(req.target_month),
            target_year=int(req.target_year),
            context={"ok": False, "error": type(e).__name__},
        )
        raise
    _log_targetsun_send(user, req, result)
    return result
