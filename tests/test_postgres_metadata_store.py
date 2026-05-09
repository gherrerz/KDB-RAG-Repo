"""Pruebas unitarias para PostgresMetadataStore.

No requieren base de datos real: psycopg.connect se parchea con MagicMock.
Si psycopg no está disponible en el entorno de dev se inyecta un stub en
sys.modules antes de cualquier importación del módulo bajo prueba.
"""

from __future__ import annotations

import datetime
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub psycopg en sys.modules si no está instalado en el entorno de dev
# ---------------------------------------------------------------------------
if "psycopg" not in sys.modules:
    _psycopg_stub = MagicMock()
    _psycopg_rows_stub = MagicMock()
    _psycopg_rows_stub.dict_row = MagicMock()
    _psycopg_stub.rows = _psycopg_rows_stub
    sys.modules["psycopg"] = _psycopg_stub
    sys.modules["psycopg.rows"] = _psycopg_rows_stub
    sys.modules["psycopg.rows.dict_row"] = _psycopg_rows_stub.dict_row

from coderag.core.models import JobInfo, JobStatus


# ---------------------------------------------------------------------------
# Helpers de mocks
# ---------------------------------------------------------------------------

_PATCH_CONNECT = "coderag.storage.postgres_metadata_store.psycopg.connect"


def _cursor(rows=None, rowcount: int = 0) -> MagicMock:
    c = MagicMock()
    c.fetchall.return_value = list(rows or [])
    c.fetchone.return_value = (rows[0] if rows else None)
    c.rowcount = rowcount
    return c


def _conn(cursor: MagicMock | None = None) -> MagicMock:
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    if cursor is not None:
        conn.execute.return_value = cursor
        conn.executemany.return_value = cursor
    return conn


def _make_store():
    """Crea PostgresMetadataStore con psycopg parcheado."""
    from coderag.storage.postgres_metadata_store import PostgresMetadataStore

    init_conn = _conn(_cursor())
    with patch(_PATCH_CONNECT, return_value=init_conn):
        return PostgresMetadataStore("postgresql://fake/db")


def _make_job(
    job_id: str = "job-1",
    status: JobStatus = JobStatus.queued,
    repo_id: str | None = "r1",
) -> JobInfo:
    return JobInfo(
        id=job_id,
        status=status,
        progress=0.0,
        logs=["Inicio"],
        repo_id=repo_id,
        error=None,
        diagnostics={},
    )


# ===========================================================================
# _init_schema
# ===========================================================================


class TestInitSchema:
    def test_crea_tablas_jobs_y_repos(self):
        """_init_schema ejecuta CREATE TABLE para jobs y repos."""
        init_conn = _conn(_cursor())
        from coderag.storage.postgres_metadata_store import PostgresMetadataStore
        with patch(_PATCH_CONNECT, return_value=init_conn):
            PostgresMetadataStore("postgresql://fake/db")

        calls_sql = [str(c.args[0]) for c in init_conn.execute.call_args_list]
        assert any("CREATE TABLE IF NOT EXISTS jobs" in s for s in calls_sql)
        assert any("CREATE TABLE IF NOT EXISTS repos" in s for s in calls_sql)


# ===========================================================================
# upsert_job
# ===========================================================================


class TestUpsertJob:
    def test_llama_execute_con_todos_los_campos(self):
        """upsert_job construye INSERT con los 9 campos del job."""
        store = _make_store()
        test_conn = _conn(_cursor())
        job = _make_job()

        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.upsert_job(job)

        test_conn.execute.assert_called_once()
        sql, params = test_conn.execute.call_args.args
        assert "INSERT INTO jobs" in str(sql)
        assert job.id in params
        assert job.status.value in params

    def test_logs_se_unen_con_salto_de_linea(self):
        """Los logs se serializan uniendo con '\\n' como separador."""
        store = _make_store()
        test_conn = _conn(_cursor())
        job = _make_job()
        job.logs = ["Línea 1", "Línea 2", "Línea 3"]

        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.upsert_job(job)

        _, params = test_conn.execute.call_args.args
        logs_param = params[3]
        assert logs_param == "Línea 1\nLínea 2\nLínea 3"

    def test_diagnostics_se_serializa_como_json(self):
        """diagnostics se serializa como JSON string."""
        store = _make_store()
        test_conn = _conn(_cursor())
        job = _make_job()
        job.diagnostics = {"symbols": 42, "files": 10}

        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.upsert_job(job)

        _, params = test_conn.execute.call_args.args
        diag_json = params[6]  # diagnostics es el 7º campo
        assert json.loads(diag_json) == {"symbols": 42, "files": 10}


