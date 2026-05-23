"""Orquestación de cutover para migración PostgreSQL legacy -> Alembic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from coderag.core.settings import resolve_postgres_dsn
from coderag.storage.postgres_legacy_migration import (
    run_legacy_postgres_data_migration,
)
from coderag.storage.postgres_schema_admin import run_postgres_schema_command


@dataclass(frozen=True)
class AuditRow:
    """Fila plana para exportar auditoría de cutover por tabla."""

    table_name: str
    source_count: int
    target_count_before: int
    target_count_after: int
    matched_after: int
    missing_after: int
    cutover_ready: bool


@dataclass(frozen=True)
class ChecklistItem:
    """Elemento del checklist de cutover exportable a Markdown."""

    title: str
    ok: bool
    detail: str
    automatic: bool


@dataclass(frozen=True)
class ManualCutoverConfirmations:
    """Confirmaciones manuales requeridas para cerrar la ventana de cutover."""

    backup_validated: bool = False
    rollback_approved: bool = False
    retain_legacy_tables: bool = False


@dataclass(frozen=True)
class ObservationExitConfirmations:
    """Confirmaciones manuales requeridas para aprobar la salida de observación."""

    observation_window_complete: bool = False
    no_sev1_sev2_incidents: bool = False
    representative_reingest_validated: bool = False
    no_sustained_legacy_flags: bool = False
    legacy_removal_approved: bool = False


@dataclass(frozen=True)
class CutoverReportProfile:
    """Perfil operativo usado para nombrar y describir artefactos exportados."""

    name: str
    report_prefix: str


_DEFAULT_REPORT_PREFIXES = {
    "cutover": "legacy_postgres_cutover_run",
    "observation-exit": "legacy_observation_exit",
}


def _normalize_manual_confirmations(
    value: ManualCutoverConfirmations | dict[str, Any] | None,
) -> ManualCutoverConfirmations:
    """Acepta dataclass o mapping y normaliza confirmaciones manuales."""
    if isinstance(value, ManualCutoverConfirmations):
        return value
    if isinstance(value, dict):
        return ManualCutoverConfirmations(
            backup_validated=bool(value.get("backup_validated", False)),
            rollback_approved=bool(value.get("rollback_approved", False)),
            retain_legacy_tables=bool(value.get("retain_legacy_tables", False)),
        )
    return ManualCutoverConfirmations()


def _normalize_observation_confirmations(
    value: ObservationExitConfirmations | dict[str, Any] | None,
) -> ObservationExitConfirmations:
    """Acepta dataclass o mapping y normaliza confirmaciones de observación."""
    if isinstance(value, ObservationExitConfirmations):
        return value
    if isinstance(value, dict):
        return ObservationExitConfirmations(
            observation_window_complete=bool(
                value.get("observation_window_complete", False)
            ),
            no_sev1_sev2_incidents=bool(
                value.get("no_sev1_sev2_incidents", False)
            ),
            representative_reingest_validated=bool(
                value.get("representative_reingest_validated", False)
            ),
            no_sustained_legacy_flags=bool(
                value.get("no_sustained_legacy_flags", False)
            ),
            legacy_removal_approved=bool(
                value.get("legacy_removal_approved", False)
            ),
        )
    return ObservationExitConfirmations()


def resolve_cutover_report_profile(
    profile: str | None,
    report_prefix: str | None,
) -> CutoverReportProfile:
    """Resuelve el perfil operativo y el prefijo de export por defecto."""
    normalized_profile = (profile or "cutover").strip().lower()
    normalized_prefix = (report_prefix or "").strip()

    if normalized_profile not in _DEFAULT_REPORT_PREFIXES:
        raise ValueError(
            "report profile invalido; usa 'cutover' o 'observation-exit'."
        )

    return CutoverReportProfile(
        name=normalized_profile,
        report_prefix=(
            normalized_prefix
            or _DEFAULT_REPORT_PREFIXES[normalized_profile]
        ),
    )


def _manual_checks_complete(confirmations: ManualCutoverConfirmations) -> bool:
    """Indica si las tres aprobaciones manuales quedaron confirmadas."""
    return (
        confirmations.backup_validated
        and confirmations.rollback_approved
        and confirmations.retain_legacy_tables
    )


def _observation_checks_complete(
    confirmations: ObservationExitConfirmations,
) -> bool:
    """Indica si la salida de observación quedó aprobada manualmente."""
    return (
        confirmations.observation_window_complete
        and confirmations.no_sev1_sev2_incidents
        and confirmations.representative_reingest_validated
        and confirmations.no_sustained_legacy_flags
        and confirmations.legacy_removal_approved
    )


def _describe_target(postgres_dsn: str) -> str:
    """Resume host/puerto/base sin exponer credenciales."""
    parsed = urlsplit(postgres_dsn)
    host = parsed.hostname or "<unknown-host>"
    port = parsed.port or 5432
    database = parsed.path.lstrip("/") or "<unknown-db>"
    return f"{host}:{port}/{database}"


def _audit_rows(report: dict[str, Any]) -> list[AuditRow]:
    """Aplana la auditoría por tabla para exportación legible."""
    audit = report.get("audit")
    if not isinstance(audit, dict):
        return []

    rows: list[AuditRow] = []
    for table_name, item in audit.items():
        if not isinstance(item, dict):
            continue
        source_count = int(item.get("source_count", 0) or 0)
        matched_after = int(item.get("matched_after", 0) or 0)
        missing_after = int(item.get("missing_after", 0) or 0)
        rows.append(
            AuditRow(
                table_name=str(table_name),
                source_count=source_count,
                target_count_before=int(item.get("target_count_before", 0) or 0),
                target_count_after=int(item.get("target_count_after", 0) or 0),
                matched_after=matched_after,
                missing_after=missing_after,
                cutover_ready=(
                    missing_after == 0 and matched_after == source_count
                ),
            )
        )
    return rows


def _is_cutover_ready(report: dict[str, Any], rows: list[AuditRow]) -> bool:
    """Evalúa si el reporte cumple el criterio mínimo de cutover."""
    missing_tables = report.get("legacy_tables_missing")
    if isinstance(missing_tables, list) and missing_tables:
        return False
    return bool(rows) and all(row.cutover_ready for row in rows)


def _build_health_report(
    health_url: str | None,
    *,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    """Intenta validar /health cuando el usuario provee una URL objetivo."""
    normalized = (health_url or "").strip()
    if not normalized:
        return None

    try:
        with urlopen(normalized, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            raw_body = response.read().decode("utf-8")
    except HTTPError as exc:
        return {
            "requested": True,
            "ok": False,
            "status_code": int(exc.code),
            "error": str(exc),
        }
    except URLError as exc:
        return {
            "requested": True,
            "ok": False,
            "status_code": None,
            "error": str(exc.reason),
        }

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        payload = raw_body

    ok = status_code == 200
    if isinstance(payload, dict):
        ok = ok and bool(payload.get("ok", False))
    return {
        "requested": True,
        "ok": ok,
        "status_code": status_code,
        "payload": payload,
    }


def _build_checklist(
    *,
    migration_report: dict[str, Any],
    validate_report: dict[str, Any],
    rows: list[AuditRow],
    health_report: dict[str, Any] | None,
    manual_confirmations: ManualCutoverConfirmations,
) -> list[ChecklistItem]:
    """Construye checklist automático + manual para la ventana de cutover."""
    current_heads = list(validate_report.get("current_heads", []))
    expected_heads = list(validate_report.get("expected_heads", []))
    items: list[ChecklistItem] = [
        ChecklistItem(
            title="Heads Alembic alineados tras la migracion",
            ok=(current_heads == expected_heads),
            detail=(
                f"current_heads={current_heads}; expected_heads={expected_heads}"
            ),
            automatic=True,
        ),
        ChecklistItem(
            title="Todas las tablas legacy esperadas estan presentes",
            ok=not bool(migration_report.get("legacy_tables_missing")),
            detail=(
                f"legacy_tables_missing={migration_report.get('legacy_tables_missing', [])}"
            ),
            automatic=True,
        ),
    ]
    for row in rows:
        items.append(
            ChecklistItem(
                title=f"Auditoria de {row.table_name}",
                ok=row.cutover_ready,
                detail=(
                    f"source={row.source_count}; target_before={row.target_count_before}; "
                    f"target_after={row.target_count_after}; matched_after={row.matched_after}; "
                    f"missing_after={row.missing_after}"
                ),
                automatic=True,
            )
        )

    if health_report is not None:
        items.append(
            ChecklistItem(
                title="/health responde OK tras el cutover",
                ok=bool(health_report.get("ok", False)),
                detail=(
                    f"status_code={health_report.get('status_code')}; "
                    f"error={health_report.get('error')}"
                ),
                automatic=True,
            )
        )

    items.extend(
        [
            ChecklistItem(
                title="Backup validado antes del cambio",
                ok=manual_confirmations.backup_validated,
                detail=(
                    "Confirmado por operaciones."
                    if manual_confirmations.backup_validated
                    else "Confirmacion manual requerida por operaciones."
                ),
                automatic=False,
            ),
            ChecklistItem(
                title="Rollback aprobado y documentado",
                ok=manual_confirmations.rollback_approved,
                detail=(
                    "Confirmado por operaciones."
                    if manual_confirmations.rollback_approved
                    else "Confirmacion manual requerida por operaciones."
                ),
                automatic=False,
            ),
            ChecklistItem(
                title="Retener tablas legacy hasta cerrar observacion",
                ok=manual_confirmations.retain_legacy_tables,
                detail=(
                    "Confirmado por operaciones."
                    if manual_confirmations.retain_legacy_tables
                    else "Confirmacion manual requerida por operaciones."
                ),
                automatic=False,
            ),
        ]
    )
    return items


def _build_observation_checklist(
    *,
    automatic_ready: bool,
    confirmations: ObservationExitConfirmations,
) -> list[ChecklistItem]:
    """Construye checklist exportable para aprobar salida de observación."""
    return [
        ChecklistItem(
            title="Evidencia automática del bloque permanece en verde",
            ok=automatic_ready,
            detail=(
                "Alembic, auditoría y health siguen alineados con el corte."
                if automatic_ready
                else "La evidencia automática del bloque ya no está en verde."
            ),
            automatic=True,
        ),
        ChecklistItem(
            title="Ventana de observación completada",
            ok=confirmations.observation_window_complete,
            detail=(
                "Confirmado por operaciones."
                if confirmations.observation_window_complete
                else "Confirmación manual requerida."
            ),
            automatic=False,
        ),
        ChecklistItem(
            title="Sin incidentes Sev1/Sev2 atribuibles al bloque",
            ok=confirmations.no_sev1_sev2_incidents,
            detail=(
                "Confirmado por operaciones."
                if confirmations.no_sev1_sev2_incidents
                else "Confirmación manual requerida."
            ),
            automatic=False,
        ),
        ChecklistItem(
            title="Reingesta representativa validada sin fallback",
            ok=confirmations.representative_reingest_validated,
            detail=(
                "Confirmado por operaciones."
                if confirmations.representative_reingest_validated
                else "Confirmación manual requerida."
            ),
            automatic=False,
        ),
        ChecklistItem(
            title="Sin uso sostenido de flags legacy",
            ok=confirmations.no_sustained_legacy_flags,
            detail=(
                "Confirmado por operaciones."
                if confirmations.no_sustained_legacy_flags
                else "Confirmación manual requerida."
            ),
            automatic=False,
        ),
        ChecklistItem(
            title="Retiro definitivo del legacy aprobado",
            ok=confirmations.legacy_removal_approved,
            detail=(
                "Aprobación formal registrada."
                if confirmations.legacy_removal_approved
                else "Aprobación formal pendiente."
            ),
            automatic=False,
        ),
    ]


def run_legacy_postgres_cutover(
    settings: object,
    *,
    health_url: str | None = None,
    health_timeout_seconds: float = 10.0,
    manual_confirmations: ManualCutoverConfirmations | dict[str, Any] | None = None,
    observation_confirmations: ObservationExitConfirmations
    | dict[str, Any]
    | None = None,
) -> dict[str, Any]:
    """Ejecuta pre-check, migración y validate para un cutover controlado."""
    postgres_dsn = resolve_postgres_dsn(settings)
    if not postgres_dsn:
        raise ValueError(
            "POSTGRES_HOST y credenciales validas son obligatorios para "
            "ejecutar el cutover PostgreSQL legacy."
        )

    current_report = run_postgres_schema_command(settings, operation="current")
    migration_report = run_legacy_postgres_data_migration(settings)
    validate_report = run_postgres_schema_command(settings, operation="validate")
    rows = _audit_rows(migration_report)
    normalized_manual_confirmations = _normalize_manual_confirmations(
        manual_confirmations
    )
    normalized_observation_confirmations = _normalize_observation_confirmations(
        observation_confirmations
    )
    health_report = _build_health_report(
        health_url,
        timeout_seconds=health_timeout_seconds,
    )
    automatic_ready = _is_cutover_ready(migration_report, rows) and (
        list(validate_report.get("current_heads", []))
        == list(validate_report.get("expected_heads", []))
    )
    if health_report is not None:
        automatic_ready = automatic_ready and bool(health_report.get("ok", False))

    checklist = _build_checklist(
        migration_report=migration_report,
        validate_report=validate_report,
        rows=rows,
        health_report=health_report,
        manual_confirmations=normalized_manual_confirmations,
    )
    manual_checks_complete = _manual_checks_complete(normalized_manual_confirmations)
    observation_checklist = _build_observation_checklist(
        automatic_ready=automatic_ready,
        confirmations=normalized_observation_confirmations,
    )
    observation_checks_complete = _observation_checks_complete(
        normalized_observation_confirmations
    )
    return {
        "generated_at": datetime.now().isoformat(),
        "postgres_target": _describe_target(postgres_dsn),
        "automatic_cutover_ready": automatic_ready,
        "manual_checks_complete": manual_checks_complete,
        "cutover_ready": automatic_ready and manual_checks_complete,
        "manual_confirmations": normalized_manual_confirmations.__dict__,
        "observation_exit_automatic_ready": automatic_ready,
        "observation_checks_complete": observation_checks_complete,
        "observation_exit_ready": (
            automatic_ready and observation_checks_complete
        ),
        "observation_confirmations": (
            normalized_observation_confirmations.__dict__
        ),
        "precheck": current_report,
        "migration": migration_report,
        "validate": validate_report,
        "health": health_report,
        "checklist": [item.__dict__ for item in checklist],
        "observation_checklist": [
            item.__dict__ for item in observation_checklist
        ],
        "audit_rows": [row.__dict__ for row in rows],
    }


def write_cutover_reports(
    *,
    output_dir: Path,
    report_prefix: str,
    report: dict[str, Any],
) -> tuple[Path, Path]:
    """Escribe reporte JSON y checklist Markdown para la ventana de cutover."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"{report_prefix}_{timestamp}.json"
    md_path = output_dir / f"{report_prefix}_{timestamp}.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    lines = [
        "# Checklist de Cutover PostgreSQL legacy",
        "",
        f"- Report profile: `{report.get('report_profile', 'cutover')}`",
        f"- Report prefix: `{report.get('report_prefix', report_prefix)}`",
        f"- Target: `{report.get('postgres_target')}`",
        f"- Generated at: `{report.get('generated_at')}`",
        (
            f"- Automatic cutover ready: `{report.get('automatic_cutover_ready')}`"
        ),
        f"- Manual checks complete: `{report.get('manual_checks_complete')}`",
        f"- Cutover ready: `{report.get('cutover_ready')}`",
        (
            "- Observation exit automatic ready: "
            f"`{report.get('observation_exit_automatic_ready')}`"
        ),
        (
            f"- Observation checks complete: "
            f"`{report.get('observation_checks_complete')}`"
        ),
        f"- Observation exit ready: `{report.get('observation_exit_ready')}`",
        "",
        "## Checks automaticos",
        "",
    ]
    checklist = report.get("checklist")
    if isinstance(checklist, list):
        for item in checklist:
            if not isinstance(item, dict) or not item.get("automatic"):
                continue
            marker = "x" if bool(item.get("ok", False)) else " "
            lines.append(f"- [{marker}] {item.get('title')}")
            lines.append(f"  Detalle: {item.get('detail')}")
    lines.extend([
        "",
        "## Checks manuales",
        "",
    ])
    if isinstance(checklist, list):
        for item in checklist:
            if not isinstance(item, dict) or item.get("automatic"):
                continue
            marker = "x" if bool(item.get("ok", False)) else " "
            lines.append(f"- [{marker}] {item.get('title')}")
            lines.append(f"  Detalle: {item.get('detail')}")

    lines.extend([
        "",
        "## Salida de observacion",
        "",
    ])
    observation_checklist = report.get("observation_checklist")
    if isinstance(observation_checklist, list):
        for item in observation_checklist:
            if not isinstance(item, dict):
                continue
            marker = "x" if bool(item.get("ok", False)) else " "
            lines.append(f"- [{marker}] {item.get('title')}")
            lines.append(f"  Detalle: {item.get('detail')}")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path