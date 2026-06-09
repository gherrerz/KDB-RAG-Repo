"""Materializa el gold set de code retrieval contra el workspace local."""

from __future__ import annotations

import argparse
import ast
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any


WHOLE_FILE_PREVIEW_LINES = 40


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the materialization step."""
    repo_root = Path(__file__).resolve().parents[1]
    default_gold = repo_root / "scripts" / "benchmark_data" / "code_retrieval_gold.json"
    default_output = repo_root / "benchmark_reports" / "code_gold_materialized.json"
    parser = argparse.ArgumentParser(
        description="Materializa spans y contextos del gold set de code retrieval."
    )
    parser.add_argument(
        "--gold-file",
        type=Path,
        default=default_gold,
        help="Ruta al gold set JSON.",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=repo_root,
        help="Raiz local del repo sobre la que se resuelven los paths.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help="Ruta del JSON materializado.",
    )
    return parser.parse_args()


def _load_json(file_path: Path) -> dict[str, Any]:
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _python_symbol_spans(content: str, symbol_name: str) -> list[tuple[int, int]]:
    try:
        module_ast = ast.parse(content)
    except SyntaxError:
        return []

    spans: list[tuple[int, int]] = []
    for node in ast.walk(module_ast):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.name != symbol_name:
            continue
        spans.append((int(node.lineno), int(getattr(node, "end_lineno", node.lineno))))
    return spans


def _read_text(file_path: Path) -> str:
    return file_path.read_text(encoding="utf-8", errors="ignore")


def _slice_lines(content: str, start_line: int, end_line: int) -> str:
    lines = content.splitlines()
    return "\n".join(lines[start_line - 1:end_line])


def _preview_text(content: str, line_limit: int = WHOLE_FILE_PREVIEW_LINES) -> str:
    lines = content.splitlines()
    return "\n".join(lines[:line_limit])


def _resolve_span(
    *,
    file_path: Path,
    content: str,
    symbol_name: str | None,
    expected_start_line: int | None,
    expected_end_line: int | None,
    errors: list[str],
) -> tuple[int | None, int | None, str]:
    resolved_start = expected_start_line
    resolved_end = expected_end_line

    if symbol_name and file_path.suffix.lower() == ".py":
        spans = _python_symbol_spans(content, symbol_name)
        if len(spans) == 1:
            symbol_start, symbol_end = spans[0]
            if resolved_start is None:
                resolved_start = symbol_start
            elif resolved_start != symbol_start:
                errors.append(
                    f"symbol_start_mismatch:{symbol_name}:{resolved_start}!={symbol_start}"
                )
            if resolved_end is None:
                resolved_end = symbol_end
            elif resolved_end != symbol_end:
                errors.append(
                    f"symbol_end_mismatch:{symbol_name}:{resolved_end}!={symbol_end}"
                )
        elif not spans:
            errors.append(f"symbol_not_found:{symbol_name}")
        else:
            errors.append(f"symbol_ambiguous:{symbol_name}")

    if resolved_start is None and resolved_end is None:
        return None, None, "whole_file"

    if resolved_start is None or resolved_end is None:
        errors.append("incomplete_span")
        if resolved_start is None and resolved_end is not None:
            resolved_start = resolved_end
        if resolved_end is None and resolved_start is not None:
            resolved_end = resolved_start

    assert resolved_start is not None
    assert resolved_end is not None
    if resolved_start < 1:
        errors.append(f"invalid_start_line:{resolved_start}")
    if resolved_end < resolved_start:
        errors.append(f"invalid_end_line:{resolved_end}")
    return resolved_start, resolved_end, "span"


def _materialize_target(
    *,
    workspace_root: Path,
    target: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    relative_path = str(target.get("path") or target.get("expected_path") or "").strip()
    file_path = workspace_root / relative_path
    if not relative_path:
        errors.append("missing_path")
    if not file_path.exists() or not file_path.is_file():
        errors.append(f"missing_file:{relative_path}")
        return (
            {
                "path": relative_path,
                "kind": target.get("kind") or target.get("expected_kind"),
                "symbol_name": target.get("symbol_name"),
            },
            errors,
        )

    content = _read_text(file_path)
    lines = content.splitlines()
    start_line, end_line, content_strategy = _resolve_span(
        file_path=file_path,
        content=content,
        symbol_name=target.get("symbol_name"),
        expected_start_line=target.get("start_line", target.get("expected_start_line")),
        expected_end_line=target.get("end_line", target.get("expected_end_line")),
        errors=errors,
    )

    materialized: dict[str, Any] = {
        "path": relative_path,
        "kind": target.get("kind") or target.get("expected_kind"),
        "symbol_name": target.get("symbol_name"),
        "content_strategy": content_strategy,
        "file_line_count": len(lines),
    }
    if content_strategy == "whole_file":
        preview_end_line = min(len(lines), WHOLE_FILE_PREVIEW_LINES)
        materialized.update(
            {
                "start_line": None,
                "end_line": None,
                "preview_start_line": 1 if lines else 0,
                "preview_end_line": preview_end_line,
                "snippet": None,
                "snippet_preview": _preview_text(content),
            }
        )
        return materialized, errors

    assert start_line is not None
    assert end_line is not None
    if end_line > len(lines):
        errors.append(f"end_line_out_of_range:{end_line}>{len(lines)}")
        end_line = len(lines)
    snippet = _slice_lines(content, start_line, end_line)
    materialized.update(
        {
            "start_line": start_line,
            "end_line": end_line,
            "preview_start_line": start_line,
            "preview_end_line": end_line,
            "snippet": snippet,
            "snippet_preview": snippet,
        }
    )
    return materialized, errors


def materialize_dataset(
    gold_data: dict[str, Any],
    workspace_root: Path,
) -> dict[str, Any]:
    """Resolve all primary and alternative targets against the local workspace."""
    materialized_queries: list[dict[str, Any]] = []

    for entry in gold_data.get("queries", []):
        primary_target = {
            "expected_path": entry.get("expected_path"),
            "expected_start_line": entry.get("expected_start_line"),
            "expected_end_line": entry.get("expected_end_line"),
            "symbol_name": entry.get("symbol_name"),
            "expected_kind": entry.get("expected_kind"),
        }
        materialized_expected, primary_errors = _materialize_target(
            workspace_root=workspace_root,
            target=primary_target,
        )

        materialized_alternatives: list[dict[str, Any]] = []
        alternative_errors: list[str] = []
        for alternative in entry.get("acceptable_alternatives", []):
            materialized_alternative, alt_errors = _materialize_target(
                workspace_root=workspace_root,
                target=alternative,
            )
            materialized_alternative["reason"] = alternative.get("reason")
            materialized_alternatives.append(materialized_alternative)
            alternative_errors.extend(alt_errors)

        errors = primary_errors + alternative_errors
        materialized_queries.append(
            {
                **entry,
                "materialized_expected": materialized_expected,
                "materialized_alternatives": materialized_alternatives,
                "validation_errors": errors,
                "valid": not errors,
            }
        )

    valid_queries = sum(1 for entry in materialized_queries if entry["valid"])
    return {
        "dataset_name": gold_data.get("dataset_name", "code_retrieval_gold"),
        "repo_id": gold_data.get("repo_id"),
        "defaults": gold_data.get("defaults", {}),
        "generated_at": datetime.now(UTC).isoformat(),
        "workspace_root": str(workspace_root.resolve()),
        "total_queries": len(materialized_queries),
        "valid_queries": valid_queries,
        "invalid_queries": len(materialized_queries) - valid_queries,
        "queries": materialized_queries,
    }


def main() -> int:
    """CLI entrypoint for code retrieval gold set materialization."""
    args = parse_args()
    gold_data = _load_json(args.gold_file)
    materialized = materialize_dataset(gold_data, args.workspace_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(materialized, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(
        "materialized_queries="
        f"{materialized['total_queries']} valid={materialized['valid_queries']} "
        f"invalid={materialized['invalid_queries']} output={args.output}"
    )
    return 0 if materialized["invalid_queries"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())