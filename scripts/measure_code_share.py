"""Mide qué porción de los resultados de /query/retrieval es código fuente.

Uso típico (STORY-156 / KAN-270, AC-7): lanzar consultas en español contra
``POST /query/retrieval`` y clasificar cada fragmento devuelto por la
extensión de su ruta. El script solo usa la biblioteca estándar.

Clasificación por ruta (primera regla que coincide):

* ``lockfile``: nombre base en ``LOCKFILE_NAMES`` o extensión ``.lock``.
* ``code``: extensión en ``CODE_EXTENSIONS``.
* ``docs``: extensión en ``DOC_EXTENSIONS``.
* ``config_data``: extensión en ``CONFIG_EXTENSIONS``.
* ``other``: cualquier otra cosa (sin extensión, binarios, etc.). No cuenta
  como código.

El script no decide por sí solo si la medición es aceptable: imprime una
línea PASS/FAIL contra ``--threshold`` y "ninguna consulta en 0", pero el
código de salida solo es distinto de cero ante errores de transporte.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import sys
from pathlib import Path
from typing import Any
from urllib import error, request

# Títulos de las 6 historias del run 2 de la prueba guiada del flujo
# federado (caso "nueva pantalla de login para hexa-st", 2026-09-24), tal
# como salieron del paso 1 (PO): odd/evidencia/prueba-flujo-login-2026-09-24/
# run-2/step1-after-generation.json, campo "titulo".
DEFAULT_QUERIES: tuple[str, ...] = (
    "Visualización de la Pantalla de Login",
    "Validación de Formato de Correo Electrónico",
    "Validación de Contraseña Vacía",
    "Autenticación Exitosa y Redirección",
    "Manejo de Credenciales Incorrectas",
    "Indicador de Carga durante Autenticación",
)

# Extensiones de código fuente (con el punto, en minúsculas).
CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".java",
        ".kt", ".kts", ".go", ".rs", ".rb", ".php", ".cs", ".c", ".h",
        ".cpp", ".hpp", ".cc", ".swift", ".scala", ".sh", ".sql",
        ".vue", ".svelte", ".css", ".scss", ".html",
    }
)

# Extensiones de documentación.
DOC_EXTENSIONS: frozenset[str] = frozenset(
    {".md", ".mdx", ".rst", ".txt", ".adoc"}
)

# Extensiones de configuración o datos.
CONFIG_EXTENSIONS: frozenset[str] = frozenset(
    {".yaml", ".yml", ".json", ".toml", ".ini", ".xml", ".cfg", ".csv",
     ".properties"}
)

# Nombres base de lockfiles (comparados en minúsculas). Además se trata
# como lockfile cualquier archivo con extensión ``.lock``.
LOCKFILE_NAMES: frozenset[str] = frozenset(
    {
        "pnpm-lock.yaml", "package-lock.json", "yarn.lock",
        "npm-shrinkwrap.json", "poetry.lock", "pipfile.lock", "uv.lock",
        "cargo.lock", "composer.lock", "gemfile.lock", "go.sum",
    }
)

CATEGORIES: tuple[str, ...] = (
    "code", "docs", "config_data", "lockfile", "other",
)


def classify_path(path: str) -> str:
    """Clasifica una ruta en code, docs, config_data, lockfile u other."""
    normalized = path.replace("\\", "/")
    basename = posixpath.basename(normalized).lower()
    extension = posixpath.splitext(basename)[1]
    if basename in LOCKFILE_NAMES or extension == ".lock":
        return "lockfile"
    if extension in CODE_EXTENSIONS:
        return "code"
    if extension in DOC_EXTENSIONS:
        return "docs"
    if extension in CONFIG_EXTENSIONS:
        return "config_data"
    return "other"


def build_payload(
    *,
    repo_id: str,
    query: str,
    top_n: int,
    top_k: int,
    languages: list[str] | None,
    path_globs: list[str] | None,
) -> dict[str, Any]:
    """Arma el cuerpo de /query/retrieval igual que hexa-st-be."""
    payload: dict[str, Any] = {
        "repo_id": repo_id,
        "query": query,
        "top_n": top_n,
        "top_k": top_k,
        "embedding_provider": "vertex",
        "embedding_model": "text-embedding-005",
        "include_context": False,
    }
    if path_globs:
        payload["path_globs"] = path_globs
    if languages:
        payload["languages"] = languages
    return payload


def summarize_query(query: str, paths: list[str]) -> dict[str, Any]:
    """Cuenta fragmentos por categoría para una consulta."""
    counts = {category: 0 for category in CATEGORIES}
    for path in paths:
        counts[classify_path(path)] += 1
    total = len(paths)
    share = counts["code"] / total if total else 0.0
    return {
        "query": query,
        "chunks": total,
        "counts": counts,
        "code_share": share,
        "paths": list(paths),
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcula promedio por consulta, promedio ponderado y consultas en 0."""
    if not results:
        return {
            "queries": 0,
            "average_code_share": 0.0,
            "pooled_code_share": 0.0,
            "queries_at_zero": 0,
        }
    total_chunks = sum(item["chunks"] for item in results)
    total_code = sum(item["counts"]["code"] for item in results)
    return {
        "queries": len(results),
        "average_code_share": (
            sum(item["code_share"] for item in results) / len(results)
        ),
        "pooled_code_share": (
            total_code / total_chunks if total_chunks else 0.0
        ),
        "queries_at_zero": sum(
            1 for item in results if item["counts"]["code"] == 0
        ),
    }


