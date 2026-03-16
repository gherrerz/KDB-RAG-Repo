"""Unit tests for inventory diagnostics helper payload construction."""

from coderag.api.query_diagnostics import (
    build_inventory_diagnostics,
    build_inventory_missing_target_diagnostics,
)


def test_build_inventory_missing_target_diagnostics_shape() -> None:
    """Return the expected fallback diagnostics when target is missing."""
    diagnostics = build_inventory_missing_target_diagnostics(
        explain_inventory=True,
        module_name_raw="core",
        module_name="coderag/core",
        budget_seconds=9.0,
        stage_timings={"parse_ms": 1.2},
    )

    assert diagnostics["inventory_target"] is None
    assert diagnostics["inventory_terms"] == []
    assert diagnostics["inventory_count"] == 0
    assert diagnostics["inventory_explain"] is True
    assert diagnostics["module_name_raw"] == "core"
    assert diagnostics["module_name_resolved"] == "coderag/core"
    assert diagnostics["query_budget_seconds"] == 9.0
    assert diagnostics["budget_exhausted"] is False
    assert diagnostics["fallback_reason"] == "inventory_target_missing"


def test_build_inventory_diagnostics_shape() -> None:
    """Return full diagnostics payload for structured inventory responses."""
    diagnostics = build_inventory_diagnostics(
        inventory_target="controller",
        inventory_terms=["controller", "controlador"],
        inventory_count=8,
        explain_inventory=False,
        inventory_purpose_count=0,
        module_name_raw="mall-admin",
        module_name="mall-admin",
        budget_seconds=12.0,
        budget_exhausted=True,
        stage_timings={"total_ms": 44.0},
        fallback_reason="time_budget_exhausted",
    )

    assert diagnostics["inventory_target"] == "controller"
    assert diagnostics["inventory_terms"] == ["controller", "controlador"]
    assert diagnostics["inventory_count"] == 8
    assert diagnostics["inventory_explain"] is False
    assert diagnostics["inventory_purpose_count"] == 0
    assert diagnostics["module_name_raw"] == "mall-admin"
    assert diagnostics["module_name_resolved"] == "mall-admin"
    assert diagnostics["query_budget_seconds"] == 12.0
    assert diagnostics["budget_exhausted"] is True
    assert diagnostics["stage_timings_ms"] == {"total_ms": 44.0}
    assert diagnostics["fallback_reason"] == "time_budget_exhausted"