# ===========================================================================
# recover_interrupted_jobs
# ===========================================================================


class TestRecoverInterruptedJobs:
    def test_retorna_rowcount(self):
        """recover_interrupted_jobs devuelve el número de filas actualizadas."""
        store = _make_store()
        test_conn = _conn(_cursor(rowcount=3))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            count = store.recover_interrupted_jobs()

        assert count == 3

    def test_actualiza_queued_y_running(self):
        """La UPDATE incluye queued y running en el WHERE."""
        store = _make_store()
        test_conn = _conn(_cursor(rowcount=0))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.recover_interrupted_jobs()

        sql, params = test_conn.execute.call_args.args
        assert "UPDATE jobs" in str(sql)
        assert "queued" in params
        assert "running" in params

    def test_retorna_cero_si_rowcount_none(self):
        """recover_interrupted_jobs devuelve 0 si rowcount es None."""
        store = _make_store()
        test_conn = _conn(_cursor(rowcount=None))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            count = store.recover_interrupted_jobs()

        assert count == 0


# ===========================================================================
# get_job
# ===========================================================================


class TestGetJob:
    def _make_row(
        self,
        job_id: str = "job-1",
        status: str = "queued",
        logs: str = "log1\nlog2",
        diagnostics: str = '{"k": 1}',
    ) -> dict:
        return {
            "id": job_id,
            "status": status,
            "progress": 0.5,
            "logs": logs,
            "repo_id": "r1",
            "error": None,
            "diagnostics": diagnostics,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }

    def test_retorna_jobinfo_con_campos_correctos(self):
        """get_job hidrata correctamente un JobInfo desde la fila de DB."""
        store = _make_store()
        row = self._make_row()
        test_conn = _conn(_cursor(rows=[row]))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            job = store.get_job("job-1")

        assert job is not None
        assert job.id == "job-1"
        assert job.status == JobStatus.queued
        assert job.progress == 0.5
        assert job.repo_id == "r1"

    def test_separa_logs_por_salto_de_linea(self):
        """get_job divide los logs por '\\n'."""
        store = _make_store()
        row = self._make_row(logs="a\nb\nc")
        test_conn = _conn(_cursor(rows=[row]))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            job = store.get_job("job-1")

        assert job.logs == ["a", "b", "c"]

    def test_retorna_none_si_no_existe(self):
        """get_job devuelve None cuando la fila no existe."""
        store = _make_store()
        test_conn = _conn(_cursor(rows=[]))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            job = store.get_job("no-existe")

        assert job is None

    def test_diagnostics_invalidos_retornan_dict_vacio(self):
        """get_job maneja diagnostics con JSON corrupto."""
        store = _make_store()
        row = self._make_row(diagnostics="NOT_JSON{{")
        test_conn = _conn(_cursor(rows=[row]))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            job = store.get_job("job-1")

        assert job.diagnostics == {}

    def test_logs_vacios_retornan_lista_vacia(self):
        """get_job con logs='' devuelve logs=[]."""
        store = _make_store()
        row = self._make_row(logs="")
        test_conn = _conn(_cursor(rows=[row]))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            job = store.get_job("job-1")

        assert job.logs == []


# ===========================================================================
# list_repo_ids
# ===========================================================================


class TestListRepoIds:
    def test_retorna_ids_de_filas(self):
        """list_repo_ids extrae repo_id de las filas SQL."""
        store = _make_store()
        rows = [{"repo_id": "r1"}, {"repo_id": "r2"}]
        test_conn = _conn(_cursor(rows=rows))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            ids = store.list_repo_ids()

        assert ids == ["r1", "r2"]

    def test_filtra_vacios_y_none(self):
        """list_repo_ids omite filas con repo_id vacío o None."""
        store = _make_store()
        rows = [{"repo_id": "r1"}, {"repo_id": None}, {"repo_id": ""}]
        test_conn = _conn(_cursor(rows=rows))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            ids = store.list_repo_ids()

        assert "r1" in ids
        assert None not in ids
        assert "" not in ids


# ===========================================================================
# delete_repo_data
# ===========================================================================


