"""Pruebas de resolución por entorno de URLs y credenciales de infraestructura.

Verifica la convención de sufijo ``{VAR}_{SUFIJO}`` -> ``{VAR}`` -> default,
gobernada por ``RUNTIME_ENVIRONMENT`` (development/test/production), aplicada a
Chroma, Postgres, Neo4j y Redis (endpoints y credenciales).
"""

from urllib.parse import unquote, urlsplit

import pytest

from coderag.core.settings import Settings


def test_scoped_variant_wins_over_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """La variante del entorno activo prevalece sobre la variable base."""
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "test")
    monkeypatch.setenv("CHROMA_HOST", "base-host")
    monkeypatch.setenv("CHROMA_HOST_TEST", "qa-host")

    assert Settings().chroma_host == "qa-host"


def test_fallback_to_base_when_no_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin variante por entorno se usa la base (compatibilidad)."""
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "production")
    monkeypatch.setenv("CHROMA_HOST", "base-host")
    monkeypatch.delenv("CHROMA_HOST_PROD", raising=False)

    assert Settings().chroma_host == "base-host"


def test_credentials_are_scoped_per_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Usuario/password/token también resuelven la variante por entorno."""
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "production")
    monkeypatch.setenv("POSTGRES_HOST_PROD", "pg-prod")
    monkeypatch.setenv("POSTGRES_USER_PROD", "prod-user")
    monkeypatch.setenv("POSTGRES_PASSWORD_PROD", "prod-pass")
    monkeypatch.setenv("POSTGRES_DB_PROD", "prod-db")
    monkeypatch.setenv("NEO4J_PASSWORD_PROD", "prod-neo-pass")
    monkeypatch.setenv("REDIS_URL_PROD", "redis://redis-prod:6379/0")

    settings = Settings()

    parsed = urlsplit(settings.resolve_postgres_dsn())
    assert parsed.hostname == "pg-prod"
    assert unquote(parsed.username or "") == "prod-user"
    assert unquote(parsed.password or "") == "prod-pass"
    assert unquote(parsed.path.lstrip("/")) == "prod-db"
    assert settings.neo4j_password == "prod-neo-pass"
    assert settings.redis_url == "redis://redis-prod:6379/0"


def test_environment_switch_changes_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cambiar RUNTIME_ENVIRONMENT selecciona el set de variantes acorde."""
    monkeypatch.setenv("NEO4J_URI_TEST", "bolt://neo4j-qa:7687")
    monkeypatch.setenv("NEO4J_URI_PROD", "bolt://neo4j-prod:7687")

    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "test")
    assert Settings().neo4j_uri == "bolt://neo4j-qa:7687"

    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "production")
    assert Settings().neo4j_uri == "bolt://neo4j-prod:7687"


def test_non_infra_variables_are_not_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parámetros no-infra ignoran el sufijo (permanecen globales)."""
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "test")
    monkeypatch.setenv("POSTGRES_POOL_SIZE", "7")
    monkeypatch.setenv("POSTGRES_POOL_SIZE_TEST", "99")

    assert Settings().postgres_pool_size == 7
