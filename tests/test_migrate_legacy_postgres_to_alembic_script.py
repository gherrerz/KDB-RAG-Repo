"""Pruebas del wrapper exportable para auditoría de cutover legacy."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


_SCRIPT_PATH = Path("scripts/migrate_legacy_postgres_to_alembic.py")
_SPEC = importlib.util.spec_from_file_location(
    "migrate_legacy_postgres_to_alembic_script",
    _SCRIPT_PATH,
)
assert _SPEC is not None
assert _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


def test_audit_rows_marks_cutover_ready_when_counts_match() -> None:
    """La fila plana debe señalar ready cuando no quedan faltantes."""
    report = {
        "legacy_tables_missing": [],
        "audit": {
            "jobs": {
                "source_count": 2,
                "target_count_before": 0,
                "target_count_after": 2,
                "matched_after": 2,
                "missing_after": 0,
            }
        },
    }

    rows = _MODULE._audit_rows(report)

    assert len(rows) == 1
    assert rows[0].table_name == "jobs"
    assert rows[0].cutover_ready is True
    assert _MODULE._is_cutover_ready(report, rows) is True


def test_write_reports_creates_json_and_csv(tmp_path: Path) -> None:
    """El wrapper debe exportar artefactos JSON/CSV legibles por tabla."""
    report = {
        "legacy_tables_missing": [],
        "audit": {
            "jobs": {
                "source_count": 1,
                "target_count_before": 0,
                "target_count_after": 1,
                "matched_after": 1,
                "missing_after": 0,
            },
            "repos": {
                "source_count": 1,
                "target_count_before": 0,
                "target_count_after": 1,
                "matched_after": 1,
                "missing_after": 0,
            },
        },
    }

    json_path, csv_path = _MODULE.write_reports(
        output_dir=tmp_path,
        report_prefix="legacy_cutover_test",
        postgres_target="localhost:5432/coderag",
        report=report,
    )

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    csv_text = csv_path.read_text(encoding="utf-8")

    assert payload["meta"]["postgres_target"] == "localhost:5432/coderag"
    assert payload["meta"]["cutover_ready"] is True
    assert len(payload["rows"]) == 2
    assert "table_name,source_count,target_count_before" in csv_text
