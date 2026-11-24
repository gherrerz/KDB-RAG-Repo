"""Pruebas de las funciones puras de scripts/measure_code_share.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "measure_code_share.py"
SPEC = importlib.util.spec_from_file_location(
    "measure_code_share", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/routes/router.tsx", "code"),
        ("src/app.TS", "code"),
        ("tools/build.py", "code"),
        ("README.md", "docs"),
        ("openspec/changes/x/spec.mdx", "docs"),
        ("docs/notas.txt", "docs"),
        ("config/app.yaml", "config_data"),
        ("package.json", "config_data"),
        ("pnpm-lock.yaml", "lockfile"),
        ("apps/web/package-lock.json", "lockfile"),
        ("apps\\web\\yarn.lock", "lockfile"),
        ("deps/custom.lock", "lockfile"),
        ("go.sum", "lockfile"),
        ("Makefile", "other"),
        ("assets/logo.bin", "other"),
    ],
)
def test_classify_path(path: str, expected: str) -> None:
    """La clasificación usa nombre base y extensión, en ese orden."""
    assert MODULE.classify_path(path) == expected


def test_summarize_query_counts_and_share() -> None:
    """Cuenta por categoría y calcula la fracción de código."""
    paths = ["a.ts", "b.tsx", "c.md", "d.yaml", "pnpm-lock.yaml"]
    result = MODULE.summarize_query("consulta", paths)
    assert result["chunks"] == 5
    assert result["counts"]["code"] == 2
    assert result["counts"]["docs"] == 1
    assert result["counts"]["config_data"] == 1
    assert result["counts"]["lockfile"] == 1
    assert result["code_share"] == pytest.approx(0.4)


def test_summarize_query_without_chunks_is_zero() -> None:
    """Sin fragmentos la fracción es 0 y no divide por cero."""
    result = MODULE.summarize_query("vacía", [])
    assert result["chunks"] == 0
    assert result["code_share"] == 0.0


def test_aggregate_and_evaluate() -> None:
    """Promedio simple, ponderado y consultas en 0; umbral AC-7."""
    results = [
        MODULE.summarize_query("q1", ["a.ts", "b.ts", "c.md", "d.md"]),
        MODULE.summarize_query("q2", ["a.ts", "b.py", "c.js", "d.md"]),
    ]
    summary = MODULE.aggregate(results)
    assert summary["queries"] == 2
    assert summary["average_code_share"] == pytest.approx(0.625)
    assert summary["pooled_code_share"] == pytest.approx(5 / 8)
    assert summary["queries_at_zero"] == 0
    assert MODULE.evaluate(summary, 0.5) is True
    assert MODULE.evaluate(summary, 0.7) is False


def test_evaluate_fails_when_any_query_is_at_zero() -> None:
    """Un promedio alto no basta si alguna consulta no trae código."""
    results = [
        MODULE.summarize_query("q1", ["a.ts"] * 4),
        MODULE.summarize_query("q2", ["a.ts"] * 4),
        MODULE.summarize_query("q3", ["a.md"] * 4),
    ]
    summary = MODULE.aggregate(results)
    assert summary["average_code_share"] == pytest.approx(2 / 3)
    assert summary["queries_at_zero"] == 1
    assert MODULE.evaluate(summary, 0.5) is False


def test_aggregate_empty_is_not_a_pass() -> None:
    """Sin consultas no hay medición y no se considera aprobada."""
    summary = MODULE.aggregate([])
    assert MODULE.evaluate(summary, 0.0) is False


def test_build_payload_matches_hexa_client_shape() -> None:
    """El cuerpo replica al de hexa-st-be y añade filtros solo si existen."""
    base = MODULE.build_payload(
        repo_id="r", query="q", top_n=60, top_k=20,
        languages=None, path_globs=None,
    )
    assert base == {
        "repo_id": "r",
        "query": "q",
        "top_n": 60,
        "top_k": 20,
        "embedding_provider": "vertex",
        "embedding_model": "text-embedding-005",
        "include_context": False,
    }
    filtered = MODULE.build_payload(
        repo_id="r", query="q", top_n=60, top_k=20,
        languages=["typescript"], path_globs=["src/**/*.tsx"],
    )
    assert filtered["languages"] == ["typescript"]
    assert filtered["path_globs"] == ["src/**/*.tsx"]


def test_load_queries_default_and_file(tmp_path: Path) -> None:
    """Sin archivo se usan las 6 consultas embebidas; con archivo, esas."""
    assert MODULE.load_queries(None) == list(MODULE.DEFAULT_QUERIES)
    assert len(MODULE.DEFAULT_QUERIES) == 6
    custom = tmp_path / "queries.json"
    custom.write_text(json.dumps([" uno ", "dos"]), encoding="utf-8")
    assert MODULE.load_queries(str(custom)) == ["uno", "dos"]
    custom.write_text(json.dumps({"no": "lista"}), encoding="utf-8")
    with pytest.raises(ValueError):
        MODULE.load_queries(str(custom))
