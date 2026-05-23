"""Wrapper CLI para migrar tablas PostgreSQL legacy al esquema Alembic."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import json
import sys
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit


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


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser CLI para la migración de datos legacy."""
    parser = argparse.ArgumentParser(
        description=(
            "Migra tablas PostgreSQL legacy jobs/repos/lexical_corpus al "
            "esquema Alembic actual tbl_repository_*."
        )
    )
    parser.add_argument(
        "--output-dir",
        default="migration_reports",
        help="directorio donde se escriben JSON/CSV de auditoria",
    )
    parser.add_argument(
        "--report-prefix",
        default="legacy_postgres_cutover_audit",
        help="prefijo base de los archivos exportados",
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="no escribe archivos JSON/CSV; solo imprime el reporte en stdout",
    )
    return parser


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


def write_reports(
    *,
    output_dir: Path,
    report_prefix: str,
    postgres_target: str,
    report: dict[str, Any],
) -> tuple[Path, Path]:
    """Escribe auditoría JSON/CSV reutilizable para cutover."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"{report_prefix}_{timestamp}.json"
    csv_path = output_dir / f"{report_prefix}_{timestamp}.csv"
    rows = _audit_rows(report)
    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "postgres_target": postgres_target,
            "cutover_ready": _is_cutover_ready(report, rows),
            "tables_audited": len(rows),
        },
        "report": report,
        "rows": [row.__dict__ for row in rows],
    }
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    headers = list(AuditRow.__annotations__.keys())
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)

    return json_path, csv_path


def main(argv: Sequence[str] | None = None) -> int:
    """Agrega src/ al path y delega al migrador de datos legacy."""
    args = build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    from coderag.core.settings import get_settings
    from coderag.core.settings import resolve_postgres_dsn
    from coderag.storage.postgres_legacy_migration import (
        run_legacy_postgres_data_migration,
    )

    settings = get_settings()
    try:
        report = run_legacy_postgres_data_migration(settings)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not args.no_export:
        postgres_target = _describe_target(resolve_postgres_dsn(settings))
        json_path, csv_path = write_reports(
            output_dir=Path(args.output_dir),
            report_prefix=str(args.report_prefix),
            postgres_target=postgres_target,
            report=report,
        )
        print(f"JSON: {json_path}")
        print(f"CSV: {csv_path}")

    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())