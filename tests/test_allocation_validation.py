"""Unit tests: ผลรวมหีบต่อ SKU ต้องตรงกับเป้าหัวหน้า"""
import pandas as pd

from main import _validate_allocation_vs_targets


def test_validate_empty():
    assert _validate_allocation_vs_targets(pd.DataFrame(), pd.DataFrame({"sku": ["A"]})) == []


def test_validate_match():
    df_alloc = pd.DataFrame(
        [
            {"sku": "624001", "emp_id": "E1", "allocated_boxes": 4},
            {"sku": "624001", "emp_id": "E2", "allocated_boxes": 6},
        ]
    )
    df_sku = pd.DataFrame([{"sku": "624001", "supervisor_target_boxes": 10}])
    assert _validate_allocation_vs_targets(df_alloc, df_sku) == []


def test_validate_mismatch():
    df_alloc = pd.DataFrame([{"sku": "624001", "emp_id": "E1", "allocated_boxes": 7}])
    df_sku = pd.DataFrame([{"sku": "624001", "supervisor_target_boxes": 10}])
    w = _validate_allocation_vs_targets(df_alloc, df_sku)
    assert len(w) == 1
    assert w[0]["sku"] == "624001"
    assert w[0]["expected_boxes"] == 10
    assert w[0]["allocated_sum"] == 7
