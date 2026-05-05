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
