"""Pruebas del orquestador de cutover PostgreSQL legacy."""

from __future__ import annotations

import json
from pathlib import Path

from coderag.storage import postgres_legacy_cutover


def test_resolve_cutover_report_profile_uses_stage_default_prefix() -> None:
    """Cada perfil operativo debe resolver un prefijo estable por defecto."""
    profile = postgres_legacy_cutover.resolve_cutover_report_profile(
        "observation-exit",
        "",
    )

    assert profile.name == "observation-exit"
    assert profile.report_prefix == "legacy_observation_exit"


def test_run_cutover_marks_ready_when_audit_and_validate_match(monkeypatch) -> None:
    """El cutover automático queda listo cuando migración y validate cierran."""
    monkeypatch.setattr(
        postgres_legacy_cutover,
        "resolve_postgres_dsn",
        lambda settings: "postgresql://user:pass@localhost:5432/db",
    )
    monkeypatch.setattr(
        postgres_legacy_cutover,
        "run_postgres_schema_command",
        lambda settings, operation: {
            "command": operation,
            "current_heads": ["0001_initial_postgres_schema"],
            "expected_heads": ["0001_initial_postgres_schema"],
        },
    )
    monkeypatch.setattr(
        postgres_legacy_cutover,
        "run_legacy_postgres_data_migration",
        lambda settings: {
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
        },
    )
    monkeypatch.setattr(
        postgres_legacy_cutover,
        "_build_health_report",
        lambda health_url, timeout_seconds: {
            "requested": True,
            "ok": True,
            "status_code": 200,
        },
    )

    report = postgres_legacy_cutover.run_legacy_postgres_cutover(
        object(),
        health_url="http://127.0.0.1:8000/health",
        manual_confirmations={
            "backup_validated": True,
            "rollback_approved": True,
            "retain_legacy_tables": True,
        },
    )

    assert report["automatic_cutover_ready"] is True
    assert report["manual_checks_complete"] is True
    assert report["cutover_ready"] is True
    assert report["postgres_target"] == "localhost:5432/db"
    assert len(report["audit_rows"]) == 1
    assert any(item["automatic"] for item in report["checklist"])
    assert any(
        (not item["automatic"]) and item["ok"]
        for item in report["checklist"]
    )
    assert report["observation_exit_automatic_ready"] is True
    assert report["observation_checks_complete"] is False
    assert report["observation_exit_ready"] is False


def test_run_cutover_keeps_manual_checks_pending_by_default(monkeypatch) -> None:
    """Sin confirmaciones explícitas, el cutover completo no debe cerrarse."""
    monkeypatch.setattr(
        postgres_legacy_cutover,
        "resolve_postgres_dsn",
        lambda settings: "postgresql://user:pass@localhost:5432/db",
    )
    monkeypatch.setattr(
        postgres_legacy_cutover,
        "run_postgres_schema_command",
        lambda settings, operation: {
            "command": operation,
            "current_heads": ["0001_initial_postgres_schema"],
            "expected_heads": ["0001_initial_postgres_schema"],
        },
    )
    monkeypatch.setattr(
        postgres_legacy_cutover,
        "run_legacy_postgres_data_migration",
        lambda settings: {
            "legacy_tables_missing": [],
            "audit": {
                "jobs": {
                    "source_count": 1,
                    "target_count_before": 0,
                    "target_count_after": 1,
                    "matched_after": 1,
                    "missing_after": 0,
                }
            },
        },
    )
    monkeypatch.setattr(
        postgres_legacy_cutover,
        "_build_health_report",
        lambda health_url, timeout_seconds: None,
    )

    report = postgres_legacy_cutover.run_legacy_postgres_cutover(object())

    assert report["automatic_cutover_ready"] is True
    assert report["manual_checks_complete"] is False
    assert report["cutover_ready"] is False
    assert report["observation_checks_complete"] is False
    assert report["observation_exit_ready"] is False


