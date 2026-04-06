"""Pruebas básicas para componentes de ingestión y recuperación."""

from src.coderag.core.models import RetrievalChunk, ScannedFile
from src.coderag.ingestion.chunker import extract_symbol_chunks
from src.coderag.ingestion.index_bm25 import BM25Index, tokenize
from src.coderag.retrieval.context_assembler import assemble_context


def test_extract_symbol_chunks_java_class_method_constructor() -> None:
    """Extrae símbolos de clases, constructores y métodos de Java."""
    scanned = [
        ScannedFile(
            path="src/AuthService.java",
            language="java",
            content=(
                "public class AuthService {\n"
                "    public AuthService() { }\n"
                "    public String authenticate(String user) { return user; }\n"
                "}\n"
            ),
        )
    ]
    chunks = extract_symbol_chunks(repo_id="repo1", scanned_files=scanned)
    pairs = {(item.symbol_type, item.symbol_name) for item in chunks}
    assert ("class", "AuthService") in pairs
    assert ("constructor", "AuthService") in pairs
    assert ("method", "authenticate") in pairs


def test_extract_symbol_chunks_python_def_and_class() -> None:
    """Extrae símbolos de clases y funciones del contenido de Python."""
    scanned = [
        ScannedFile(
            path="app/main.py",
            language="python",
            content="class Service:\n    pass\n\n\ndef run():\n    return 1\n",
        )
    ]
    chunks = extract_symbol_chunks(repo_id="repo1", scanned_files=scanned)
    names = {item.symbol_name for item in chunks}
    assert "Service" in names
    assert "run" in names


def test_extract_symbol_chunks_python_long_symbol_uses_full_span(
    monkeypatch,
) -> None:
    """Con extractor v2 activo, conserva un símbolo Python completo (>30 líneas)."""

    class _Settings:
        symbol_extractor_v2_enabled = True

    import src.coderag.ingestion.chunker as module

    monkeypatch.setattr(module, "get_settings", lambda: _Settings())

    body = "\n".join([f"    value_{i} = {i}" for i in range(40)])
    scanned = [
        ScannedFile(
            path="app/long.py",
            language="python",
            content=f"def very_long():\n{body}\n    return value_0\n",
        )
    ]

    chunks = extract_symbol_chunks(repo_id="repo1", scanned_files=scanned)
    target = next(item for item in chunks if item.symbol_name == "very_long")
    assert target.end_line > 30


def test_extract_symbol_chunks_legacy_flag_keeps_windowed_span(
    monkeypatch,
) -> None:
    """Con extractor v2 desactivado, mantiene ventana legacy de ~30 líneas."""

    class _Settings:
        symbol_extractor_v2_enabled = False

    import src.coderag.ingestion.chunker as module

    monkeypatch.setattr(module, "get_settings", lambda: _Settings())

    body = "\n".join([f"    value_{i} = {i}" for i in range(40)])
    scanned = [
        ScannedFile(
            path="app/long.py",
            language="python",
            content=f"def very_long():\n{body}\n    return value_0\n",
        )
    ]

    chunks = extract_symbol_chunks(repo_id="repo1", scanned_files=scanned)
    target = next(item for item in chunks if item.symbol_name == "very_long")
    assert target.end_line <= 31


def test_extract_symbol_chunks_markdown_headings() -> None:
    """Extrae secciones de markdown cuando no hay símbolos de código tradicionales."""
    scanned = [
        ScannedFile(
            path="README.md",
            language="markdown",
            content="# Proyecto\n\n## Instalacion\n\nTexto\n",
        )
    ]
    chunks = extract_symbol_chunks(repo_id="repo1", scanned_files=scanned)
    names = {item.symbol_name for item in chunks}
    types = {item.symbol_type for item in chunks}
    assert "Proyecto" in names
    assert "Instalacion" in names
    assert "section" in types


def test_extract_symbol_chunks_config_keys_yaml_json_toml() -> None:
    """Extrae claves de configuración para yaml, json y toml."""
    scanned = [
        ScannedFile(
            path="cfg/app.yaml",
            language="yaml",
            content="server:\n  port: 8000\n",
        ),
        ScannedFile(
            path="cfg/app.json",
            language="json",
            content='{"name": "demo", "version": 1}',
        ),
        ScannedFile(
            path="cfg/app.toml",
            language="toml",
            content="title = \"demo\"\n[db]\nurl = \"x\"\n",
        ),
    ]

    chunks = extract_symbol_chunks(repo_id="repo1", scanned_files=scanned)
    names = {item.symbol_name for item in chunks}
    types = {item.symbol_type for item in chunks}
    assert "server" in names
    assert "name" in names
    assert "title" in names
    assert "config_key" in types


def test_bm25_returns_ranked_documents() -> None:
    """Devuelve el documento principal que coincide exactamente con los términos de la consulta."""
    index = BM25Index()
    index.build(
        repo_id="r1",
        docs=["def process_payment(order)", "class UserRepository"],
        metadatas=[{"id": "a"}, {"id": "b"}],
    )
    result = index.query(repo_id="r1", text="payment", top_n=1)
    assert result
    assert result[0]["id"] == "a"


def test_bm25_persist_and_load_roundtrip(
    monkeypatch,
    tmp_path,
) -> None:
    """Persiste y recarga BM25 para mantener capacidad tras reinicio."""
    index = BM25Index()

    class _Settings:
        workspace_path = tmp_path / "workspace"

    (_Settings.workspace_path).mkdir(parents=True, exist_ok=True)

    import src.coderag.ingestion.index_bm25 as module

    monkeypatch.setattr(module, "get_settings", lambda: _Settings())

    index.build(
        repo_id="r1",
        docs=["alpha beta", "gamma"],
        metadatas=[{"id": "a"}, {"id": "b"}],
    )
    assert index.persist_repo("r1") is True

    other = BM25Index()
    monkeypatch.setattr(module, "get_settings", lambda: _Settings())
    assert other.ensure_repo_loaded("r1") is True
    result = other.query(repo_id="r1", text="alpha", top_n=1)
    assert result
    assert result[0]["id"] == "a"


def test_tokenize_splits_identifiers_and_normalizes_accents() -> None:
    """Tokeniza camel/snake/kebab y normaliza acentos para matching estable."""
    tokens = tokenize("DependencyManager parse_requirements archivo-dependencias")

    assert "dependencymanager" in tokens
    assert "dependency" in tokens
    assert "manager" in tokens
    assert "parse_requirements" in tokens
    assert "parse" in tokens
    assert "requirements" in tokens
    assert "archivo-dependencias" in tokens
    assert "dependencias" in tokens


def test_bm25_query_expands_spanish_technical_terms() -> None:
    """Consulta en español recupera documentos en inglés vía expansión ES/EN genérica."""
    index = BM25Index()
    index.build(
        repo_id="r2",
        docs=[
            "Project dependencies are declared in requirements.txt",
            "Authentication service handles login",
        ],
        metadatas=[{"id": "dep"}, {"id": "auth"}],
    )

    result = index.query(repo_id="r2", text="dependencias del proyecto", top_n=1)
    assert result
    assert result[0]["id"] == "dep"


def test_assemble_context_applies_token_limit() -> None:
    """Trunca el contexto ensamblado al presupuesto de tokens configurado."""
    chunks = [
        RetrievalChunk(
            id="1",
            text="A" * 1000,
            score=1.0,
            metadata={"path": "a.py", "start_line": 1, "end_line": 2},
        )
    ]
    context = assemble_context(chunks=chunks, graph_records=[], max_tokens=30)
    assert len(context) <= 120
