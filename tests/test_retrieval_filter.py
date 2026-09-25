"""Pruebas del filtro por ruta y lenguaje de consultas retrieval-only."""

import pytest

from coderag.core.models import RetrievalChunk
from coderag.retrieval.retrieval_filter import (
    RetrievalFilter,
    chunk_language,
    language_for_path,
    normalize_repo_path,
    path_matches_glob,
)


@pytest.mark.parametrize(
    ("glob", "path", "expected"),
    [
        # `**/` cruza cero o más directorios.
        ("**/*.tsx", "app.tsx", True),
        ("**/*.tsx", "src/app.tsx", True),
        ("**/*.tsx", "src/ui/app.tsx", True),
        ("**/*.tsx", "src/ui/app.ts", False),
        # `*` no cruza `/`; sin coincidencia implícita por nombre base.
        ("*.tsx", "app.tsx", True),
        ("*.tsx", "src/app.tsx", False),
        ("src/*.ts", "src/a.ts", True),
        ("src/*.ts", "src/deep/a.ts", False),
        # `**` al final y en medio.
        ("src/**", "src/a.ts", True),
        ("src/**", "src/x/y/a.ts", True),
        ("src/**", "src", False),
        ("src/**/test.ts", "src/test.ts", True),
        ("src/**/test.ts", "src/a/b/test.ts", True),
        ("src/**/test.ts", "lib/test.ts", False),
        # Un glob terminado en `/` equivale a `<glob>**`.
        ("src/", "src/a/b.ts", True),
        # `?` y clases de caracteres.
        ("src/?.ts", "src/a.ts", True),
        ("src/?.ts", "src/ab.ts", False),
        ("src/[ab].ts", "src/a.ts", True),
        ("src/[ab].ts", "src/c.ts", False),
        ("src/[!ab].ts", "src/c.ts", True),
        ("src/[!ab].ts", "src/a.ts", False),
        # Distingue mayúsculas y escapa metacaracteres de regex.
        ("src/App.tsx", "src/app.tsx", False),
        ("src/a.b.ts", "src/aXb.ts", False),
        ("src/(x)+.ts", "src/(x)+.ts", True),
        # Una `[` sin cierre es literal.
        ("src/[a.ts", "src/[a.ts", True),
    ],
)
def test_path_matches_glob_semantics(
    glob: str,
    path: str,
    expected: bool,
) -> None:
    """Cubre `**`, `*`, `?`, clases, mayúsculas y rutas anidadas."""
    assert path_matches_glob(path, glob) is expected


def test_path_matching_normalizes_backslashes_and_leading_markers() -> None:
    """Acepta rutas con `\\`, `./` o `/` inicial contra globs con `/`."""
    assert normalize_repo_path(".\\src\\app.tsx") == "src/app.tsx"
    assert normalize_repo_path("/src/app.tsx") == "src/app.tsx"
    assert path_matches_glob("src\\ui\\app.tsx", "src/**/*.tsx") is True
    assert path_matches_glob("src/app.tsx", "./src/*.tsx") is True


def test_language_for_path_uses_extension_map_with_text_fallback() -> None:
    """Deriva el lenguaje de LANG_MAP y cae en `text` si no lo conoce."""
    assert language_for_path("src/App.TSX") == "typescript"
    assert language_for_path("src/util.js") == "javascript"
    assert language_for_path("docs/guia.md") == "markdown"
    assert language_for_path("Makefile") == "text"
    assert language_for_path("src/data.unknownext") == "text"
    assert language_for_path("") == "text"


def test_chunk_language_prefers_stored_value_then_path_then_module() -> None:
    """Usa `language` persistido, deriva de la ruta y marca los módulos."""
    assert chunk_language({"language": "Kotlin", "path": "a.ts"}) == "kotlin"
    assert chunk_language({"path": "src/app.tsx"}) == "typescript"
    assert chunk_language({"path": "src/app", "entity_type": "module"}) == (
        "module"
    )
    assert chunk_language({"language": "module", "path": "src/app"}) == (
        "module"
    )


def test_from_request_returns_none_without_criteria() -> None:
    """Listas ausentes, vacías o en blanco no activan filtro."""
    assert RetrievalFilter.from_request(None, None) is None
    assert RetrievalFilter.from_request([], []) is None
    assert RetrievalFilter.from_request([" "], ["  "]) is None


def test_from_request_normalizes_and_deduplicates() -> None:
    """Recorta espacios, deduplica globs y pasa lenguajes a minúsculas."""
    retrieval_filter = RetrievalFilter.from_request(
        [" src/** ", "src/**", "lib/*.py"],
        ["TypeScript", "typescript", " Python "],
    )

    assert retrieval_filter is not None
    assert retrieval_filter.path_globs == ("src/**", "lib/*.py")
    assert retrieval_filter.languages == frozenset({"typescript", "python"})


