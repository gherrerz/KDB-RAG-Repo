"""Tests for shared module resolution helpers."""

from coderag.core.models import ScannedFile, SymbolChunk
from coderag.ingestion.module_resolver import (
    build_python_module_index,
    build_python_qualified_name_index,
    derive_python_module_name,
    load_tsconfig_paths,
    normalize_js_import_path,
    resolve_python_relative_import,
)


def test_derive_python_module_name_for_regular_module() -> None:
    """Converts a Python module file path into a dotted module name."""
    assert derive_python_module_name("pkg/sub/module.py") == "pkg.sub.module"


def test_derive_python_module_name_for_package_init() -> None:
    """Converts package __init__ files into package module names."""
    assert derive_python_module_name("pkg/sub/__init__.py") == "pkg.sub"


def test_build_python_qualified_name_index_uses_module_path() -> None:
    """Builds qualified names from the owning file module path."""
    scanned_files = [
        ScannedFile(path="pkg/helpers.py", language="python", content=""),
    ]
    symbols = [
        SymbolChunk(
            id="s1",
            repo_id="repo-1",
            path="pkg/helpers.py",
            language="python",
            symbol_name="helper",
            symbol_type="function",
            start_line=1,
            end_line=1,
            snippet="def helper(): ...",
        )
    ]

    qualified_index = build_python_qualified_name_index(
        symbols,
        build_python_module_index(scanned_files),
    )

    assert qualified_index == {"pkg.helpers.helper": "s1"}


def test_resolve_python_relative_import_for_sibling_module() -> None:
    """Resolves sibling relative imports from a standard module file."""
    assert (
        resolve_python_relative_import(
            source_path="pkg/consumer.py",
            level=1,
            module="utils",
            name="helper",
        )
        == "pkg.utils.helper"
    )


def test_resolve_python_relative_import_for_parent_package() -> None:
    """Resolves parent relative imports from nested modules."""
    assert (
        resolve_python_relative_import(
            source_path="pkg/api/consumer.py",
            level=2,
            module="shared",
            name="helper",
        )
        == "pkg.shared.helper"
    )


def test_normalize_js_import_path_probes_extensions_and_index_files() -> None:
    """Resolves JS imports by trying extensions and index files."""
    scanned_paths = {"src/lib.ts", "src/components/button/index.tsx"}

    assert (
        normalize_js_import_path("src/app.ts", "./lib", scanned_paths)
        == "src/lib.ts"
    )
    assert (
        normalize_js_import_path(
            "src/app.ts",
            "./components/button",
            scanned_paths,
        )
        == "src/components/button/index.tsx"
    )


def test_load_tsconfig_paths_parses_base_url_and_aliases() -> None:
    """Loads tsconfig baseUrl and first path alias target."""
    scanned_files = [
        ScannedFile(
            path="web/tsconfig.json",
            language="json",
            content=(
                '{"compilerOptions":{"baseUrl":"src","paths":{"@ui/*":["components/*"]}}}'
            ),
        )
    ]

    base_url, alias_paths = load_tsconfig_paths(scanned_files)

    assert base_url == "web/src"
    assert alias_paths == {"@ui/*": "web/components/*"}


def test_normalize_js_import_path_supports_tsconfig_alias_and_base_url() -> None:
    """Resolves non-relative imports using tsconfig paths and baseUrl."""
    scanned_paths = {"web/src/lib.ts", "web/components/button.tsx"}

    assert (
        normalize_js_import_path(
            "web/src/app/page.tsx",
            "lib",
            scanned_paths,
            tsconfig_base_url="web/src",
        )
        == "web/src/lib.ts"
    )
    assert (
        normalize_js_import_path(
            "web/src/app/page.tsx",
            "@ui/button",
            scanned_paths,
            tsconfig_paths={"@ui/*": "web/components/*"},
        )
        == "web/components/button.tsx"
    )