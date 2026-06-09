"""Pruebas del reranker heurístico basado en intención de consulta."""

from coderag.core.models import RetrievalChunk
from coderag.retrieval.reranker import rerank


def test_rerank_prioritizes_runtime_config_over_tests_for_natural_query() -> None:
    """Favorece configuración runtime frente a tests en consultas naturales."""
    chunks = [
        RetrievalChunk(
            id="test",
            text="class _Settings:\n    workspace_path = tmp_path / 'workspace'",
            score=0.82,
            metadata={
                "path": "tests/test_job_manager_status.py",
                "symbol_name": "_Settings",
                "symbol_type": "class",
                "start_line": 10,
                "end_line": 11,
            },
        ),
        RetrievalChunk(
            id="config",
            text=(
                "WORKSPACE_PATH: /app/storage/workspace\n"
                "RETAIN_WORKSPACE_AFTER_INGEST: \"false\""
            ),
            score=0.50,
            metadata={
                "path": "k8s/base/api-configmap.yaml",
                "symbol_name": "RETAIN_WORKSPACE_AFTER_INGEST",
                "symbol_type": "config_key",
                "start_line": 38,
                "end_line": 40,
            },
        ),
    ]

    ranked = rerank(
        query="where is workspace retention configured",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["path"] == "k8s/base/api-configmap.yaml"


def test_rerank_prioritizes_code_symbol_for_natural_code_query() -> None:
    """Favorece símbolos de código para consultas naturales de implementación."""
    chunks = [
        RetrievalChunk(
            id="config",
            text="QUERY_MAX_SECONDS: \"55\"",
            score=0.74,
            metadata={
                "path": "k8s/base/api-configmap.yaml",
                "symbol_name": "QUERY_MAX_SECONDS",
                "symbol_type": "config_key",
                "start_line": 40,
                "end_line": 40,
            },
        ),
        RetrievalChunk(
            id="code",
            text="def run_storage_preflight(context: str, force: bool = False):\n    return {}",
            score=0.63,
            metadata={
                "path": "src/coderag/core/storage_health.py",
                "symbol_name": "run_storage_preflight",
                "symbol_type": "function",
                "start_line": 281,
                "end_line": 282,
            },
        ),
    ]

    ranked = rerank(
        query="how is storage preflight executed",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["path"] == "src/coderag/core/storage_health.py"


def test_rerank_can_prioritize_tests_when_query_explicitly_requests_them() -> None:
    """Permite que tests suban cuando la intención explícita es test/fixture."""
    chunks = [
        RetrievalChunk(
            id="runtime",
            text="WORKSPACE_PATH: /app/storage/workspace",
            score=0.80,
            metadata={
                "path": "k8s/base/api-configmap.yaml",
                "symbol_name": "WORKSPACE_PATH",
                "symbol_type": "config_key",
                "start_line": 39,
                "end_line": 39,
            },
        ),
        RetrievalChunk(
            id="test",
            text="def test_run_query_literal_code_returns_live_file_content():\n    assert True",
            score=0.72,
            metadata={
                "path": "tests/test_query_service_modules.py",
                "symbol_name": "test_run_query_literal_code_returns_live_file_content",
                "symbol_type": "function",
                "start_line": 1031,
                "end_line": 1032,
            },
        ),
    ]

    ranked = rerank(
        query="which test validates literal mode workspace",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["path"] == "tests/test_query_service_modules.py"


def test_rerank_applies_diversity_by_path() -> None:
    """Evita que un mismo archivo monopolice el top cuando hay alternativas."""
    chunks = [
        RetrievalChunk(
            id="cfg-1",
            text="RETAIN_WORKSPACE_AFTER_INGEST: \"false\"",
            score=0.90,
            metadata={
                "path": "k8s/base/api-configmap.yaml",
                "symbol_name": "RETAIN_WORKSPACE_AFTER_INGEST",
                "symbol_type": "config_key",
                "start_line": 38,
                "end_line": 38,
            },
        ),
        RetrievalChunk(
            id="cfg-2",
            text="WORKSPACE_PATH: /app/storage/workspace",
            score=0.89,
            metadata={
                "path": "k8s/base/api-configmap.yaml",
                "symbol_name": "WORKSPACE_PATH",
                "symbol_type": "config_key",
                "start_line": 39,
                "end_line": 39,
            },
        ),
        RetrievalChunk(
            id="settings",
            text="retain_workspace_after_ingest: bool = Field(default=False)",
            score=0.81,
            metadata={
                "path": "src/coderag/core/settings.py",
                "symbol_name": "retain_workspace_after_ingest",
                "symbol_type": "field",
                "start_line": 177,
                "end_line": 179,
            },
        ),
    ]

    ranked = rerank(
        query="where is workspace retention configured",
        chunks=chunks,
        top_k=2,
    )

    returned_paths = [item.metadata["path"] for item in ranked]
    assert "k8s/base/api-configmap.yaml" in returned_paths
    assert "src/coderag/core/settings.py" in returned_paths


def test_rerank_uses_embedded_identifier_inside_natural_query() -> None:
    """Detecta identificadores exactos incrustados dentro de una consulta natural."""
    chunks = [
        RetrievalChunk(
            id="compose",
            text="WORKSPACE_PATH: /app/storage/workspace",
            score=0.86,
            metadata={
                "path": "docker-compose.yml",
                "symbol_name": "WORKSPACE_PATH",
                "symbol_type": "config_key",
                "start_line": 173,
                "end_line": 181,
            },
        ),
        RetrievalChunk(
            id="config",
            text="RETAIN_WORKSPACE_AFTER_INGEST: \"false\"",
            score=0.71,
            metadata={
                "path": "k8s/base/api-configmap.yaml",
                "symbol_name": "RETAIN_WORKSPACE_AFTER_INGEST",
                "symbol_type": "config_key",
                "start_line": 38,
                "end_line": 40,
            },
        ),
    ]

    ranked = rerank(
        query="where is RETAIN_WORKSPACE_AFTER_INGEST configured",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["symbol_name"] == "RETAIN_WORKSPACE_AFTER_INGEST"


def test_rerank_prefers_productive_implementation_over_tests_for_code_intent() -> None:
    """Prioriza implementación productiva frente a tests en consultas naturales de código."""
    chunks = [
        RetrievalChunk(
            id="test",
            text="def test_run_storage_preflight_behavior():\n    assert True",
            score=0.91,
            metadata={
                "path": "tests/test_storage_health.py",
                "symbol_name": "test_run_storage_preflight_behavior",
                "symbol_type": "function",
                "start_line": 10,
                "end_line": 11,
            },
        ),
        RetrievalChunk(
            id="prod",
            text="def run_storage_preflight(context: str, force: bool = False):\n    return {}",
            score=0.74,
            metadata={
                "path": "src/coderag/core/storage_health.py",
                "symbol_name": "run_storage_preflight",
                "symbol_type": "function",
                "start_line": 281,
                "end_line": 282,
            },
        ),
    ]

    ranked = rerank(
        query="how is storage preflight executed",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["path"] == "src/coderag/core/storage_health.py"


def test_rerank_prefers_direct_implementation_over_wrapper_for_execution_query() -> None:
    """Favorece el símbolo implementador frente a wrappers con llamadas indirectas."""
    chunks = [
        RetrievalChunk(
            id="wrapper",
            text=(
                "def storage_health():\n"
                "    report = run_storage_preflight(context='health', force=True)\n"
                "    return report"
            ),
            score=0.92,
            metadata={
                "path": "src/coderag/api/server.py",
                "symbol_name": "storage_health",
                "symbol_type": "function",
                "start_line": 513,
                "end_line": 517,
            },
        ),
        RetrievalChunk(
            id="implementation",
            text=(
                "def run_storage_preflight(context: str, force: bool = False):\n"
                "    return {'status': 'ok'}"
            ),
            score=0.73,
            metadata={
                "path": "src/coderag/core/storage_health.py",
                "symbol_name": "run_storage_preflight",
                "symbol_type": "function",
                "start_line": 281,
                "end_line": 282,
            },
        ),
    ]

    ranked = rerank(
        query="how is storage preflight executed",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["path"] == "src/coderag/core/storage_health.py"


def test_rerank_prefers_exact_definition_over_test_for_symbol_lookup() -> None:
    """Prioriza la definición exacta del símbolo frente a tests cercanos."""
    chunks = [
        RetrievalChunk(
            id="test",
            text=(
                "def test_run_query_uses_literal_mode():\n"
                "    result = run_query(query='demo')\n"
                "    assert result"
            ),
            score=0.96,
            metadata={
                "path": "tests/test_api.py",
                "symbol_name": "test_run_query_uses_literal_mode",
                "symbol_type": "function",
                "start_line": 10,
                "end_line": 12,
            },
        ),
        RetrievalChunk(
            id="definition",
            text="def run_query(request: dict) -> dict:\n    return {}",
            score=0.78,
            metadata={
                "path": "src/coderag/api/query_service.py",
                "symbol_name": "run_query",
                "symbol_type": "function",
                "start_line": 100,
                "end_line": 101,
            },
        ),
    ]

    ranked = rerank(
        query="where is run_query implemented",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["symbol_name"] == "run_query"


def test_rerank_prefers_exact_definition_over_api_wrapper_for_symbol_lookup() -> None:
    """Baja wrappers API cuando la query pide la definición exacta."""
    chunks = [
        RetrievalChunk(
            id="wrapper",
            text=(
                "def resolve_query():\n"
                "    return run_retrieval_query(request='demo')"
            ),
            score=0.95,
            metadata={
                "path": "src/coderag/api/server.py",
                "symbol_name": "resolve_query",
                "symbol_type": "function",
                "start_line": 40,
                "end_line": 41,
            },
        ),
        RetrievalChunk(
            id="definition",
            text=(
                "def run_retrieval_query(request: dict) -> dict:\n"
                "    return {}"
            ),
            score=0.76,
            metadata={
                "path": "src/coderag/api/query_service.py",
                "symbol_name": "run_retrieval_query",
                "symbol_type": "function",
                "start_line": 220,
                "end_line": 221,
            },
        ),
    ]

    ranked = rerank(
        query="where is run_retrieval_query implemented",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["symbol_name"] == "run_retrieval_query"


def test_rerank_prefers_documentation_over_runtime_config_for_docs_query() -> None:
    """Favorece docs cuando la query pide explícitamente documentación."""
    chunks = [
        RetrievalChunk(
            id="config",
            text="CHROMA_HOST=chromadb",
            score=0.93,
            metadata={
                "path": "docker-compose.yml",
                "symbol_name": "CHROMA_HOST",
                "symbol_type": "config_key",
                "start_line": 10,
                "end_line": 10,
            },
        ),
        RetrievalChunk(
            id="docs",
            text="CHROMA_HOST defines the Chroma service hostname.",
            score=0.72,
            metadata={
                "path": "docs/CONFIGURATION.md",
                "symbol_name": "CHROMA_HOST",
                "symbol_type": "section",
                "start_line": 80,
                "end_line": 82,
            },
        ),
    ]

    ranked = rerank(
        query="where is CHROMA_HOST documented",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["path"] == "docs/CONFIGURATION.md"


def test_rerank_prefers_runtime_config_over_docs_for_config_query() -> None:
    """Mantiene preferencia por config operativa cuando la query la pide."""
    chunks = [
        RetrievalChunk(
            id="docs",
            text="CHROMA_HOST defines the Chroma service hostname.",
            score=0.94,
            metadata={
                "path": "docs/CONFIGURATION.md",
                "symbol_name": "CHROMA_HOST",
                "symbol_type": "section",
                "start_line": 80,
                "end_line": 82,
            },
        ),
        RetrievalChunk(
            id="config",
            text="CHROMA_HOST=chromadb",
            score=0.73,
            metadata={
                "path": "docker-compose.yml",
                "symbol_name": "CHROMA_HOST",
                "symbol_type": "config_key",
                "start_line": 10,
                "end_line": 10,
            },
        ),
    ]

    ranked = rerank(
        query="where is CHROMA_HOST configured",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["path"] == "docker-compose.yml"


def test_rerank_prefers_documentation_section_over_exact_config_key_for_docs_query() -> None:
    """Sube la sección documental correcta aunque exista config_key exacta."""
    chunks = [
        RetrievalChunk(
            id="config",
            text='  QUERY_MAX_SECONDS: "55"',
            score=4.19,
            metadata={
                "path": "k8s/base/api-configmap.yaml",
                "symbol_name": "QUERY_MAX_SECONDS",
                "symbol_type": "config_key",
                "start_line": 55,
                "end_line": 55,
            },
        ),
        RetrievalChunk(
            id="docs",
            text=(
                "### Retrieval y limites de consulta\n\n"
                "- `CHROMA_MODE`: modo de acceso a Chroma (`remote`, `embedded`).\n"
                "- `QUERY_MAX_SECONDS`: limite global de latencia para query API."
            ),
            score=-0.36,
            metadata={
                "path": "docs/CONFIGURATION.md",
                "symbol_name": "Retrieval y limites de consulta",
                "symbol_type": "section",
                "start_line": 48,
                "end_line": 68,
            },
        ),
    ]

    ranked = rerank(
        query="donde esta documentado QUERY_MAX_SECONDS",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["path"] == "docs/CONFIGURATION.md"


def test_rerank_detects_spanish_documentation_intent_for_config_docs_query() -> None:
    """Reconoce `documentado` como intención documental y sube la doc correcta."""
    chunks = [
        RetrievalChunk(
            id="config",
            text="CHROMA_HOST=chromadb",
            score=0.93,
            metadata={
                "path": "docker-compose.yml",
                "symbol_name": "CHROMA_HOST",
                "symbol_type": "config_key",
                "start_line": 10,
                "end_line": 10,
            },
        ),
        RetrievalChunk(
            id="docs",
            text="CHROMA_HOST define el host remoto de Chroma.",
            score=0.72,
            metadata={
                "path": "docs/CONFIGURATION.md",
                "symbol_name": "CHROMA_HOST",
                "symbol_type": "section",
                "start_line": 80,
                "end_line": 82,
            },
        ),
    ]

    ranked = rerank(
        query="donde esta documentado CHROMA_HOST",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["path"] == "docs/CONFIGURATION.md"


def test_rerank_penalizes_prefixed_wrapper_symbol_for_lookup_query() -> None:
    """Baja wrappers prefijados aunque compartan sufijo con el target."""
    chunks = [
        RetrievalChunk(
            id="wrapper",
            text=(
                "def fake_run_query(**kwargs):\n"
                "    return run_query(**kwargs)"
            ),
            score=0.97,
            metadata={
                "path": "tests/test_api.py",
                "symbol_name": "fake_run_query",
                "symbol_type": "function",
                "start_line": 10,
                "end_line": 11,
            },
        ),
        RetrievalChunk(
            id="definition",
            text="def run_query(request, deps):\n    return {}",
            score=0.74,
            metadata={
                "path": "src/coderag/api/query_service.py",
                "symbol_name": "run_query",
                "symbol_type": "function",
                "start_line": 100,
                "end_line": 101,
            },
        ),
    ]

    ranked = rerank(
        query="donde esta run_query",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["symbol_name"] == "run_query"


def test_rerank_prefers_owner_file_context_when_symbol_chunk_is_missing() -> None:
    """Sube el archivo dueño si el chunk exacto no está dentro del candidato."""
    chunks = [
        RetrievalChunk(
            id="wrapper",
            text=(
                "def fake_run_retrieval_query(**kwargs):\n"
                "    return run_retrieval_query(**kwargs)"
            ),
            score=0.98,
            metadata={
                "path": "tests/test_api.py",
                "symbol_name": "fake_run_retrieval_query",
                "symbol_type": "function",
                "start_line": 10,
                "end_line": 11,
            },
        ),
        RetrievalChunk(
            id="owner-file",
            text=(
                '"""Orquestación"""\n\n'
                "def run_retrieval_query(repo_id, query, top_n, top_k):\n"
                "    return _build_retrieval_answer()"
            ),
            score=-0.05,
            metadata={
                "path": "src/coderag/api/query_service.py",
                "start_line": 1,
                "end_line": 1287,
            },
        ),
    ]

    ranked = rerank(
        query="donde esta run_retrieval_query",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["path"] == "src/coderag/api/query_service.py"


def test_rerank_penalizes_private_wrapper_prefix_for_literal_symbol_lookup() -> None:
    """Baja wrappers privados que solo delegan al símbolo real."""
    chunks = [
        RetrievalChunk(
            id="wrapper",
            text=(
                "def _resolve_literal_symbol_match(repo_id, query):\n"
                "    return resolve_literal_symbol_match(repo_id, query, hooks={})"
            ),
            score=0.94,
            metadata={
                "path": "src/coderag/api/query_service.py",
                "symbol_name": "_resolve_literal_symbol_match",
                "symbol_type": "function",
                "start_line": 10,
                "end_line": 11,
            },
        ),
        RetrievalChunk(
            id="definition",
            text=(
                "def resolve_literal_symbol_match(repo_id, query, *, hooks):\n"
                "    return (None, None, None, None, None, 'missing_symbol_hint')"
            ),
            score=0.78,
            metadata={
                "path": "src/coderag/api/literal_mode.py",
                "symbol_name": "resolve_literal_symbol_match",
                "symbol_type": "function",
                "start_line": 60,
                "end_line": 61,
            },
        ),
    ]

    ranked = rerank(
        query="donde esta resolve_literal_symbol_match",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["path"] == "src/coderag/api/literal_mode.py"


def test_rerank_prefers_owner_file_over_orchestration_full_file_for_run_query() -> None:
    """Baja archivos flow genéricos cuando compiten con el owner file del símbolo."""
    chunks = [
        RetrievalChunk(
            id="flow-file",
            text=(
                '"""Inventory query flow extracted from query service orchestration."""\n'
                "from time import monotonic"
            ),
            score=0.49,
            metadata={
                "path": "src/coderag/api/inventory_query_flow.py",
                "start_line": 1,
                "end_line": 179,
            },
        ),
        RetrievalChunk(
            id="owner-file",
            text='"""Orquestación de consultas de un extremo a otro para Hybrid RAG + GraphRAG."""',
            score=0.26,
            metadata={
                "path": "src/coderag/api/query_service.py",
                "start_line": 1,
                "end_line": 1287,
            },
        ),
    ]

    ranked = rerank(
        query="donde esta run_query",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["path"] == "src/coderag/api/query_service.py"


def test_rerank_prefers_runtime_owner_for_private_symbol_lookup() -> None:
    """Desempata símbolos privados a favor del owner no administrativo."""
    chunks = [
        RetrievalChunk(
            id="admin-copy",
            text=(
                "def _read_database_heads(factory):\n"
                '    """Lee las revisiones aplicadas hoy en la base activa."""\n'
                "    return set()"
            ),
            score=2.50,
            metadata={
                "path": "src/coderag/storage/postgres_schema_admin.py",
                "symbol_name": "_read_database_heads",
                "symbol_type": "function",
                "start_line": 71,
                "end_line": 80,
            },
        ),
        RetrievalChunk(
            id="runtime-owner",
            text=(
                "def _read_database_heads(factory):\n"
                '    """Lee las revisiones aplicadas actualmente en la base activa."""\n'
                "    return set()"
            ),
            score=2.49,
            metadata={
                "path": "src/coderag/storage/postgres_startup.py",
                "symbol_name": "_read_database_heads",
                "symbol_type": "function",
                "start_line": 92,
                "end_line": 99,
            },
        ),
    ]

    ranked = rerank(
        query="muestrame la implementacion de _read_database_heads",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["path"] == "src/coderag/storage/postgres_startup.py"


