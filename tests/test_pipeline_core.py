"""Pruebas básicas para componentes de ingestión y recuperación."""

from types import SimpleNamespace

from coderag.core.models import RetrievalChunk, ScannedFile
from coderag.ingestion.chunker import extract_symbol_chunks
from coderag.retrieval.context_assembler import assemble_context
import coderag.retrieval.hybrid_search as hybrid_search_module


class _FakeLexicalIndex:
    def __init__(self, results: list[dict]) -> None:
        self._results = results

    def query(self, repo_id: str, text: str, top_n: int = 50) -> list[dict]:
        del repo_id, text
        return self._results[:top_n]


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

    import coderag.ingestion.chunker as module

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

    import coderag.ingestion.chunker as module

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


def test_extract_symbol_chunks_config_keys_use_localized_spans() -> None:
    """Usa spans compactos alrededor de cada config key en YAML y JSON."""
    scanned = [
        ScannedFile(
            path="cfg/runtime.yaml",
            language="yaml",
            content=(
                "server:\n"
                "  port: 8000\n"
                "  host: localhost\n"
                "logging:\n"
                "  level: info\n"
            ),
        ),
        ScannedFile(
            path="cfg/runtime.json",
            language="json",
            content=(
                "{\n"
                '  "featureFlags": {\n'
                '    "retainWorkspaceAfterIngest": false\n'
                "  },\n"
                '  "maxContextTokens": 8000\n'
                "}\n"
            ),
        ),
    ]

    chunks = extract_symbol_chunks(repo_id="repo1", scanned_files=scanned)

    yaml_server = next(
        item
        for item in chunks
        if item.path == "cfg/runtime.yaml" and item.symbol_name == "server"
    )
    yaml_logging = next(
        item
        for item in chunks
        if item.path == "cfg/runtime.yaml" and item.symbol_name == "logging"
    )
    json_feature_flags = next(
        item
        for item in chunks
        if item.path == "cfg/runtime.json" and item.symbol_name == "featureFlags"
    )
    json_max_context = next(
        item
        for item in chunks
        if item.path == "cfg/runtime.json" and item.symbol_name == "maxContextTokens"
    )

    assert yaml_server.start_line == 1
    assert yaml_server.end_line == 3
    assert yaml_logging.start_line == 4
    assert yaml_logging.end_line == 5
    assert json_feature_flags.start_line == 2
    assert json_feature_flags.end_line == 4
    assert json_max_context.start_line == 5
    assert json_max_context.end_line == 5


def test_extract_symbol_chunks_detects_frontend_symbols_in_jsx_and_tsx() -> None:
    """Extrae componentes frontend y exports anónimos con nombres sintéticos útiles."""
    scanned = [
        ScannedFile(
            path="components/Button.jsx",
            language="javascript",
            content=(
                "export const Button = () => {\n"
                "  return <button>Click</button>;\n"
                "};\n"
            ),
        ),
        ScannedFile(
            path="app/page.tsx",
            language="typescript",
            content=(
                "export default () => {\n"
                "  return <main>Home</main>;\n"
                "};\n"
            ),
        ),
    ]

    chunks = extract_symbol_chunks(repo_id="repo1", scanned_files=scanned)
    pairs = {(item.path, item.symbol_name, item.symbol_type) for item in chunks}

    assert ("components/Button.jsx", "Button", "function") in pairs
    assert ("app/page.tsx", "Page", "function") in pairs


def test_extract_symbol_chunks_detects_next_route_handlers_by_http_verb() -> None:
    """Extrae handlers HTTP de Next route files como símbolos separados."""
    scanned = [
        ScannedFile(
            path="app/api/users/route.ts",
            language="typescript",
            content=(
                "export async function GET() {\n"
                "  return Response.json({ ok: true });\n"
                "}\n\n"
                "export async function POST() {\n"
                "  return Response.json({ created: true });\n"
                "}\n"
            ),
        )
    ]

    chunks = extract_symbol_chunks(repo_id="repo1", scanned_files=scanned)
    names = {item.symbol_name for item in chunks}

    assert "GET" in names
    assert "POST" in names


