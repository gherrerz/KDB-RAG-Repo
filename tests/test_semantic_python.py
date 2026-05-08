"""Tests for Python semantic relation extraction."""

from coderag.core.models import FileImportRelation, ScannedFile
from coderag.ingestion.chunker import extract_symbol_chunks
from coderag.ingestion.semantic_python import extract_python_semantic_relations


def test_extract_python_semantic_relations_intrafile_resolution() -> None:
    """Extracts CALLS/EXTENDS/IMPORTS and resolves intra-file targets."""
    content = (
        "class Base:\n"
        "    pass\n\n"
        "class Child(Base):\n"
        "    pass\n\n"
        "def helper():\n"
        "    return 1\n\n"
        "def use_helper():\n"
        "    import json\n"
        "    helper()\n"
    )
    scanned_files = [
        ScannedFile(
            path="pkg/mod.py",
            language="python",
            content=content,
        )
    ]
    symbols = extract_symbol_chunks(repo_id="repo-1", scanned_files=scanned_files)

    relations = extract_python_semantic_relations(
        repo_id="repo-1",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(
        item.relation_type == "EXTENDS"
        and item.target_ref == "Base"
        and item.target_symbol_id is not None
        for item in relations
    )
    assert any(
        item.relation_type == "CALLS"
        and item.target_ref == "helper"
        and item.target_symbol_id is not None
        for item in relations
    )
    assert any(
        item.relation_type == "IMPORTS"
        and item.target_ref == "json"
        and item.target_symbol_id is None
        for item in relations
    )


def test_extract_python_semantic_relations_ignores_syntax_errors() -> None:
    """Skips malformed Python files without failing extraction."""
    scanned_files = [
        ScannedFile(
            path="pkg/broken.py",
            language="python",
            content="def broken(:\n    pass\n",
        )
    ]
    symbols = extract_symbol_chunks(repo_id="repo-1", scanned_files=scanned_files)

    relations = extract_python_semantic_relations(
        repo_id="repo-1",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert relations == []


def test_extract_python_semantic_relations_resolves_absolute_cross_file_import() -> None:
    """Resolves cross-file imports through absolute module references."""
    scanned_files = [
        ScannedFile(
            path="pkg/shared.py",
            language="python",
            content="def helper():\n    return 1\n",
        ),
        ScannedFile(
            path="pkg/consumer.py",
            language="python",
            content=(
                "def run():\n"
                "    from pkg.shared import helper\n"
                "    helper()\n"
            ),
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-1", scanned_files=scanned_files)

    relations = extract_python_semantic_relations(
        repo_id="repo-1",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(
        item.relation_type == "CALLS"
        and item.target_ref == "helper"
        and item.target_symbol_id is not None
        for item in relations
    )
    assert any(
        item.relation_type == "IMPORTS"
        and item.target_ref == "pkg.shared.helper"
        and item.target_symbol_id is not None
        for item in relations
    )


def test_extract_python_semantic_relations_resolves_relative_import_alias() -> None:
    """Resolves aliases imported from sibling Python modules."""
    scanned_files = [
        ScannedFile(
            path="pkg/utils.py",
            language="python",
            content="def helper():\n    return 1\n",
        ),
        ScannedFile(
            path="pkg/consumer.py",
            language="python",
            content=(
                "def run():\n"
                "    from .utils import helper as local_helper\n"
                "    local_helper()\n"
            ),
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-1", scanned_files=scanned_files)

    relations = extract_python_semantic_relations(
        repo_id="repo-1",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(
        item.relation_type == "CALLS"
        and item.target_ref == "local_helper"
        and item.target_symbol_id is not None
        for item in relations
    )
    assert any(
        item.relation_type == "IMPORTS"
        and item.target_ref == "pkg.utils.helper"
        and item.target_symbol_id is not None
        for item in relations
    )


def test_extract_python_semantic_relations_uses_top_level_import_binding() -> None:
    """Applies top-level module aliases to later calls inside functions."""
    scanned_files = [
        ScannedFile(
            path="pkg/helpers.py",
            language="python",
            content="def helper():\n    return 1\n",
        ),
        ScannedFile(
            path="pkg/consumer.py",
            language="python",
            content=(
                "import pkg.helpers as helpers\n\n"
                "def run():\n"
                "    helpers.helper()\n"
            ),
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-1", scanned_files=scanned_files)

    relations = extract_python_semantic_relations(
        repo_id="repo-1",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(
        item.relation_type == "CALLS"
        and item.target_ref == "helpers.helper"
        and item.target_symbol_id is not None
        for item in relations
    )


def test_extract_python_semantic_relations_reports_resolution_stats() -> None:
    """Exposes Python resolution source counts for diagnostics."""
    scanned_files = [
        ScannedFile(
            path="pkg/shared.py",
            language="python",
            content="def helper():\n    return 1\n",
        ),
        ScannedFile(
            path="pkg/consumer.py",
            language="python",
            content=(
                "from pkg.shared import helper as imported_helper\n\n"
                "def helper():\n"
                "    return 1\n\n"
                "def run():\n"
                "    helper()\n"
                "    imported_helper()\n"
                "    missing()\n"
            ),
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-1", scanned_files=scanned_files)
    stats: dict[str, int] = {}

    extract_python_semantic_relations(
        repo_id="repo-1",
        scanned_files=scanned_files,
        symbols=symbols,
        resolution_stats_sink=stats,
    )

    assert stats.get("local", 0) >= 1
    assert stats.get("alias", 0) >= 1
    assert stats.get("unresolved", 0) >= 1


def test_extract_python_semantic_relations_emits_top_level_file_imports() -> None:
    """Captures top-level Python imports as file-scoped dependency relations."""
    scanned_files = [
        ScannedFile(
            path="pkg/shared.py",
            language="python",
            content="def helper():\n    return 1\n",
        ),
        ScannedFile(
            path="pkg/consumer.py",
            language="python",
            content=(
                "from pkg.shared import helper\n"
                "import json\n\n"
                "def run():\n"
                "    helper()\n"
            ),
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-1", scanned_files=scanned_files)
    file_imports: list[FileImportRelation] = []

    extract_python_semantic_relations(
        repo_id="repo-1",
        scanned_files=scanned_files,
        symbols=symbols,
        file_imports_sink=file_imports,
    )

    assert any(
        item.source_path == "pkg/consumer.py"
        and item.target_path == "pkg/shared.py"
        and item.target_ref == "pkg.shared.helper"
        and item.target_kind == "file"
        for item in file_imports
    )
    assert any(
        item.source_path == "pkg/consumer.py"
        and item.target_path is None
        and item.target_ref == "json"
        and item.target_kind == "external"
        for item in file_imports
    )
