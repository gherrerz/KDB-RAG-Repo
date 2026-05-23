from dataclasses import dataclass
import os
import uuid

import psycopg
from psycopg import conninfo

from coderag.storage.postgres_startup import ensure_postgres_schema_ready


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} debe estar definido para ejecutar validate_schemas.py"
        )
    return value


def _build_conninfo(*, database: str) -> str:
    return conninfo.make_conninfo(
        host=_required_env("POSTGRES_HOST"),
        port=_required_env("POSTGRES_PORT"),
        dbname=database,
        user=_required_env("POSTGRES_USER"),
        password=_required_env("POSTGRES_PASSWORD"),
    )


@dataclass
class ValidationSettings:
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    runtime_environment: str = "development"
    postgres_pool_size: int = 5
    postgres_pool_timeout: int = 30

    def resolve_postgres_startup_policy(self) -> str:
        return "validate" if self.runtime_environment == "production" else "auto_upgrade"


def create_temp_db(base_name: str) -> str:
    db_name = f"{base_name}_{uuid.uuid4().hex[:8]}"
    print(f"Creating database: {db_name}")
    admin_database = _required_env("POSTGRES_DB")
    with psycopg.connect(_build_conninfo(database=admin_database), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{db_name}"')
    return db_name


def setup_legacy_exact(db_name: str) -> None:
    print(f"Setting up legacy_exact schema in {db_name}")
    with psycopg.connect(_build_conninfo(database=db_name), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE repos (id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL)")
            cur.execute("CREATE TABLE jobs (id SERIAL PRIMARY KEY, repo_id INTEGER REFERENCES repos(id))")
            cur.execute("CREATE TABLE lexical_indices (id SERIAL PRIMARY KEY, repo_id INTEGER REFERENCES repos(id))")


def setup_legacy_incomplete(db_name: str) -> None:
    print(f"Setting up legacy_incomplete schema in {db_name}")
    with psycopg.connect(_build_conninfo(database=db_name), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE repos (id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL)")


def _build_settings(db_name: str) -> ValidationSettings:
    return ValidationSettings(
        postgres_host=_required_env("POSTGRES_HOST"),
        postgres_port=int(_required_env("POSTGRES_PORT")),
        postgres_db=db_name,
        postgres_user=_required_env("POSTGRES_USER"),
        postgres_password=_required_env("POSTGRES_PASSWORD"),
    )


def run_test() -> None:
    dbs = {
        "empty": create_temp_db("test_empty"),
        "legacy_exact": create_temp_db("test_legacy_exact"),
        "legacy_incomplete": create_temp_db("test_legacy_incomplete"),
    }

    setup_legacy_exact(dbs["legacy_exact"])
    setup_legacy_incomplete(dbs["legacy_incomplete"])

    results: dict[str, dict[str, object]] = {}
    for key, db_name in dbs.items():
        print(f"\n--- Testing base: {key} ({db_name}) ---")
        try:
            result = ensure_postgres_schema_ready(_build_settings(db_name))
            results[key] = {
                "db_name": db_name,
                "status": "success",
                "action": result.get("action", "unknown"),
                "policy": result.get("policy", "unknown"),
                "current_heads": result.get("current_heads", "unknown"),
                "expected_heads": result.get("expected_heads", "unknown"),
            }
        except Exception as exc:
            results[key] = {
                "db_name": db_name,
                "status": "exception",
                "error": str(exc),
            }
        print(results[key])

    print("\n--- Final Analysis ---")
    legacy_incomplete = results["legacy_incomplete"]
    if legacy_incomplete["status"] == "exception":
        print(
            "CONFIRMATION: legacy_incomplete failed as expected. "
            f"Error: {legacy_incomplete['error']}"
        )
    else:
        print(
            "CONFIRMATION: legacy_incomplete did NOT fail. "
            f"Result: {legacy_incomplete}"
        )


if __name__ == "__main__":
    run_test()
