"""Pruebas de prioridad de resolución provider/modelo en Settings."""

import base64
import json
from urllib.parse import unquote, urlsplit

import pytest

from coderag.core.settings import Settings


def _assert_postgres_dsn(
    dsn: str,
    *,
    host: str,
    port: int,
    db: str,
    user: str,
    password: str,
) -> None:
    """Valida componentes de la DSN sin dejar credenciales embebidas en un literal."""
    parsed = urlsplit(dsn)

    assert parsed.scheme == "postgresql"
    assert parsed.hostname == host
    assert parsed.port == port
    assert unquote(parsed.path.lstrip("/")) == db
    assert unquote(parsed.username or "") == user
    assert unquote(parsed.password or "") == password


def test_embedding_resolution_priority_override_over_env_and_legacy() -> None:
    """Aplica prioridad override > env nuevo para embeddings."""
    settings = Settings(
        EMBEDDING_PROVIDER="gemini",
        EMBEDDING_MODEL="env-embed-model",
    )

    provider = settings.resolve_embedding_provider("vertex")
    model = settings.resolve_embedding_model(provider, "override-embed-model")

    assert provider == "vertex"
    assert model == "override-embed-model"


def test_embedding_resolution_uses_new_env_before_legacy() -> None:
    """Sin override, usa env nuevo para resolver modelo de embeddings."""
    settings = Settings(
        EMBEDDING_PROVIDER="gemini",
        EMBEDDING_MODEL="env-embed-model",
    )

    provider = settings.resolve_embedding_provider(None)
    model = settings.resolve_embedding_model(provider, None)

    assert provider == "gemini"
    assert model == "env-embed-model"


def test_llm_resolution_priority_override_over_env_and_legacy() -> None:
    """Aplica prioridad override > env nuevo para LLM."""
    settings = Settings(
        LLM_PROVIDER="vertex",
        LLM_ANSWER_MODEL="env-answer-model",
        LLM_VERIFIER_MODEL="env-verifier-model",
    )

    provider = settings.resolve_llm_provider("gemini")
    answer_model = settings.resolve_answer_model(provider, "override-answer")
    verifier_model = settings.resolve_verifier_model(provider, "override-verifier")

    assert provider == "gemini"
    assert answer_model == "override-answer"
    assert verifier_model == "override-verifier"


def test_llm_resolution_uses_new_env_before_legacy() -> None:
    """Sin override, usa modelos LLM definidos por env nuevo."""
    settings = Settings(
        LLM_PROVIDER="vertex",
        LLM_ANSWER_MODEL="env-answer-model",
        LLM_VERIFIER_MODEL="env-verifier-model",
    )

    provider = settings.resolve_llm_provider(None)
    answer_model = settings.resolve_answer_model(provider, None)
    verifier_model = settings.resolve_verifier_model(provider, None)

    assert provider == "vertex"
    assert answer_model == "env-answer-model"
    assert verifier_model == "env-verifier-model"


def test_chroma_hnsw_space_defaults_to_cosine() -> None:
    """Usa cosine como espacio HNSW por defecto cuando no hay override."""
    settings = Settings(_env_file=None)

    assert settings.chroma_hnsw_space == "cosine"
    assert settings.resolve_chroma_hnsw_space() == "cosine"


def test_chroma_hnsw_space_accepts_l2_and_cosine() -> None:
    """Acepta solo valores soportados para CHROMA_HNSW_SPACE."""
    settings_l2 = Settings(CHROMA_HNSW_SPACE="l2", _env_file=None)
    settings_cos = Settings(CHROMA_HNSW_SPACE="cosine", _env_file=None)

    assert settings_l2.resolve_chroma_hnsw_space() == "l2"
    assert settings_cos.resolve_chroma_hnsw_space() == "cosine"


def test_chroma_hnsw_space_rejects_invalid_values() -> None:
    """Rechaza valores no soportados para CHROMA_HNSW_SPACE."""
    with pytest.raises(ValueError):
        Settings(CHROMA_HNSW_SPACE="ip", _env_file=None)