def test_hybrid_search_boosts_exact_config_key_over_test_chunks(
    monkeypatch,
) -> None:
    """Prioriza chunks de configuración reales ante tests para env vars exactas."""

    class _FakeEmbedder:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            del texts
            return []

    lexical_results = [
        {
            "id": "worker-code",
            "text": (
                "def _run_ingest_job(self, job_id, request):\n"
                "    _execute_ingest_job(job=job, request=request, workspace_path=self._workspace_path)"
            ),
            "score": 10.0,
            "metadata": {
                "path": "src/coderag/jobs/worker.py",
                "start_line": 418,
                "end_line": 426,
                "symbol_name": "_run_ingest_job",
                "symbol_type": "function",
            },
        },
        {
            "id": "test-settings",
            "text": (
                "class _Settings:\n"
                "    workspace_path = tmp_path / 'workspace'\n"
                "    ingestion_retry_transient_only = True"
            ),
            "score": 9.5,
            "metadata": {
                "path": "tests/test_job_manager_status.py",
                "start_line": 447,
                "end_line": 449,
                "symbol_name": "_Settings",
                "symbol_type": "class",
            },
        },
        {
            "id": "runtime-config",
            "text": (
                "WORKSPACE_PATH: /app/storage/workspace\n"
                "RETAIN_WORKSPACE_AFTER_INGEST: \"false\"\n"
                "MAX_CONTEXT_TOKENS: \"8000\""
            ),
            "score": 6.0,
            "metadata": {
                "path": "k8s/base/api-configmap.yaml",
                "start_line": 38,
                "end_line": 40,
                "symbol_name": "RETAIN_WORKSPACE_AFTER_INGEST",
                "symbol_type": "config_key",
            },
        },
    ]

    monkeypatch.setattr(hybrid_search_module, "EmbeddingClient", _FakeEmbedder)
    monkeypatch.setattr(
        hybrid_search_module,
        "get_settings",
        lambda: SimpleNamespace(postgres_host=""),
    )
    monkeypatch.setattr(
        hybrid_search_module,
        "build_repository_lexical_index",
        lambda settings: _FakeLexicalIndex(lexical_results),
    )
    monkeypatch.setattr(
        hybrid_search_module,
        "ensure_repository_lexical_index_loaded",
        lambda index, repo_id: None,
    )

    ranked = hybrid_search_module.hybrid_search(
        repo_id="repo1",
        query="RETAIN_WORKSPACE_AFTER_INGEST",
        top_n=5,
    )

    assert ranked
    assert ranked[0].metadata["path"] == "k8s/base/api-configmap.yaml"
    assert ranked[0].metadata["symbol_name"] == "RETAIN_WORKSPACE_AFTER_INGEST"


def test_hybrid_search_boosts_exact_code_symbol_over_generic_chunks(
    monkeypatch,
) -> None:
    """Prioriza un símbolo de código exacto frente a ruido de tests o config."""

    class _FakeEmbedder:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            del texts
            return []

    lexical_results = [
        {
            "id": "test-helper",
            "text": (
                "def build_context_fixture():\n"
                "    return {'parse': 'requirements'}"
            ),
            "score": 10.0,
            "metadata": {
                "path": "tests/test_dependency_index.py",
                "start_line": 12,
                "end_line": 13,
                "symbol_name": "build_context_fixture",
                "symbol_type": "function",
            },
        },
        {
            "id": "config-noise",
            "text": "PIP_INDEX_URL: https://mirror.example/internal",
            "score": 9.0,
            "metadata": {
                "path": "docker-compose.yml",
                "start_line": 20,
                "end_line": 20,
                "symbol_name": "PIP_INDEX_URL",
                "symbol_type": "config_key",
            },
        },
        {
            "id": "code-target",
            "text": (
                "def parse_requirements(path: str) -> list[str]:\n"
                "    return [line.strip() for line in path.read_text().splitlines()]"
            ),
            "score": 5.0,
            "metadata": {
                "path": "src/coderag/utils/dependencies.py",
                "start_line": 10,
                "end_line": 11,
                "symbol_name": "parse_requirements",
                "symbol_type": "function",
            },
        },
    ]

    monkeypatch.setattr(hybrid_search_module, "EmbeddingClient", _FakeEmbedder)
    monkeypatch.setattr(
        hybrid_search_module,
        "get_settings",
        lambda: SimpleNamespace(postgres_host=""),
    )
    monkeypatch.setattr(
        hybrid_search_module,
        "build_repository_lexical_index",
        lambda settings: _FakeLexicalIndex(lexical_results),
    )
    monkeypatch.setattr(
        hybrid_search_module,
        "ensure_repository_lexical_index_loaded",
        lambda index, repo_id: None,
    )

    ranked = hybrid_search_module.hybrid_search(
        repo_id="repo1",
        query="parse_requirements",
        top_n=5,
    )

    assert ranked
    assert ranked[0].metadata["path"] == "src/coderag/utils/dependencies.py"
    assert ranked[0].metadata["symbol_name"] == "parse_requirements"


