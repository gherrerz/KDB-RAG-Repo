"""Runner CLI para cutover controlado de PostgreSQL legacy a Alembic."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser del runner de cutover."""
    parser = argparse.ArgumentParser(
        description=(
            "Ejecuta pre-check, migracion legacy, validate y exporta "
            "evidencia de cutover para PostgreSQL."
        )
    )
    parser.add_argument(
        "--report-profile",
        choices=("cutover", "observation-exit"),
        default="cutover",
        help=(
            "perfil operativo del artefacto exportado; define el prefijo "
            "por defecto cuando no se informa --report-prefix"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="migration_reports",
        help="directorio donde se escriben JSON/Markdown de cutover",
    )
    parser.add_argument(
        "--report-prefix",
        default="",
        help="prefijo base de los archivos exportados; por defecto depende de --report-profile",
    )
    parser.add_argument(
        "--health-url",
        default="",
        help="URL opcional de /health para post-check HTTP",
    )
    parser.add_argument(
        "--health-timeout-seconds",
        type=float,
        default=10.0,
        help="timeout del chequeo opcional de /health",
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="no escribe archivos; solo imprime el reporte en stdout",
    )
    parser.add_argument(
        "--confirm-backup",
        action="store_true",
        help="marca el check manual de backup validado",
    )
    parser.add_argument(
        "--confirm-rollback",
        action="store_true",
        help="marca el check manual de rollback aprobado y documentado",
    )
    parser.add_argument(
        "--confirm-retain-legacy",
        action="store_true",
        help="marca el check manual de retencion temporal de tablas legacy",
    )
    parser.add_argument(
        "--confirm-observation-window",
        action="store_true",
        help="marca que la ventana de observacion ya fue completada",
    )
    parser.add_argument(
        "--confirm-no-sev1-sev2",
        action="store_true",
        help="marca que no hubo incidentes Sev1/Sev2 atribuibles al bloque",
    )
    parser.add_argument(
        "--confirm-representative-reingest",
        action="store_true",
        help="marca que una reingesta representativa fue validada sin fallback",
    )
    parser.add_argument(
        "--confirm-no-sustained-legacy-flags",
        action="store_true",
        help="marca que no hubo uso sostenido de flags legacy durante la observacion",
    )
    parser.add_argument(
        "--approve-legacy-removal",
        action="store_true",
        help="registra la aprobacion formal para iniciar el retiro definitivo del legacy",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Agrega src/ al path y delega al orquestador de cutover."""
    args = build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    from coderag.core.settings import get_settings
    from coderag.storage.postgres_legacy_cutover import (
        resolve_cutover_report_profile,
        run_legacy_postgres_cutover,
        write_cutover_reports,
    )

    profile = resolve_cutover_report_profile(
        args.report_profile,
        args.report_prefix,
    )

    try:
        report = run_legacy_postgres_cutover(
            get_settings(),
            health_url=str(args.health_url),
            health_timeout_seconds=float(args.health_timeout_seconds),
            manual_confirmations={
                "backup_validated": bool(args.confirm_backup),
                "rollback_approved": bool(args.confirm_rollback),
                "retain_legacy_tables": bool(args.confirm_retain_legacy),
            },
            observation_confirmations={
                "observation_window_complete": bool(
                    args.confirm_observation_window
                ),
                "no_sev1_sev2_incidents": bool(args.confirm_no_sev1_sev2),
                "representative_reingest_validated": bool(
                    args.confirm_representative_reingest
                ),
                "no_sustained_legacy_flags": bool(
                    args.confirm_no_sustained_legacy_flags
                ),
                "legacy_removal_approved": bool(
                    args.approve_legacy_removal
                ),
            },
        )
        report["report_profile"] = profile.name
        report["report_prefix"] = profile.report_prefix
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not args.no_export:
        json_path, md_path = write_cutover_reports(
            output_dir=Path(args.output_dir),
            report_prefix=profile.report_prefix,
            report=report,
        )
        print(f"JSON: {json_path}")
        print(f"Checklist: {md_path}")

    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())