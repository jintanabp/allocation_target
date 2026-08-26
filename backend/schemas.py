import re

from pydantic import BaseModel, Field, field_validator

from .core.constants import VALID_STRATEGIES

_STRATEGY_PATTERN = "^(" + "|".join(map(re.escape, VALID_STRATEGIES)) + ")$"


class YellowTargetInput(BaseModel):
    emp_id: str
    yellow_target: float = Field(ge=0)
    warehouse_code: str | None = None
    """
    ทีมเจ้าของแถว — โหมดรวมภาคส่งพนักงานหลายทีมมาใน request เดียวและ emp_id ซ้ำ
    ข้ามทีมได้ (I7) ด่าน "ไม่ต้องตั้งเป้า" จึงต้องรู้ทีมถึงจะกันได้ตรงคน
    ไม่ส่งมาก็ได้ (หน้าเว็บรุ่นเก่าที่ค้างในเบราว์เซอร์) แล้วด่านจะตกไปใช้ชุดรวมทุกทีมแทน
    """
    supervisor_code: str | None = None


class LockedEditInput(BaseModel):
    emp_id: str
    sku: str
    locked_boxes: int = Field(ge=0)
    warehouse_code: str | None = None
    """
    ทีมเจ้าของล็อก — โหมดรวมภาคส่งมาด้วยเพราะ emp_id ซ้ำข้ามทีมได้
    backend ไม่ได้ใช้ตัดสินใจ (แต่ละ call ผูกกับ sup_id เดียวอยู่แล้ว)
    แต่ประกาศไว้ให้ payload อ่านรู้เรื่องและ log ตามรอยได้
    """
    supervisor_code: str | None = None


class OptimizeRequest(BaseModel):
    yellowTargets: list[YellowTargetInput]
    strategy: str = Field(default="L3M", pattern=_STRATEGY_PATTERN)
    force_min_one: bool = False
    new_products_even: bool = False
    locked_edits: list[LockedEditInput] = []
    cap_multiplier: float | None = None  # Custom strategy override (1.5-5.0)
    """0–1 น้ำหนักยึด baseline ประวัติใน LP (default เน้นประวัติ; รั้ว ±20% เป็นตัวจำกัดหลัก)"""
    hist_balance: float = Field(default=0.85, ge=0.0, le=1.0)
    """ยอมให้มูลค่ารวมต่อคนคลาดเป้าเงินได้ไม่เกินกี่บาท (soft penalty ใน LP)"""
    revenue_tolerance_baht: float = Field(default=1000.0, ge=0.0)
    tiered_allocation: bool = True
    """SKU หลัก (~80% มูลค่าเป้าหีบ) ปรับเงินได้ · SKU รองยึดประวัติแน่น"""
    tier_pct: float = Field(default=0.80, ge=0.5, le=0.95)
    # Multi-strategy support
    brand_strategy_map: dict[str, str] = Field(default_factory=dict)
    bui_deductions: dict[str, float] = Field(default_factory=dict)
    neg_growth_reason: str | None = None
    """
    ทีมอื่นในหน่วย+ภาคเดียวกันที่พนักงานใน yellowTargets มาจาก

    ใช้ตอน "กระจายรวมทั้งหน่วย": บางงวดเป้าเข้ามาใต้ซุปคนเดียว แต่ต้องเกลี่ยให้พนักงาน
    ทุกทีมในหน่วยนั้น พนักงานทีมอื่นจึงต้องอ่านประวัติขายจาก cache ของทีมตัวเอง
    ไม่งั้นจะถูกมองว่า "ไม่มีประวัติ" แล้วได้หีบน้อยผิดปกติ
    """
    peer_sup_ids: list[str] = Field(default_factory=list)
    """
    ทีมที่ต้องเอา "เป้าหีบ" มาบวกรวมเป็นก้อนเดียวก่อนกระจาย

    ว่าง / มีรหัสเดียว = ใช้เป้าของ sup_id ตามปกติ (พฤติกรรมเดิม)
    มีหลายรหัส = โหมดรวมเป้าทั้งภาค — เป้าต่อ SKU คือผลบวกของทุกทีมในลิสต์
    แล้วประตู I1 จะบังคับให้ผลรวมทั้งก้อนตรงเป้ารวมนั้น

    แยกจาก peer_sup_ids โดยตั้งใจ: peer_sup_ids บอกว่า "ไปอ่านประวัติขายจากทีมไหนบ้าง"
    ซึ่งไม่แตะตัวเลขเป้า ส่วนตัวนี้เปลี่ยนเป้าที่ทั้งระบบยึด — ต้องตรวจสิทธิ์ทีละรหัส
    """
    target_sup_ids: list[str] = Field(default_factory=list)
    """
    กระจายเฉพาะ SKU ในรายการนี้ (ว่าง = ทุก SKU ที่มีเป้า — พฤติกรรมเดิม)

    ใช้กับปุ่ม "กระจายเฉพาะสินค้าที่เป้าเพิ่ม/เปลี่ยน": ฝั่งเว็บส่งเฉพาะ SKU ที่เป้า
    เพิ่งเปลี่ยนมา แล้ว merge ผลกลับเข้าตารางเดิม — SKU อื่นในตารางไม่ถูกแตะ
    ประตู I1 ยังบังคับให้ทุก SKU ที่กระจายรอบนี้ตรงเป้าเป๊ะเหมือนเดิม
    """
    only_skus: list[str] = Field(default_factory=list)

    @field_validator("strategy", mode="before")
    @classmethod
    def _normalize_strategy(cls, v: object) -> str:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "L3M"
        return str(v).strip().upper()

    @field_validator("brand_strategy_map", mode="before")
    @classmethod
    def _normalize_brand_map(cls, v: object) -> dict[str, str]:
        if not v or not isinstance(v, dict):
            return {}
        out: dict[str, str] = {}
        for k, val in v.items():
            ks = str(k).strip()
            vs = str(val).strip().upper()
            if ks and vs:
                out[ks] = vs
        return out