class TestDeleteRepoData:
    def test_retorna_conteos_de_jobs_y_repos(self):
        """delete_repo_data devuelve jobs_deleted, repos_deleted y total."""
        store = _make_store()
        cursor_jobs = _cursor(rowcount=4)
        cursor_repos = _cursor(rowcount=1)
        test_conn = _conn()

        call_count = {"n": 0}

        def execute_side(sql, params=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return cursor_jobs   # DELETE FROM jobs
            return cursor_repos       # DELETE FROM repos

        test_conn.execute.side_effect = execute_side
        test_conn.__enter__.return_value = test_conn
        test_conn.__exit__.return_value = False

        with patch(_PATCH_CONNECT, return_value=test_conn):
            result = store.delete_repo_data("r1")

        assert result["jobs_deleted"] == 4
        assert result["repos_deleted"] == 1
        assert result["total"] == 5

    def test_total_es_suma_de_jobs_y_repos(self):
        """total = jobs_deleted + repos_deleted siempre."""
        store = _make_store()
        test_conn = _conn(_cursor(rowcount=2))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            result = store.delete_repo_data("r1")

        assert result["total"] == result["jobs_deleted"] + result["repos_deleted"]


# ===========================================================================
# reset_all
# ===========================================================================


class TestResetAll:
    def test_ejecuta_delete_en_ambas_tablas(self):
        """reset_all llama DELETE FROM jobs y DELETE FROM repos."""
        store = _make_store()
        test_conn = _conn(_cursor())

        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.reset_all()

        calls_sql = [str(c.args[0]) for c in test_conn.execute.call_args_list]
        assert any("DELETE FROM jobs" in s for s in calls_sql)
        assert any("DELETE FROM repos" in s for s in calls_sql)


# ===========================================================================
# upsert_repo_runtime / get_repo_runtime / delete_repo_runtime
# ===========================================================================


class TestRepoRuntime:
    def test_upsert_repo_runtime_llama_insert(self):
        """upsert_repo_runtime ejecuta INSERT INTO repos."""
        store = _make_store()
        test_conn = _conn(_cursor())

        with patch(_PATCH_CONNECT, return_value=test_conn):
            store.upsert_repo_runtime(
                repo_id="r1",
                organization="org",
                repo_url="https://example.com/repo.git",
                branch="main",
                local_path="/tmp/r1",
                embedding_provider="vertex",
                embedding_model="text-embedding-005",
            )

        sql, params = test_conn.execute.call_args.args
        assert "INSERT INTO repos" in str(sql)
        assert "r1" in params

    def test_get_repo_runtime_retorna_dict(self):
        """get_repo_runtime hidrata dict con provider y model."""
        store = _make_store()
        row = {"embedding_provider": "vertex", "embedding_model": "te-005"}
        test_conn = _conn(_cursor(rows=[row]))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            result = store.get_repo_runtime("r1")

        assert result is not None
        assert result["last_embedding_provider"] == "vertex"
        assert result["last_embedding_model"] == "te-005"

    def test_get_repo_runtime_retorna_none_si_no_existe(self):
        """get_repo_runtime devuelve None cuando la fila no existe."""
        store = _make_store()
        test_conn = _conn(_cursor(rows=[]))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            assert store.get_repo_runtime("no-existe") is None

    def test_delete_repo_runtime_retorna_rowcount(self):
        """delete_repo_runtime devuelve el número de filas borradas."""
        store = _make_store()
        test_conn = _conn(_cursor(rowcount=1))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            count = store.delete_repo_runtime("r1")

        assert count == 1


# ===========================================================================
# list_active_job_ids
# ===========================================================================


class TestListActiveJobIds:
    def test_sin_repo_id_lista_todos_activos(self):
        """list_active_job_ids sin repo_id devuelve todos queued/running."""
        store = _make_store()
        rows = [{"id": "j1"}, {"id": "j2"}]
        test_conn = _conn(_cursor(rows=rows))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            ids = store.list_active_job_ids()

        assert ids == ["j1", "j2"]

    def test_con_repo_id_filtra_por_repo(self):
        """list_active_job_ids con repo_id incluye repo_id en el WHERE."""
        store = _make_store()
        rows = [{"id": "j3"}]
        test_conn = _conn(_cursor(rows=rows))

        with patch(_PATCH_CONNECT, return_value=test_conn):
            ids = store.list_active_job_ids(repo_id="r2")

        sql, params = test_conn.execute.call_args.args
        assert "r2" in params
        assert ids == ["j3"]