def evaluate(summary: dict[str, Any], threshold: float) -> bool:
    """AC-7: promedio >= umbral y ninguna consulta con 0 de código."""
    return (
        summary["queries"] > 0
        and summary["average_code_share"] >= threshold
        and summary["queries_at_zero"] == 0
    )


def post_json(
    url: str,
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    """Envía un POST JSON y devuelve el cuerpo; RuntimeError si falla."""
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} en {url}: {detail}") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Error de transporte en {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Respuesta no JSON en {url}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Respuesta inesperada en {url}.")
    return parsed


def load_queries(queries_file: str | None) -> list[str]:
    """Carga las consultas desde un JSON (lista de strings) o los defaults."""
    if not queries_file:
        return list(DEFAULT_QUERIES)
    raw = json.loads(Path(queries_file).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not all(
        isinstance(item, str) and item.strip() for item in raw
    ):
        raise ValueError(
            "--queries-file debe ser una lista JSON de strings no vacíos."
        )
    return [item.strip() for item in raw]


def render_table(results: list[dict[str, Any]]) -> str:
    """Devuelve la tabla por consulta en texto plano."""
    lines = [f"{'chunks':>6} {'code':>5} {'%code':>6}  query"]
    for item in results:
        lines.append(
            f"{item['chunks']:>6} {item['counts']['code']:>5} "
            f"{item['code_share'] * 100:>5.1f}%  {item['query']}"
        )
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Define y parsea los argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description=(
            "Mide el porcentaje de fragmentos de código en los resultados "
            "de POST /query/retrieval."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="URL base de la API (por defecto http://localhost:8000).",
    )
    parser.add_argument(
        "--repo-id",
        required=True,
        help="Identificador del repositorio indexado a consultar.",
    )
    parser.add_argument(
        "--top-k", type=int, default=20,
        help="Fragmentos finales por consulta (por defecto 20).",
    )
    parser.add_argument(
        "--top-n", type=int, default=100,
        help=(
            "Candidatos antes del reranking (por defecto 100, el valor que "
            "envía hexa-st-be desde STORY-156)."
        ),
    )
    parser.add_argument(
        "--queries-file",
        default=None,
        help=(
            "JSON con una lista de consultas. Sin valor se usan los 6 "
            "títulos en español del run 2."
        ),
    )
    parser.add_argument(
        "--languages", nargs="+", default=None,
        help="Filtro opcional de lenguajes que se reenvía a la API.",
    )
    parser.add_argument(
        "--path-globs", nargs="+", default=None,
        help="Filtro opcional de globs de ruta que se reenvía a la API.",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.5,
        help="Umbral del promedio de código para el PASS (0.5 por defecto).",
    )
    parser.add_argument(
        "--timeout", type=float, default=180.0,
        help="Timeout HTTP por consulta en segundos (180 por defecto).",
    )
    parser.add_argument(
        "--json-out", default=None,
        help="Ruta donde guardar el resultado completo en JSON.",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    """Ejecuta la medición; devuelve 2 solo ante errores de transporte."""
    queries = load_queries(args.queries_file)
    url = f"{args.base_url.rstrip('/')}/query/retrieval"
    results: list[dict[str, Any]] = []
    for query in queries:
        payload = build_payload(
            repo_id=args.repo_id,
            query=query,
            top_n=args.top_n,
            top_k=args.top_k,
            languages=args.languages,
            path_globs=args.path_globs,
        )
        try:
            body = post_json(url, payload, args.timeout)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        chunks = body.get("chunks") or []
        paths = [str(chunk.get("path", "")) for chunk in chunks]
        results.append(summarize_query(query, paths))

    summary = aggregate(results)
    print(render_table(results))
    print(
        f"Promedio por consulta: {summary['average_code_share'] * 100:.1f}% "
        f"(ponderado: {summary['pooled_code_share'] * 100:.1f}%); "
        f"consultas en 0: {summary['queries_at_zero']}"
    )
    passed = evaluate(summary, args.threshold)
    print(
        f"{'PASS' if passed else 'FAIL'}: promedio >= "
        f"{args.threshold * 100:.0f}% y ninguna consulta en 0"
    )
    if args.json_out:
        output = {
            "base_url": args.base_url,
            "repo_id": args.repo_id,
            "top_n": args.top_n,
            "top_k": args.top_k,
            "languages": args.languages,
            "path_globs": args.path_globs,
            "threshold": args.threshold,
            "summary": summary,
            "passed": passed,
            "results": results,
        }
        Path(args.json_out).write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        # Las consultas llevan tildes; evita salida ilegible en Windows.
        reconfigure(encoding="utf-8")
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