def test_chroma_remote_auth_accepts_token_only() -> None:
    """Permite autenticación bearer cuando no se configura Basic auth."""
    settings = Settings(CHROMA_TOKEN="secret-token", _env_file=None)

    assert settings.chroma_token == "secret-token"
    assert settings.chroma_username == ""
    assert settings.chroma_password == ""


def test_chroma_remote_auth_accepts_basic_only() -> None:
    """Permite autenticación Basic cuando no se configura token bearer."""
    settings = Settings(
        CHROMA_USERNAME="svc-user",
        CHROMA_PASSWORD="svc-pass",
        _env_file=None,
    )

    assert settings.chroma_token == ""
    assert settings.chroma_username == "svc-user"
    assert settings.chroma_password == "svc-pass"


def test_chroma_remote_auth_rejects_mixed_token_and_basic() -> None:
    """Rechaza configurar simultáneamente bearer token y Basic auth."""
    with pytest.raises(ValueError, match="mutuamente excluyente"):
        Settings(
            CHROMA_TOKEN="secret-token",
            CHROMA_USERNAME="svc-user",
            CHROMA_PASSWORD="svc-pass",
            _env_file=None,
        )


def test_chroma_remote_auth_rejects_username_without_password() -> None:
    """Rechaza usuario Basic sin password asociado."""
    with pytest.raises(ValueError, match="CHROMA_PASSWORD"):
        Settings(CHROMA_USERNAME="svc-user", _env_file=None)


def test_chroma_remote_auth_rejects_password_without_username() -> None:
    """Rechaza password Basic sin usuario asociado."""
    with pytest.raises(ValueError, match="CHROMA_USERNAME"):
        Settings(CHROMA_PASSWORD="svc-pass", _env_file=None)


def test_postgres_dsn_is_empty_without_host() -> None:
    """Deshabilita Postgres cuando no hay host configurado."""
    settings = Settings(_env_file=None)

    assert settings.resolve_postgres_dsn() == ""


def test_postgres_dsn_builds_from_separate_settings() -> None:
    """Compone la DSN Postgres desde host, puerto, db y credenciales."""
    settings = Settings(
        POSTGRES_HOST="localhost",
        POSTGRES_PORT=5432,
        POSTGRES_DB="coderag",
        POSTGRES_USER="coderag",
        POSTGRES_PASSWORD="coderag",
        _env_file=None,
    )

    _assert_postgres_dsn(
        settings.resolve_postgres_dsn(),
        host="localhost",
        port=5432,
        db="coderag",
        user="coderag",
        password="coderag",
    )


def test_postgres_dsn_uses_default_port_when_omitted() -> None:
    """Usa el puerto default de Postgres cuando no se configura override."""
    settings = Settings(
        POSTGRES_HOST="db.internal",
        POSTGRES_DB="catalog",
        POSTGRES_USER="svc",
        POSTGRES_PASSWORD="secret",
        _env_file=None,
    )

    _assert_postgres_dsn(
        settings.resolve_postgres_dsn(),
        host="db.internal",
        port=5432,
        db="catalog",
        user="svc",
        password="secret",
    )


def test_postgres_dsn_normalizes_whitespace_and_quotes_credentials() -> None:
    """Recorta espacios y escapa caracteres especiales en la DSN."""
    settings = Settings(
        POSTGRES_HOST=" localhost ",
        POSTGRES_DB=" repo db ",
        POSTGRES_USER=" svc user ",
        POSTGRES_PASSWORD=" p@ss word ",
        _env_file=None,
    )

    _assert_postgres_dsn(
        settings.resolve_postgres_dsn(),
        host="localhost",
        port=5432,
        db="repo db",
        user="svc user",
        password="p@ss word",
    )


def test_postgres_port_rejects_non_positive_values() -> None:
    """Valida que el puerto de Postgres sea positivo."""
    with pytest.raises(ValueError, match="POSTGRES_PORT"):
        Settings(POSTGRES_HOST="localhost", POSTGRES_PORT=0, _env_file=None)


def test_semantic_graph_java_flag_defaults_to_true() -> None:
    """Mantiene habilitada la extracción semántica Java por defecto."""
    settings = Settings(_env_file=None)

    assert settings.semantic_graph_java_enabled is True