class AllocationRow(BaseModel):
    emp_id: str
    sku: str
    allocated_boxes: int = Field(ge=0)
    warehouse_code: str = ""
    hist_avg: float = 0.0
    hist_ly_same_month: float = 0.0
    hist_prev_month: float = 0.0
    price_per_box: float = 0.0
    brand_name_thai: str = ""
    brand_name_english: str = ""
    product_name_thai: str = ""
    baseline_boxes: int = Field(default=0, ge=0)
    hist_dev_pct: float | None = None
    hist_dev_status: str = ""


class ExportRequest(BaseModel):
    allocations: list[AllocationRow]
    brand_filter: str = "ALL"
    yellow_targets: list[YellowTargetInput] = []
    # ทีมที่อยู่ในผลกระจายก้อนนี้ — โหมดรวมภาคมีพนักงานของหลายทีมในไฟล์เดียว
    # หัว Excel ต้องบอกให้รู้ ไม่งั้นอ่านแล้วนึกว่าเป็นเป้าของทีมเดียว
    scope_sup_ids: list[str] = []


class LakehouseUploadRow(BaseModel):
    emp_id: str
    sku: str
    allocated_boxes: int = Field(ge=0)
    warehouse_code: str | None = None
    # optional — ให้ครบฟิลด์ในอนาคตเมื่อ UI มี grain จาก TGA (ปัจจุบันระบบเติมจาก cache / Fabric)
    salestype: str | None = None
    divisioncode: str | None = None
    areacode: str | None = None
    provincecode: str | None = None


class VerifySendBatchRequest(BaseModel):
    """ตรวจยอดรวมของไฟล์ที่เตรียมไว้ทั้งชุดก่อนกดส่งจริง"""

    tokens: list[str] = Field(default_factory=list)