def test_run_cutover_marks_observation_exit_ready_when_approved(
    monkeypatch,
) -> None:
    """La salida de observacion queda lista cuando la evidencia y aprobaciones cierran."""
    monkeypatch.setattr(
        postgres_legacy_cutover,
        "resolve_postgres_dsn",
        lambda settings: "postgresql://user:pass@localhost:5432/db",
    )
    monkeypatch.setattr(
        postgres_legacy_cutover,
        "run_postgres_schema_command",
        lambda settings, operation: {
            "command": operation,
            "current_heads": ["0001_initial_postgres_schema"],
            "expected_heads": ["0001_initial_postgres_schema"],
        },
    )
    monkeypatch.setattr(
        postgres_legacy_cutover,
        "run_legacy_postgres_data_migration",
        lambda settings: {
            "legacy_tables_missing": [],
            "audit": {
                "jobs": {
                    "source_count": 2,
                    "target_count_before": 2,
                    "target_count_after": 2,
                    "matched_after": 2,
                    "missing_after": 0,
                }
            },
        },
    )
    monkeypatch.setattr(
        postgres_legacy_cutover,
        "_build_health_report",
        lambda health_url, timeout_seconds: {
            "requested": True,
            "ok": True,
            "status_code": 200,
        },
    )

    report = postgres_legacy_cutover.run_legacy_postgres_cutover(
        object(),
        health_url="http://127.0.0.1:8000/health",
        observation_confirmations={
            "observation_window_complete": True,
            "no_sev1_sev2_incidents": True,
            "representative_reingest_validated": True,
            "no_sustained_legacy_flags": True,
            "legacy_removal_approved": True,
        },
    )

    assert report["observation_exit_automatic_ready"] is True
    assert report["observation_checks_complete"] is True
    assert report["observation_exit_ready"] is True
    assert any(
        item["title"] == "Retiro definitivo del legacy aprobado"
        and item["ok"]
        for item in report["observation_checklist"]
    )


def test_write_cutover_reports_creates_json_and_markdown(tmp_path: Path) -> None:
    """El cutover debe exportar un resumen JSON y checklist Markdown."""
    report = {
        "generated_at": "2026-05-21T22:00:00+00:00",
        "report_profile": "observation-exit",
        "report_prefix": "legacy_observation_exit",
        "postgres_target": "localhost:5432/coderag",
        "automatic_cutover_ready": True,
        "manual_checks_complete": True,
        "cutover_ready": True,
        "observation_exit_automatic_ready": True,
        "observation_checks_complete": True,
        "observation_exit_ready": True,
        "checklist": [
            {
                "title": "Heads Alembic alineados tras la migracion",
                "ok": True,
                "detail": "current=head",
                "automatic": True,
            },
            {
                "title": "Backup validado antes del cambio",
                "ok": True,
                "detail": "manual",
                "automatic": False,
            },
        ],
        "observation_checklist": [
            {
                "title": "Retiro definitivo del legacy aprobado",
                "ok": True,
                "detail": "manual",
                "automatic": False,
            }
        ],
        "audit_rows": [],
    }

    json_path, md_path = postgres_legacy_cutover.write_cutover_reports(
        output_dir=tmp_path,
        report_prefix="cutover_test",
        report=report,
    )

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    md_text = md_path.read_text(encoding="utf-8")

    assert payload["automatic_cutover_ready"] is True
    assert payload["cutover_ready"] is True
    assert "# Checklist de Cutover PostgreSQL legacy" in md_text
    assert "- Report profile: `observation-exit`" in md_text
    assert "- Report prefix: `legacy_observation_exit`" in md_text
    assert "- [x] Heads Alembic alineados tras la migracion" in md_text
    assert "- [x] Backup validado antes del cambio" in md_text
    assert "## Salida de observacion" in md_text
    assert "- [x] Retiro definitivo del legacy aprobado" in md_text