def test_hybrid_search_boosts_identifier_inside_natural_query(
    monkeypatch,
) -> None:
    """Prioriza una config key cuando la consulta natural contiene su identificador."""

    class _FakeEmbedder:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            del texts
            return []

    lexical_results = [
        {
            "id": "workspace",
            "text": "WORKSPACE_PATH: /app/storage/workspace",
            "score": 10.0,
            "metadata": {
                "path": "docker-compose.yml",
                "start_line": 173,
                "end_line": 173,
                "symbol_name": "WORKSPACE_PATH",
                "symbol_type": "config_key",
            },
        },
        {
            "id": "retain",
            "text": "RETAIN_WORKSPACE_AFTER_INGEST: \"false\"",
            "score": 6.0,
            "metadata": {
                "path": "k8s/base/api-configmap.yaml",
                "start_line": 38,
                "end_line": 38,
                "symbol_name": "RETAIN_WORKSPACE_AFTER_INGEST",
                "symbol_type": "config_key",
            },
        },
    ]

    monkeypatch.setattr(hybrid_search_module, "EmbeddingClient", _FakeEmbedder)
    monkeypatch.setattr(
        hybrid_search_module,
        "get_settings",
        lambda: SimpleNamespace(postgres_host=""),
    )
    monkeypatch.setattr(
        hybrid_search_module,
        "build_repository_lexical_index",
        lambda settings: _FakeLexicalIndex(lexical_results),
    )
    monkeypatch.setattr(
        hybrid_search_module,
        "ensure_repository_lexical_index_loaded",
        lambda index, repo_id: None,
    )

    ranked = hybrid_search_module.hybrid_search(
        repo_id="repo1",
        query="where is RETAIN_WORKSPACE_AFTER_INGEST configured",
        top_n=5,
    )

    assert ranked
    assert ranked[0].metadata["path"] == "k8s/base/api-configmap.yaml"
    assert ranked[0].metadata["symbol_name"] == "RETAIN_WORKSPACE_AFTER_INGEST"


def test_hybrid_search_refreshes_stale_chroma_results_once(
    monkeypatch,
) -> None:
    """Reintenta Chroma tras reset cuando la primera ronda vectorial regresa vacía."""

    class _FakeEmbedder:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            del texts
            return [[0.1, 0.2]]

    class _FakeChroma:
        reset_calls = 0
        query_calls = 0

        def __init__(self) -> None:
            return None

        @classmethod
        def reset_shared_state(cls) -> None:
            cls.reset_calls += 1

        def query(
            self,
            collection_name: str,
            query_embedding: list[float],
            top_n: int,
            where: dict[str, str] | None = None,
        ) -> dict:
            del query_embedding, top_n, where
            self.__class__.query_calls += 1
            if self.__class__.query_calls <= len(hybrid_search_module.VECTOR_COLLECTIONS):
                return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
            return {
                "ids": [[f"{collection_name}-1"]],
                "documents": [["export function AuthProvider() {}"]],
                "metadatas": [[{
                    "path": "src/providers/AuthProvider.tsx",
                    "start_line": 1,
                    "end_line": 1,
                    "symbol_name": "AuthProvider",
                    "symbol_type": "function",
                }]],
                "distances": [[0.1]],
            }

    monkeypatch.setattr(hybrid_search_module, "EmbeddingClient", _FakeEmbedder)
    monkeypatch.setattr(
        hybrid_search_module,
        "build_managed_vector_index",
        lambda: _FakeChroma(),
    )
    monkeypatch.setattr(hybrid_search_module, "ChromaIndex", _FakeChroma)
    monkeypatch.setattr(
        hybrid_search_module,
        "get_settings",
        lambda: SimpleNamespace(postgres_host=""),
    )
    monkeypatch.setattr(
        hybrid_search_module,
        "repository_has_query_ready_lexical_data",
        lambda settings, repo_id: True,
    )
    monkeypatch.setattr(
        hybrid_search_module,
        "build_repository_lexical_index",
        lambda settings: _FakeLexicalIndex([]),
    )
    monkeypatch.setattr(
        hybrid_search_module,
        "ensure_repository_lexical_index_loaded",
        lambda index, repo_id: None,
    )

    ranked = hybrid_search_module.hybrid_search(
        repo_id="repo-refresh",
        query="where is auth provider",
        top_n=5,
    )

    assert _FakeChroma.reset_calls == 1
    assert ranked
    assert ranked[0].metadata["path"] == "src/providers/AuthProvider.tsx"


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


def test_assemble_context_formats_file_and_external_graph_records() -> None:
    """Renderiza dependencias de archivo y externas en bloques legibles."""
    chunks = [
        RetrievalChunk(
            id="1",
            text="def run():\n    return helper()",
            score=1.0,
            metadata={"path": "src/a.py", "start_line": 1, "end_line": 2},
        )
    ]
    graph_records = [
        {
            "seed": "sym-1",
            "labels": ["File"],
            "props": {
                "path": "src/deps.py",
                "language": "python",
                "module_path": "src",
            },
            "edge_count": 1,
            "relation_types": ["IMPORTS_FILE"],
        },
        {
            "seed": "sym-1",
            "labels": ["ExternalSymbol"],
            "props": {
                "ref": "requests",
                "language": "python",
                "source_path": "src/a.py",
            },
            "edge_count": 1,
            "relation_types": ["IMPORTS_EXTERNAL_FILE"],
        },
    ]

    context = assemble_context(chunks=chunks, graph_records=graph_records, max_tokens=400)

    assert "GRAPH_CONTEXT:" in context
    assert "GRAPH_FILE_DEPENDENCY" in context
    assert "PATH: src/deps.py" in context
    assert "RELATION_TYPES: IMPORTS_FILE" in context
    assert "GRAPH_EXTERNAL_DEPENDENCY" in context
    assert "REF: requests" in context
    assert "SOURCE_PATH: src/a.py" in context