class LakehouseUploadRequest(BaseModel):
    sup_id: str
    target_month: int = Field(ge=1, le=12)
    target_year: int = Field(ge=2020, le=2100)
    allocations: list[LakehouseUploadRow] = Field(default_factory=list)
    upload_user_code: str | None = None
    brand_filter: str = "ALL"
    """
    ส่งเฉพาะ SKU ในรายการนี้ (ว่าง = ทุก SKU ตาม brand_filter ปกติ)

    ใช้ตอน "ส่งเฉพาะผลกระจายใหม่" หลังกระจายเพิ่มเฉพาะสินค้าที่เป้าเพิ่งเปลี่ยน —
    SKU นอกรายการไม่ถูกแตะใน Target Sun (ของเดิมคงอยู่) พฤติกรรมประตูตรวจเหมือน
    การส่งเฉพาะแบรนด์: S1 ตรวจความเท่าเป้าเฉพาะ SKU ที่อยู่ใน payload
    """
    sku_filter: list[str] = Field(default_factory=list)
    """จาก POST /lakehouse/prepare-targetsun — ส่ง import โดยไม่สร้าง Excel ซ้ำ"""
    prepare_token: str | None = None
    """
    ผู้ใช้ยืนยันแล้วว่ายอดหีบต่อ SKU ไม่ตรงเป้าทีมโดยตั้งใจ (เช่นย้ายข้ามทีมในโหมดรวมภาค)

    ค่าเริ่มต้น False = server ปฏิเสธด้วย 409 พร้อมรายการ SKU ที่ไม่ตรง
    frontend เอารายการนั้นไปแสดงให้ตรวจ แล้วส่งซ้ำด้วย true เมื่อผู้ใช้กดยืนยัน
    """
    confirm_target_mismatch: bool = False
    """
    ผู้ใช้รับทราบแล้วว่าบางคู่พนักงาน×สินค้าไม่มีใน Target Sun งวดนี้ → หีบเหล่านั้นจะไม่ถูกส่ง
    และผู้ใช้จะไปเพิ่มจำนวนเองใน Target Sun

    **คนละ flag กับ confirm_target_mismatch โดยตั้งใจ** — อันนั้นแปลว่า "แก้มือแล้วไม่ตรงเป้า"
    ซึ่งในโหมดรวมภาคเป็นเรื่องปกติตาม I7 คนจึงกดยืนยันจนชิน ถ้าใช้ flag เดียวกัน
    ปัญหา master data จะถูกกดข้ามไปโดยไม่ได้อ่าน
    """
    confirm_manual_topup: bool = False
    """
    ผู้ใช้ยืนยันส่งทั้งที่ระบบ "ไม่มีไฟล์เป้าให้ตรวจ" (เช่นไฟล์เป้าถูกล้างตามอายุ cache
    แล้วเปิด snapshot เก่ามาส่ง) — ปกติต้องกลับไปโหลดขั้นที่ 1 ใหม่ก่อน

    **คนละ flag กับอีกสองตัวโดยตั้งใจ** — สองตัวบนแปลว่า "ตรวจแล้วไม่ตรง แต่ตั้งใจ"
    ตัวนี้แปลว่า "ตรวจไม่ได้เลย" ซึ่งเป็นความเสี่ยงคนละแบบ ถ้าใช้ flag ร่วมกัน
    การกดยืนยันเรื่องหนึ่งจะปลดล็อกอีกเรื่องที่ผู้ใช้ไม่เคยเห็น
    """
    confirm_unverifiable_target: bool = False
    """
    SKU ที่ต้องไม่ส่งสำหรับทีมนี้ แม้ทีมนี้จะส่งได้ครบ

    ใช้ตอนส่งรวมภาค: ถ้าทีมหนึ่งส่ง SKU นั้นไม่ได้ (ไม่มีแถวใน Target Sun) แต่ทีมอื่นส่งได้
    การส่งเฉพาะบางทีมจะทำให้เป้าของ SKU นั้นทั้งภาคครึ่ง ๆ กลาง ๆ เพราะหีบถูกเกลี่ย
    ข้ามทีมมาแล้ว — ด่าน verify-send-batch จึงสั่งให้ตัดชุดเดียวกันทุกทีม
    """
    exclude_skus: list[str] = Field(default_factory=list)
    """
    ผู้ใช้รับทราบว่าเป้าใน Target Sun เปลี่ยนไปหลังจากโหลดข้อมูลขั้นที่ 1 แล้วยังจะส่ง
    ตามแผนเดิม — คนละเรื่องกับ flag อื่น ตัวนี้แปลว่า "ข้อมูลอ้างอิงเก่าไปแล้ว"
    """
    confirm_stale_target: bool = False
    """
    ยอมให้สร้าง "แถวเป้าใหม่" ใน Target Sun สำหรับคู่พนักงาน×สินค้าที่ยังไม่เคยมี
    โดยเติมเขต/พื้นที่จากแถวอื่นของพนักงานคนเดียวกัน

    Target Sun รองรับ insert อยู่แล้ว (targetsun-importTargetSalesmanNextFromExcel.md)
    การตัดแถวทิ้งทำให้หีบที่กระจายไปแล้วหายจากเป้าจริง แล้วผู้ใช้ต้องไปนั่งเพิ่มเอง
    ทีละแถว — หน้าเว็บจึงเปิด flag นี้ทุกกรณีตั้งแต่ 26 ส.ค. 2026
    (เดิมเปิดเฉพาะโหมดรวมทั้งหน่วย แต่เคสเดียวกันเกิดกับทีมเดียวด้วย: สินค้าใหม่
    หรือสินค้าที่พนักงานคนนั้นยังไม่เคยมีเป้าในงวดนั้น)

    ค่าเริ่มต้นยังปิดไว้ เพราะผู้เรียกอื่น (สคริปต์/เทส) ไม่ควรสร้างแถวใหม่โดยไม่ตั้งใจ
    ตัวกันความผิดพลาดอยู่ที่ emp_dims_from_own_grain — เติม dim จากแถวอื่นของพนักงาน
    คนเดียวกันเท่านั้น และเติมเฉพาะเมื่อทุกแถวของคนนั้นตรงกันหมด ถ้าขัดกันเอง
    (ขายหลายเขต) หรือไม่มีแถวใดเลย จะยังถูกตัดตามนโยบายเดิม
    """
    allow_new_targetsun_rows: bool = False
