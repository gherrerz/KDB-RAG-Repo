"""Pruebas unitarias para PostgresMetadataStore sobre SQLAlchemy."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from coderag.core.models import JobInfo, JobStatus
from coderag.storage.postgres_models import IngestionSnapshotRecord, JobRecord
from coderag.storage.postgres_metadata_store import PostgresMetadataStore


def _session_factory_mock(session: MagicMock) -> MagicMock:
    """Construye un session factory mock compatible con context manager."""
    factory = MagicMock()
    factory.get_session.return_value.__enter__.return_value = session
    factory.get_session.return_value.__exit__.return_value = False
    return factory


def _make_job(
    job_id: str = "job-1",
    status: JobStatus = JobStatus.queued,
    repo_id: str | None = "r1",
) -> JobInfo:
    return JobInfo(
        id=job_id,
        status=status,
        progress=0.25,
        logs=["Inicio"],
        repo_id=repo_id,
        error=None,
        diagnostics={"symbols": 3},
    )


def test_constructor_uses_provided_session_factory() -> None:
    """El store debe respetar un session factory inyectado."""
    session_factory = MagicMock()

    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=session_factory,
    )

    assert store._session_factory is session_factory


def test_constructor_builds_default_session_factory_when_missing() -> None:
    """Sin inyección explícita, el store crea su propio session factory."""
    with patch(
        "coderag.storage.postgres_metadata_store.PostgresSessionFactory"
    ) as factory_class:
        factory_instance = MagicMock()
        factory_class.return_value = factory_instance

        store = PostgresMetadataStore("postgresql://fake/db")

    factory_class.assert_called_once_with("postgresql://fake/db")
    assert store._session_factory is factory_instance


def test_upsert_job_executes_upsert_and_commits() -> None:
    """upsert_job debe ejecutar un upsert ORM y confirmar la transacción."""
    session = MagicMock()
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )
    job = _make_job()
    job.logs = ["Línea 1", "Línea 2"]

    store.upsert_job(job)

    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert compiled.params["id"] == job.id
    assert compiled.params["status"] == job.status.value
    assert compiled.params["logs"] == "Línea 1\nLínea 2"
    assert compiled.params["diagnostics"] == {"symbols": 3}
    session.commit.assert_called_once_with()


def test_recover_interrupted_jobs_returns_rowcount() -> None:
    """recover_interrupted_jobs debe propagar rowcount de la actualización."""
    session = MagicMock()
    session.execute.return_value = SimpleNamespace(rowcount=3)
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    count = store.recover_interrupted_jobs()

    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "tbl_repository_jobs" in str(compiled)
    assert count == 3
    session.commit.assert_called_once_with()


def test_get_job_returns_jobinfo_from_orm_record() -> None:
    """get_job debe hidratar un JobInfo a partir del modelo ORM."""
    session = MagicMock()
    session.get.return_value = JobRecord(
        id="job-1",
        status="queued",
        progress=0.5,
        logs="a\nb",
        repo_id="r1",
        error=None,
        diagnostics={"k": 1},
        created_at=datetime.datetime.now(datetime.UTC),
        updated_at=datetime.datetime.now(datetime.UTC),
    )
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    job = store.get_job("job-1")

    assert job is not None
    assert job.id == "job-1"
    assert job.logs == ["a", "b"]
    assert job.diagnostics == {"k": 1}


def test_get_job_tolerates_legacy_diagnostics_string() -> None:
    """get_job debe tolerar rows legacy con diagnostics serializado."""
    session = MagicMock()
    session.get.return_value = JobRecord(
        id="job-1",
        status="queued",
        progress=0.5,
        logs="a",
        repo_id="r1",
        error=None,
        diagnostics='{"legacy": true}',
        created_at=datetime.datetime.now(datetime.UTC),
        updated_at=datetime.datetime.now(datetime.UTC),
    )
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    job = store.get_job("job-1")

    assert job is not None
    assert job.diagnostics == {"legacy": True}


def test_list_repo_ids_filters_empty_values() -> None:
    """list_repo_ids debe omitir valores vacíos o nulos."""
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = [
        "r1",
        None,
        "",
        "r2",
    ]
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    repo_ids = store.list_repo_ids()

    assert repo_ids == ["r1", "r2"]


def test_list_repo_catalog_returns_expected_shape() -> None:
    """list_repo_catalog debe conservar el contrato de salida actual."""
    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = [
        {
            "repo_id": "r1",
            "organization": "org",
            "url": "https://example.com/repo.git",
            "branch": "main",
        }
    ]
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    catalog = store.list_repo_catalog()

    assert catalog == [
        {
            "repo_id": "r1",
            "organization": "org",
            "url": "https://example.com/repo.git",
            "branch": "main",
        }
    ]


def test_list_active_job_ids_supports_repo_filter() -> None:
    """list_active_job_ids debe poder filtrar por repo_id."""
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = ["j3"]
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    ids = store.list_active_job_ids(repo_id="r2")

    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "r2" in compiled.params.values()
    assert ids == ["j3"]


def test_upsert_repo_runtime_executes_upsert_and_commits() -> None:
    """upsert_repo_runtime debe ejecutar upsert y confirmar transacción."""
    session = MagicMock()
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    store.upsert_repo_runtime(
        repo_id="r1",
        organization="org",
        repo_url="https://example.com/repo.git",
        branch="main",
        local_path="/tmp/r1",
        embedding_provider="vertex",
        embedding_model="text-embedding-005",
    )

    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert compiled.params["id"] == "r1"
    assert compiled.params["organization"] == "org"
    assert compiled.params["embedding_provider"] == "vertex"
    session.commit.assert_called_once_with()


def test_record_ingest_snapshot_executes_insert_and_retention_delete() -> None:
    """record_ingest_snapshot inserta snapshot y aplica retención por repo."""
    session = MagicMock()
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    store.record_ingest_snapshot(
        repo_id="r1",
        job_id="job-1",
        job_status="completed",
        error_message=None,
        diagnostics={
            "retryable_error": False,
            "workspace_retained": True,
            "repo_size_mb": 1.23,
            "scan_stats": {"visited": 3, "scanned": 2},
            "coverage": {"chunks": 4, "languages": {"python": 2}},
            "vector_index": {
                "collections_written": 3,
                "documents_written": 9,
                "embedding_tokens_read_estimated": 321,
            },
            "semantic_graph": {
                "enabled": True,
                "status": "ok",
                "relation_counts": 5,
            },
            "ingest_mode": "incremental",
            "ingest_mode_reason": "git_diff",
            "base_commit": "aaaa1111",
            "head_commit": "bbbb2222",
            "changed_files_count": 1,
            "deleted_files_count": 0,
        },
        snapshot_at=datetime.datetime.now(datetime.UTC),
    )

    assert session.execute.call_count == 2
    insert_stmt = session.execute.call_args_list[0].args[0]
    compiled_insert = insert_stmt.compile(dialect=postgresql.dialect())
    assert compiled_insert.params["repo_id"] == "r1"
    assert compiled_insert.params["job_id"] == "job-1"
    assert compiled_insert.params["files_visited"] == 3
    assert compiled_insert.params["vector_collections_written"] == 3
    assert compiled_insert.params["repo_size_mb"] == 1.23
    assert compiled_insert.params["embedding_tokens_read_estimated"] == 321
    assert compiled_insert.params["ingest_mode"] == "incremental"
    assert compiled_insert.params["ingest_mode_reason"] == "git_diff"
    assert compiled_insert.params["base_commit"] == "aaaa1111"
    assert compiled_insert.params["head_commit"] == "bbbb2222"
    assert compiled_insert.params["changed_files_count"] == 1
    assert compiled_insert.params["deleted_files_count"] == 0

    delete_stmt = session.execute.call_args_list[1].args[0]
    compiled_delete = delete_stmt.compile(dialect=postgresql.dialect())
    assert IngestionSnapshotRecord.__tablename__ in str(compiled_delete)
    session.commit.assert_called_once_with()


def test_record_ingest_snapshot_tolerates_missing_incremental_fields() -> None:
    """Un job que falla antes de resolver el modo persiste None/0 sin excepción."""
    session = MagicMock()
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    store.record_ingest_snapshot(
        repo_id="r1",
        job_id="job-fail",
        job_status="failed",
        error_message="clone error",
        diagnostics={},
        snapshot_at=datetime.datetime.now(datetime.UTC),
    )

    insert_stmt = session.execute.call_args_list[0].args[0]
    compiled_insert = insert_stmt.compile(dialect=postgresql.dialect())
    assert compiled_insert.params["ingest_mode"] is None
    assert compiled_insert.params["ingest_mode_reason"] is None
    assert compiled_insert.params["base_commit"] is None
    assert compiled_insert.params["head_commit"] is None
    assert compiled_insert.params["changed_files_count"] == 0
    assert compiled_insert.params["deleted_files_count"] == 0


def test_list_repo_ingest_snapshots_returns_public_operational_shape() -> None:
    """list_repo_ingest_snapshots debe mapear el modelo ORM al contrato público."""
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = [
        IngestionSnapshotRecord(
            id=7,
            repo_id="r1",
            job_id="job-7",
            snapshot_at=datetime.datetime(2026, 5, 23, 12, 0, tzinfo=datetime.UTC),
            job_status="completed",
            error_message=None,
            retryable_error=False,
            workspace_retained=True,
            workspace_cleanup_attempted=False,
            workspace_cleanup_succeeded=False,
            clone_ms=1.5,
            scan_ms=2.5,
            chunk_ms=3.5,
            vector_total_ms=4.5,
            lexical_ms=5.5,
            graph_ms=6.5,
            readiness_ms=7.5,
            ingestion_total_ms=8.5,
            repo_size_mb=1.23,
            files_visited=10,
            files_scanned=9,
            excluded_dir_count=1,
            excluded_extension_count=2,
            excluded_file_count=3,
            excluded_size_count=4,
            excluded_decode_count=5,
            excluded_pattern_count=6,
            visited_dirs=7,
            pruned_dirs=8,
            symbols_count=11,
            chunks_count=12,
            languages_detected_count=2,
            vector_collections_written=3,
            vector_initial_batch_size=100,
            vector_effective_batch_size=50,
            vector_split_count=2,
            vector_recovered_retry_count=1,
            vector_payload_too_large_events=1,
            vector_proxy_reset_events=0,
            vector_upstream_restarting_events=0,
            vector_documents_written=42,
            embedding_tokens_read_estimated=321,
            semantic_enabled=True,
            semantic_status="ok",
            semantic_relations_count=5,
            semantic_unresolved_count=1,
            ingest_mode="incremental",
            ingest_mode_reason="git_diff",
            base_commit="aaaa1111",
            head_commit="bbbb2222",
            changed_files_count=1,
            deleted_files_count=0,
        )
    ]
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    result = store.list_repo_ingest_snapshots("r1", limit=5)

    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert compiled.params["repo_id_1"] == "r1"
    assert compiled.params["param_1"] == 5
    assert result == [
        {
            "snapshot_id": 7,
            "repo_id": "r1",
            "job_id": "job-7",
            "snapshot_at": datetime.datetime(
                2026,
                5,
                23,
                12,
                0,
                tzinfo=datetime.UTC,
            ),
            "job_status": "completed",
            "error_message": None,
            "retryable_error": False,
            "workspace_retained": True,
            "workspace_cleanup_attempted": False,
            "workspace_cleanup_succeeded": False,
            "clone_ms": 1.5,
            "scan_ms": 2.5,
            "chunk_ms": 3.5,
            "vector_total_ms": 4.5,
            "lexical_ms": 5.5,
            "graph_ms": 6.5,
            "readiness_ms": 7.5,
            "ingestion_total_ms": 8.5,
            "repo_size_mb": 1.23,
            "files_visited": 10,
            "files_scanned": 9,
            "excluded_dir_count": 1,
            "excluded_extension_count": 2,
            "excluded_file_count": 3,
            "excluded_size_count": 4,
            "excluded_decode_count": 5,
            "excluded_pattern_count": 6,
            "visited_dirs": 7,
            "pruned_dirs": 8,
            "symbols_count": 11,
            "chunks_count": 12,
            "languages_detected_count": 2,
            "vector_collections_written": 3,
            "vector_initial_batch_size": 100,
            "vector_effective_batch_size": 50,
            "vector_split_count": 2,
            "vector_recovered_retry_count": 1,
            "vector_payload_too_large_events": 1,
            "vector_proxy_reset_events": 0,
            "vector_upstream_restarting_events": 0,
            "vector_documents_written": 42,
            "embedding_tokens_read_estimated": 321,
            "semantic_enabled": True,
            "semantic_status": "ok",
            "semantic_relations_count": 5,
            "semantic_unresolved_count": 1,
            "ingest_mode": "incremental",
            "ingest_mode_reason": "git_diff",
            "base_commit": "aaaa1111",
            "head_commit": "bbbb2222",
            "changed_files_count": 1,
            "deleted_files_count": 0,
        }
    ]


def test_delete_repo_ingest_snapshots_returns_deleted_rows() -> None:
    """delete_repo_ingest_snapshots debe propagar el rowcount del delete."""
    session = MagicMock()
    session.execute.return_value = SimpleNamespace(rowcount=3)
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    deleted = store.delete_repo_ingest_snapshots("r1")

    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert IngestionSnapshotRecord.__tablename__ in str(compiled)
    assert compiled.params["repo_id_1"] == "r1"
    assert deleted == 3
    session.commit.assert_called_once_with()


def test_get_repo_runtime_returns_expected_keys() -> None:
    """get_repo_runtime debe preservar las claves públicas actuales."""
    session = MagicMock()
    session.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "embedding_provider": "vertex",
        "embedding_model": "te-005",
        "last_queried_at": datetime.datetime(
            2026,
            5,
            23,
            12,
            0,
            tzinfo=datetime.UTC,
        ),
        "last_indexed_commit": "commit-aaa",
    }
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    result = store.get_repo_runtime("r1")

    assert result == {
        "last_embedding_provider": "vertex",
        "last_embedding_model": "te-005",
        "last_queried_at": "2026-05-23T12:00:00+00:00",
        "last_indexed_commit": "commit-aaa",
    }


def test_get_repo_runtime_normalizes_legacy_timestamp_strings() -> None:
    """get_repo_runtime debe tolerar timestamps string con offset abreviado."""
    session = MagicMock()
    session.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "embedding_provider": "vertex",
        "embedding_model": "te-005",
        "last_queried_at": "2026-05-23 12:00:00.12345+00",
        "last_indexed_commit": None,
    }
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    result = store.get_repo_runtime("r1")

    assert result == {
        "last_embedding_provider": "vertex",
        "last_embedding_model": "te-005",
        "last_queried_at": "2026-05-23T12:00:00.123450+00:00",
        "last_indexed_commit": None,
    }


def test_touch_repo_last_queried_at_updates_timestamp_and_commits() -> None:
    """touch_repo_last_queried_at debe ejecutar update y confirmar transacción."""
    session = MagicMock()
    session.execute.return_value = SimpleNamespace(rowcount=1)
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    updated = store.touch_repo_last_queried_at("r1")

    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert compiled.params["id_1"] == "r1"
    assert updated == 1
    session.commit.assert_called_once_with()


def test_list_stale_repos_returns_runtime_shape() -> None:
    """list_stale_repos debe devolver el detalle runtime esperado."""
    session = MagicMock()
    cutoff = datetime.datetime(2026, 5, 23, 0, 0, tzinfo=datetime.UTC)
    session.execute.return_value.mappings.return_value.all.return_value = [
        {
            "repo_id": "r1",
            "organization": "org",
            "url": "https://example.com/repo.git",
            "branch": "main",
            "local_path": "/tmp/r1",
            "created_at": datetime.datetime(2026, 5, 1, tzinfo=datetime.UTC),
            "updated_at": datetime.datetime(2026, 5, 2, tzinfo=datetime.UTC),
            "last_queried_at": None,
        }
    ]
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    result = store.list_stale_repos(last_queried_on_or_before=cutoff)

    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert cutoff in compiled.params.values()
    assert result == [
        {
            "repo_id": "r1",
            "organization": "org",
            "url": "https://example.com/repo.git",
            "branch": "main",
            "local_path": "/tmp/r1",
            "created_at": datetime.datetime(2026, 5, 1, tzinfo=datetime.UTC),
            "updated_at": datetime.datetime(2026, 5, 2, tzinfo=datetime.UTC),
            "last_queried_at": None,
        }
    ]


def test_list_stale_repos_normalizes_legacy_timestamp_strings() -> None:
    """list_stale_repos debe normalizar timestamps string legados."""
    session = MagicMock()
    cutoff = datetime.datetime(2026, 5, 23, 0, 0, tzinfo=datetime.UTC)
    session.execute.return_value.mappings.return_value.all.return_value = [
        {
            "repo_id": "r1",
            "organization": "org",
            "url": "https://example.com/repo.git",
            "branch": "main",
            "local_path": "/tmp/r1",
            "created_at": "2026-05-01 00:00:00+00",
            "updated_at": "2026-05-02 00:00:00+00",
            "last_queried_at": "2026-05-03 00:00:00+00",
        }
    ]
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    result = store.list_stale_repos(last_queried_on_or_before=cutoff)

    assert result == [
        {
            "repo_id": "r1",
            "organization": "org",
            "url": "https://example.com/repo.git",
            "branch": "main",
            "local_path": "/tmp/r1",
            "created_at": datetime.datetime(2026, 5, 1, tzinfo=datetime.UTC),
            "updated_at": datetime.datetime(2026, 5, 2, tzinfo=datetime.UTC),
            "last_queried_at": datetime.datetime(
                2026,
                5,
                3,
                tzinfo=datetime.UTC,
            ),
        }
    ]


def test_delete_repo_data_returns_aggregated_counts() -> None:
    """delete_repo_data debe agregar correctamente los conteos borrados."""
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(MagicMock()),
    )
    store.delete_repo_ingest_snapshots = MagicMock(return_value=2)
    store.delete_repo_jobs = MagicMock(return_value=4)
    store.delete_repo_runtime = MagicMock(return_value=1)

    result = store.delete_repo_data("r1")

    assert result == {
        "snapshots_deleted": 2,
        "jobs_deleted": 4,
        "repos_deleted": 1,
        "total": 7,
    }


def test_reset_all_executes_delete_on_both_tables_and_commits() -> None:
    """reset_all debe borrar snapshots, jobs y repos dentro de una transacción."""
    session = MagicMock()
    store = PostgresMetadataStore(
        "postgresql://fake/db",
        session_factory=_session_factory_mock(session),
    )

    store.reset_all()

    assert session.execute.call_count == 3
    session.commit.assert_called_once_with()