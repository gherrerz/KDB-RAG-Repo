"""Pruebas del reranker heurístico basado en intención de consulta."""

from types import SimpleNamespace

import pytest

from coderag.core.models import RetrievalChunk
from coderag.retrieval import reranker
from coderag.retrieval.reranker import (
    _build_query_profile,
    _is_docs_path,
    _is_documentation_document,
    rerank,
)


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


def test_rerank_detects_context_intent_and_prioritizes_docs() -> None:
    """Sube documentación para consultas de contexto sin pedir config."""
    chunks = [
        RetrievalChunk(
            id="config",
            text="CHROMA_HOST=chromadb",
            score=0.91,
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
            text="CHROMA_HOST define el host remoto de Chroma para el runtime.",
            score=0.69,
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
        query="dame contexto del repositorio sobre CHROMA_HOST",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["path"] == "docs/CONFIGURATION.md"


def test_rerank_context_intent_prefers_productive_code_over_test_and_config() -> None:
    """En contexto general, prioriza código productivo sobre test y config."""
    chunks = [
        RetrievalChunk(
            id="config",
            text="QUERY_MAX_SECONDS: \"55\"",
            score=0.94,
            metadata={
                "path": "k8s/base/api-configmap.yaml",
                "symbol_name": "QUERY_MAX_SECONDS",
                "symbol_type": "config_key",
                "start_line": 40,
                "end_line": 40,
            },
        ),
        RetrievalChunk(
            id="test",
            text="def test_run_storage_preflight_behavior():\n    assert True",
            score=0.92,
            metadata={
                "path": "tests/test_storage_health.py",
                "symbol_name": "test_run_storage_preflight_behavior",
                "symbol_type": "function",
                "start_line": 10,
                "end_line": 11,
            },
        ),
        RetrievalChunk(
            id="docs",
            text=(
                "Guia de arquitectura del flujo de preflight y su contexto "
                "operativo."
            ),
            score=0.70,
            metadata={
                "path": "docs/ARCHITECTURE.md",
                "symbol_name": "Storage preflight architecture",
                "symbol_type": "section",
                "start_line": 20,
                "end_line": 24,
            },
        ),
        RetrievalChunk(
            id="code",
            text=(
                "def run_storage_preflight(context: str, force: bool = False):\n"
                "    return {}"
            ),
            score=0.67,
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
        query="dame contexto del repositorio sobre storage preflight",
        chunks=chunks,
        top_k=4,
    )

    top_paths = [str(item.metadata.get("path", "")) for item in ranked[:2]]
    assert "src/coderag/core/storage_health.py" in top_paths
    assert "docs/ARCHITECTURE.md" in top_paths
    assert ranked[0].metadata["path"] != "k8s/base/api-configmap.yaml"
    assert ranked[0].metadata["path"] != "tests/test_storage_health.py"


def test_rerank_detects_spanish_code_intent_and_prefers_tsx_over_markdown() -> None:
    """Una consulta en español sobre UI activa la intención de código."""
    query = "pantalla de inicio de sesion con formulario y ruta"
    chunks = [
        RetrievalChunk(
            id="notes",
            text="Apuntes sobre el flujo de acceso de usuarios.",
            score=0.62,
            metadata={
                "path": "notes/login-screen.md",
                "symbol_name": "Login screen",
                "symbol_type": "section",
                "start_line": 1,
                "end_line": 8,
            },
        ),
        RetrievalChunk(
            id="screen",
            text="export function LoginScreen() { return <form /> }",
            score=0.60,
            metadata={
                "path": "src/screens/LoginScreen.tsx",
                "symbol_name": "LoginScreen",
                "symbol_type": "function",
                "start_line": 3,
                "end_line": 9,
            },
        ),
    ]

    assert _build_query_profile(query).code_intent is True

    ranked = rerank(query=query, chunks=chunks, top_k=2)

    assert ranked[0].metadata["path"] == "src/screens/LoginScreen.tsx"


def test_rerank_spanish_docs_query_keeps_documentation_first() -> None:
    """Un término de UI en español no convierte una consulta de docs en código."""
    chunks = [
        RetrievalChunk(
            id="screen",
            text="export function LoginScreen() { return <form /> }",
            score=0.62,
            metadata={
                "path": "src/screens/LoginScreen.tsx",
                "symbol_name": "LoginScreen",
                "symbol_type": "function",
                "start_line": 3,
                "end_line": 9,
            },
        ),
        RetrievalChunk(
            id="docs",
            text="Guia de uso de la pantalla de inicio de sesion.",
            score=0.60,
            metadata={
                "path": "docs/pantallas.md",
                "symbol_name": "Pantalla de inicio de sesion",
                "symbol_type": "section",
                "start_line": 1,
                "end_line": 6,
            },
        ),
    ]

    ranked = rerank(
        query="documentacion de la pantalla de inicio de sesion",
        chunks=chunks,
        top_k=2,
    )

    assert ranked[0].metadata["path"] == "docs/pantallas.md"


def test_rerank_penalizes_openspec_markdown_like_docs_under_code_intent() -> None:
    """Las rutas openspec/ cuentan como documentación frente a código."""
    chunks = [
        RetrievalChunk(
            id="spec",
            text="Lineamientos generales del cambio.",
            score=0.66,
            metadata={
                "path": "openspec/changes/registro/design.md",
                "symbol_name": "Diseno",
                "symbol_type": "section",
                "start_line": 1,
                "end_line": 5,
            },
        ),
        RetrievalChunk(
            id="code",
            text="export const valor = 1;",
            score=0.60,
            metadata={
                "path": "src/registro/valor.ts",
                "symbol_name": "valor",
                "symbol_type": "file",
                "start_line": 1,
                "end_line": 1,
            },
        ),
    ]

    assert _is_docs_path("openspec/changes/registro/design.md") is True
    assert _is_docs_path("src/registro/valor.ts") is False

    ranked = rerank(query="funcion registrar usuario", chunks=chunks, top_k=2)

    assert ranked[0].metadata["path"] == "src/registro/valor.ts"


def _chunk(chunk_id: str, path: str, score: float) -> RetrievalChunk:
    """Crea un chunk mínimo con el score y la ruta indicados."""
    return RetrievalChunk(
        id=chunk_id,
        text="Registro de nuevos usuarios con verificacion.",
        score=score,
        metadata={
            "path": path,
            "symbol_name": "",
            "symbol_type": "file",
            "start_line": 1,
            "end_line": 5,
        },
    )


_NEUTRAL_QUERY = "registro de nuevos usuarios con verificacion"


def test_rerank_default_docs_penalty_puts_code_before_equal_docs() -> None:
    """Sin intención documental, código gana a docs con el mismo score."""
    chunks = [
        _chunk("spec", "openspec/specs/registro/spec.md", 0.60),
        _chunk("guide", "docs/registro.md", 0.60),
        _chunk("notes", "notas/registro.md", 0.60),
        _chunk("view", "src/routes/Registro.tsx", 0.60),
        _chunk("logic", "src/registro/verificar.ts", 0.60),
    ]
    profile = _build_query_profile(_NEUTRAL_QUERY)

    assert profile.prefers_docs is False
    assert profile.code_intent is False

    ranked = rerank(
        query=_NEUTRAL_QUERY,
        chunks=chunks,
        top_k=5,
        default_docs_penalty=0.40,
    )

    top_two = {item.metadata["path"] for item in ranked[:2]}
    assert top_two == {"src/routes/Registro.tsx", "src/registro/verificar.ts"}


def test_rerank_default_docs_penalty_keeps_docs_first_for_docs_query() -> None:
    """Con intención documental la penalización no se aplica."""
    chunks = [
        _chunk("view", "src/routes/Registro.tsx", 0.62),
        _chunk("guide", "docs/registro.md", 0.60),
    ]
    query = "documentacion del registro de nuevos usuarios"

    assert _build_query_profile(query).prefers_docs is True

    ranked = rerank(
        query=query,
        chunks=chunks,
        top_k=2,
        default_docs_penalty=0.40,
    )

    assert ranked[0].metadata["path"] == "docs/registro.md"


def test_rerank_zero_default_docs_penalty_restores_previous_order() -> None:
    """Con penalización 0 el orden es el previo y con 0.40 cambia."""
    chunks = [
        _chunk("spec", "openspec/specs/registro/spec.md", 0.66),
        _chunk("view", "src/routes/Registro.tsx", 0.60),
    ]

    disabled = rerank(
        query=_NEUTRAL_QUERY,
        chunks=[chunk.model_copy() for chunk in chunks],
        top_k=2,
        default_docs_penalty=0.0,
    )
    enabled = rerank(
        query=_NEUTRAL_QUERY,
        chunks=[chunk.model_copy() for chunk in chunks],
        top_k=2,
        default_docs_penalty=0.40,
    )

    assert disabled[0].metadata["path"] == "openspec/specs/registro/spec.md"
    assert enabled[0].metadata["path"] == "src/routes/Registro.tsx"


def test_rerank_reads_default_docs_penalty_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin argumento, el reranker usa RERANK_DEFAULT_DOCS_PENALTY."""
    chunks = [
        _chunk("spec", "openspec/specs/registro/spec.md", 0.66),
        _chunk("view", "src/routes/Registro.tsx", 0.60),
    ]

    monkeypatch.setattr(
        reranker,
        "get_settings",
        lambda: SimpleNamespace(rerank_default_docs_penalty=0.0),
    )
    disabled = rerank(
        query=_NEUTRAL_QUERY,
        chunks=[chunk.model_copy() for chunk in chunks],
        top_k=2,
    )

    monkeypatch.setattr(
        reranker,
        "get_settings",
        lambda: SimpleNamespace(rerank_default_docs_penalty=0.40),
    )
    enabled = rerank(
        query=_NEUTRAL_QUERY,
        chunks=[chunk.model_copy() for chunk in chunks],
        top_k=2,
    )

    assert disabled[0].metadata["path"] == "openspec/specs/registro/spec.md"
    assert enabled[0].metadata["path"] == "src/routes/Registro.tsx"


def test_is_documentation_document_covers_folders_and_prose_files() -> None:
    """Carpetas de docs y extensiones de prosa cuentan como documentos."""
    assert _is_documentation_document("docs/guia.md") is True
    assert _is_documentation_document("openspec/specs/x/spec.md") is True
    assert _is_documentation_document("odd/tasks/plan.md") is True
    assert _is_documentation_document("notas.rst") is True
    assert _is_documentation_document("src/routes/Registro.tsx") is False
    assert _is_documentation_document("requirements.txt") is False