def test_matches_metadata_combines_globs_with_or_and_languages_with_and() -> (
    None
):
    """Globs entre sí con OR; con lenguajes, AND."""
    retrieval_filter = RetrievalFilter.from_request(
        ["src/**/*.ts*", "lib/**"],
        ["typescript"],
    )
    assert retrieval_filter is not None

    assert retrieval_filter.matches_metadata({"path": "src/ui/App.tsx"})
    assert retrieval_filter.matches_metadata(
        {"path": "lib/util.ts", "language": "typescript"}
    )
    # Cumple el glob pero no el lenguaje.
    assert not retrieval_filter.matches_metadata({"path": "lib/notes.md"})
    # Cumple el lenguaje pero no ningún glob.
    assert not retrieval_filter.matches_metadata({"path": "other/a.ts"})
    # Sin ruta no puede cumplir un filtro de ruta.
    assert not retrieval_filter.matches_metadata({})


def test_language_only_filter_ignores_path_and_excludes_module_summaries() -> (
    None
):
    """Con solo lenguajes, la ruta no importa y los módulos quedan fuera."""
    retrieval_filter = RetrievalFilter.from_request(None, ["typescript"])
    assert retrieval_filter is not None

    assert retrieval_filter.matches_metadata({"path": "anywhere/a.tsx"})
    assert not retrieval_filter.matches_metadata({"path": "docs/a.md"})
    assert not retrieval_filter.matches_metadata(
        {"path": "src/ui", "language": "module"}
    )
    module_filter = RetrievalFilter.from_request(None, ["module"])
    assert module_filter is not None
    assert module_filter.matches_metadata(
        {"path": "src/ui", "language": "module"}
    )


def test_unknown_language_matches_nothing() -> None:
    """Un lenguaje desconocido no coincide con ningún archivo conocido."""
    retrieval_filter = RetrievalFilter.from_request(None, ["cobol"])
    assert retrieval_filter is not None

    assert not retrieval_filter.matches_metadata({"path": "src/a.ts"})
    assert not retrieval_filter.matches_metadata({"path": "README"})


def test_filter_chunks_keeps_only_matching_chunks() -> None:
    """filter_chunks conserva orden y descarta los que incumplen."""
    chunks = [
        RetrievalChunk(id="a", text="", score=1.0, metadata={"path": "a.md"}),
        RetrievalChunk(
            id="b", text="", score=0.9, metadata={"path": "src/b.tsx"}
        ),
        RetrievalChunk(
            id="c", text="", score=0.8, metadata={"path": "src/c.ts"}
        ),
    ]
    retrieval_filter = RetrievalFilter.from_request(["src/**"], ["typescript"])
    assert retrieval_filter is not None

    kept = retrieval_filter.filter_chunks(chunks)

    assert [chunk.id for chunk in kept] == ["b", "c"]


def test_filter_graph_records_drops_out_of_filter_files_only() -> None:
    """Descarta archivos vecinos fuera del filtro y conserva los sin ruta."""
    records = [
        {"labels": ["File"], "props": {"path": "src/ok.ts"}},
        {"labels": ["File"], "props": {"path": "docs/guide.md"}},
        {
            "labels": ["ExternalSymbol"],
            "props": {"ref": "react"},
            "source_path": "src/ok.ts",
        },
        {
            "labels": ["ExternalSymbol"],
            "props": {"ref": "lodash"},
            "source_path": "docs/other.md",
        },
        {"labels": ["ExternalSymbol"], "props": {"ref": "left-pad"}},
    ]
    retrieval_filter = RetrievalFilter.from_request(["src/**"], None)
    assert retrieval_filter is not None

    kept = retrieval_filter.filter_graph_records(records)

    labels = [
        item["props"].get("path") or item["props"]["ref"] for item in kept
    ]
    assert labels == ["src/ok.ts", "react", "left-pad"]


def test_chroma_where_combines_repo_and_languages_with_and() -> None:
    """Sin lenguajes queda el where previo; con lenguajes usa `$and`/`$in`."""
    only_paths = RetrievalFilter.from_request(["src/**"], None)
    with_languages = RetrievalFilter.from_request(
        ["src/**"], ["typescript", "javascript"]
    )
    assert only_paths is not None and with_languages is not None

    assert only_paths.chroma_where("repo-1") == {"repo_id": "repo-1"}
    assert with_languages.chroma_where("repo-1") == {
        "$and": [
            {"repo_id": "repo-1"},
            {"language": {"$in": ["javascript", "typescript"]}},
        ]
    }


def test_describe_is_stable_for_diagnostics() -> None:
    """describe() devuelve listas ordenadas y serializables."""
    retrieval_filter = RetrievalFilter.from_request(
        ["b/**", "a/**"], ["python", "go"]
    )
    assert retrieval_filter is not None

    assert retrieval_filter.describe() == {
        "path_globs": ["b/**", "a/**"],
        "languages": ["go", "python"],
    }
