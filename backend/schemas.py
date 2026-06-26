import re

from pydantic import BaseModel, Field, field_validator

from .core.constants import VALID_STRATEGIES

_STRATEGY_PATTERN = "^(" + "|".join(map(re.escape, VALID_STRATEGIES)) + ")$"


class YellowTargetInput(BaseModel):
    emp_id: str
    yellow_target: float = Field(ge=0)
    warehouse_code: str | None = None


class LockedEditInput(BaseModel):
    emp_id: str
    sku: str
    locked_boxes: int = Field(ge=0)
    warehouse_code: str | None = None


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


class LakehouseUploadRequest(BaseModel):
    sup_id: str
    target_month: int = Field(ge=1, le=12)
    target_year: int = Field(ge=2020, le=2100)
    allocations: list[LakehouseUploadRow] = Field(default_factory=list)
    upload_user_code: str | None = None
    """จาก POST /lakehouse/prepare-targetsun — ส่ง import โดยไม่สร้าง Excel ซ้ำ"""
    prepare_token: str | None = None
