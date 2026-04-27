from pydantic import BaseModel, Field


class YellowTargetInput(BaseModel):
    emp_id: str
    yellow_target: float = Field(ge=0)


class LockedEditInput(BaseModel):
    emp_id: str
    sku: str
    locked_boxes: int = Field(ge=0)


class OptimizeRequest(BaseModel):
    yellowTargets: list[YellowTargetInput]
    strategy: str = "L3M"
    force_min_one: bool = False
    new_products_even: bool = False
    locked_edits: list[LockedEditInput] = []
    cap_multiplier: float | None = None  # Custom strategy override (1.5–5.0)


class AllocationRow(BaseModel):
    emp_id: str
    sku: str
    allocated_boxes: int = Field(ge=0)
    hist_avg: float = 0.0
    hist_ly_same_month: float = 0.0
    hist_prev_month: float = 0.0
    price_per_box: float = 0.0
    brand_name_thai: str = ""
    brand_name_english: str = ""
    product_name_thai: str = ""


class ExportRequest(BaseModel):
    allocations: list[AllocationRow]
    brand_filter: str = "ALL"
    yellow_targets: list[YellowTargetInput] = []


class LakehouseUploadRow(BaseModel):
    emp_id: str
    sku: str
    allocated_boxes: int = Field(ge=0)


class LakehouseUploadRequest(BaseModel):
    sup_id: str
    target_month: int = Field(ge=1, le=12)
    target_year: int = Field(ge=2020, le=2100)
    allocations: list[LakehouseUploadRow]