def test_semantic_graph_typescript_flag_defaults_to_true() -> None:
    """Mantiene habilitada la extracción semántica TypeScript por defecto."""
    settings = Settings(_env_file=None)

    assert settings.semantic_graph_typescript_enabled is True


def test_semantic_graph_javascript_flag_defaults_to_true() -> None:
    """Mantiene habilitada la extracción semántica JavaScript por defecto."""
    settings = Settings(_env_file=None)

    assert settings.semantic_graph_javascript_enabled is True


def test_semantic_graph_query_flags_defaults() -> None:
    """Configura por defecto la expansión semántica de query habilitada."""
    settings = Settings(_env_file=None)

    assert settings.semantic_graph_query_enabled is True
    assert settings.semantic_graph_query_max_edges == 400
    assert settings.semantic_graph_query_max_nodes == 200
    assert settings.semantic_graph_query_max_ms == 120.0
    assert settings.semantic_graph_query_fallback_to_structural is True


def test_vertex_credentials_reference_prefers_base64() -> None:
    """Usa la credencial Base64 de Vertex como referencia efectiva."""
    payload = {"type": "service_account", "project_id": "demo"}
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")
    settings = Settings(
        VERTEX_SERVICE_ACCOUNT_JSON_B64=encoded,
        _env_file=None,
    )

    assert settings.resolve_vertex_credentials_reference() == encoded


def test_decode_vertex_service_account_b64_returns_dict() -> None:
    """Decodifica VERTEX_SERVICE_ACCOUNT_JSON_B64 a objeto JSON en runtime."""
    payload = {
        "type": "service_account",
        "project_id": "demo-project",
        "private_key_id": "key-id",
    }
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")
    settings = Settings(VERTEX_SERVICE_ACCOUNT_JSON_B64=encoded, _env_file=None)

    assert settings.decode_vertex_service_account_b64() == payload


def test_decode_vertex_service_account_b64_raises_on_invalid_value() -> None:
    """Informa error cuando VERTEX_SERVICE_ACCOUNT_JSON_B64 no es válido."""
    settings = Settings(VERTEX_SERVICE_ACCOUNT_JSON_B64="not-valid-b64", _env_file=None)

    with pytest.raises(ValueError):
        settings.decode_vertex_service_account_b64()


def test_resolve_vertex_project_id_prefers_service_account_payload() -> None:
    """Resuelve project_id desde el JSON Base64 antes que el fallback legacy."""
    payload = {"type": "service_account", "project_id": "demo-project"}
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")
    settings = Settings(
        VERTEX_SERVICE_ACCOUNT_JSON_B64=encoded,
        VERTEX_AI_PROJECT_ID="legacy-project",
        _env_file=None,
    )

    assert settings.resolve_vertex_project_id() == "demo-project"


def test_resolve_vertex_location_prefers_base_url() -> None:
    """Deriva location desde VERTEX_API_BASE_URL antes que el fallback legacy."""
    settings = Settings(
        VERTEX_API_BASE_URL="https://europe-west1-aiplatform.googleapis.com",
        VERTEX_AI_LOCATION="legacy-location",
        _env_file=None,
    )

    assert settings.resolve_vertex_location() == "europe-west1"


def test_resolve_semantic_relation_types_filters_invalid_and_duplicates() -> None:
    """Normaliza tipos válidos y elimina entradas inválidas/duplicadas."""
    settings = Settings(
        SEMANTIC_RELATION_TYPES="calls,IMPORTS,foo,implements,calls",
        _env_file=None,
    )

    assert settings.resolve_semantic_relation_types() == [
        "CALLS",
        "IMPORTS",
        "IMPLEMENTS",
    ]


def test_resolve_semantic_relation_weights_parses_and_falls_back() -> None:
    """Acepta pesos válidos y conserva defaults ante entradas inválidas."""
    settings = Settings(
        SEMANTIC_RELATION_WEIGHTS="CALLS:1.4,IMPORTS:abc,EXTENDS:1.2,foo:3,IMPLEMENTS:-1",
        _env_file=None,
    )

    weights = settings.resolve_semantic_relation_weights()

    assert weights["CALLS"] == 1.4
    assert weights["EXTENDS"] == 1.2
    assert weights["IMPORTS"] == 0.7
    assert weights["IMPLEMENTS"] == 1.0
