"""Pruebas para filtros de escaneo del repositorio en la ingesta."""

from pathlib import Path

from coderag.ingestion.repo_scanner import (
    detect_language,
    scan_repository,
    scan_repository_with_stats,
)


def test_detect_language_maps_frontend_extensions() -> None:
    """Clasifica JSX y TSX como código ECMAScript en lugar de text."""
    assert detect_language(Path("component.jsx")) == "javascript"
    assert detect_language(Path("component.tsx")) == "typescript"
    assert detect_language(Path("component.js")) == "javascript"
    assert detect_language(Path("component.ts")) == "typescript"


def test_scan_repository_excludes_dirs_extensions_and_large_files(tmp_path: Path) -> None:
    """Excluye carpetas, extensiones binarias y archivos que superan el límite."""
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "node_modules").mkdir(parents=True)
    (tmp_path / "dist").mkdir(parents=True)

    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("documentación útil\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("version: 1\n", encoding="utf-8")

    (tmp_path / "src" / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "node_modules" / "lib.js").write_text("console.log(1);\n", encoding="utf-8")
    (tmp_path / "dist" / "bundle.js").write_text("console.log(2);\n", encoding="utf-8")

    large_file = tmp_path / "src" / "big.txt"
    large_file.write_text("x" * 500, encoding="utf-8")

    scanned = scan_repository(
        tmp_path,
        max_file_size=200,
        excluded_dirs={"node_modules", "dist"},
        excluded_extensions={".png"},
    )
    scanned_paths = {item.path for item in scanned}

    assert "src/app.py" in scanned_paths
    assert "README.md" in scanned_paths
    assert "config.yaml" in scanned_paths
    assert "src/image.png" not in scanned_paths
    assert "node_modules/lib.js" not in scanned_paths
    assert "dist/bundle.js" not in scanned_paths
    assert "src/big.txt" not in scanned_paths


def test_scan_repository_allows_custom_exclusion_sets(tmp_path: Path) -> None:
    """Permite sobrescribir directorios y extensiones excluidas por llamada."""
    (tmp_path / "vendor").mkdir(parents=True)
    (tmp_path / "src").mkdir(parents=True)

    (tmp_path / "vendor" / "lib.ts").write_text("export const x = 1;\n", encoding="utf-8")
    (tmp_path / "src" / "notes.lock").write_text("lock\n", encoding="utf-8")
    (tmp_path / "src" / "ok.py").write_text("print('ok')\n", encoding="utf-8")

    scanned = scan_repository(
        tmp_path,
        max_file_size=100_000,
        excluded_dirs={"vendor"},
        excluded_extensions={".lock"},
    )
    scanned_paths = {item.path for item in scanned}

    assert "src/ok.py" in scanned_paths
    assert "vendor/lib.ts" not in scanned_paths
    assert "src/notes.lock" not in scanned_paths


def test_scan_repository_excludes_specific_files_list(tmp_path: Path) -> None:
    """Excluye archivos por nombre y por ruta relativa según lista explícita."""
    (tmp_path / "src").mkdir(parents=True)

    (tmp_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    (tmp_path / ".env").write_text("DEBUG=true\n", encoding="utf-8")
    (tmp_path / "src" / "secret.txt").write_text("top-secret\n", encoding="utf-8")
    (tmp_path / "src" / "ok.py").write_text("print('ok')\n", encoding="utf-8")

    scanned = scan_repository(
        tmp_path,
        max_file_size=100_000,
        excluded_dirs=set(),
        excluded_extensions=set(),
        excluded_files={".gitignore", ".env", "src/secret.txt"},
    )
    scanned_paths = {item.path for item in scanned}

    assert ".gitignore" not in scanned_paths
    assert ".env" not in scanned_paths
    assert "src/secret.txt" not in scanned_paths
    assert "src/ok.py" in scanned_paths


def test_scan_repository_with_stats_reports_exclusion_reasons(tmp_path: Path) -> None:
    """Expone contadores por causa de exclusión para observabilidad de ingesta."""
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "skipdir").mkdir(parents=True)
    (tmp_path / "skipdir" / "x.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "src" / "ok.py").write_text("print(2)\n", encoding="utf-8")
    (tmp_path / "src" / "blob.bin").write_bytes(b"123")
    (tmp_path / "src" / "large.txt").write_text("x" * 400, encoding="utf-8")

    scanned, stats = scan_repository_with_stats(
        tmp_path,
        max_file_size=200,
        excluded_dirs={"skipdir"},
        excluded_extensions={".bin"},
        excluded_files=set(),
    )

    scanned_paths = {item.path for item in scanned}
    assert "src/ok.py" in scanned_paths
    assert stats["visited"] >= 4
    assert stats["excluded_dir"] >= 1
    assert stats["excluded_extension"] >= 1
    assert stats["excluded_size"] >= 1


def test_scan_repository_preserves_jsx_and_tsx_languages(tmp_path: Path) -> None:
    """Escanea archivos frontend con etiquetas de lenguaje correctas."""
    (tmp_path / "app").mkdir(parents=True)
    (tmp_path / "app" / "page.tsx").write_text(
        "export default function Page() { return <div />; }\n",
        encoding="utf-8",
    )
    (tmp_path / "components").mkdir(parents=True)
    (tmp_path / "components" / "button.jsx").write_text(
        "export const Button = () => <button />;\n",
        encoding="utf-8",
    )

    scanned = scan_repository(
        tmp_path,
        max_file_size=100_000,
        excluded_dirs=set(),
        excluded_extensions=set(),
        excluded_files=set(),
    )

    language_by_path = {item.path: item.language for item in scanned}

    assert language_by_path["app/page.tsx"] == "typescript"
    assert language_by_path["components/button.jsx"] == "javascript"
