"""Contrato abstracto para el almacén de metadatos operativos."""

from __future__ import annotations

from abc import ABC, abstractmethod
import datetime

from coderag.core.models import JobInfo


class BaseMetadataStore(ABC):
    """Interfaz común para almacenes de metadatos de jobs y repositorios."""

    @abstractmethod
    def upsert_job(self, job: JobInfo) -> None:
        """Inserta o reemplaza la instantánea del trabajo."""

    @abstractmethod
    def recover_interrupted_jobs(self) -> int:
        """Marca jobs queued/running como failed tras reinicio inesperado."""

    @abstractmethod
    def get_job(self, job_id: str) -> JobInfo | None:
        """Lee la instantánea del trabajo por identificador."""

    @abstractmethod
    def list_repo_ids(self) -> list[str]:
        """Lista ids de repositorio conocidos desde jobs y repos."""

    @abstractmethod
    def list_repo_catalog(self) -> list[dict[str, str | None]]:
        """Retorna catálogo de repos persistidos con metadata de ingesta."""

    @abstractmethod
    def list_active_job_ids(self, repo_id: str | None = None) -> list[str]:
        """Lista jobs activos (queued/running), opcionalmente por repo."""

    @abstractmethod
    def upsert_repo_runtime(
        self,
        *,
        repo_id: str,
        organization: str | None,
        repo_url: str,
        branch: str,
        local_path: str,
        embedding_provider: str | None,
        embedding_model: str | None,
    ) -> None:
        """Inserta o actualiza metadata runtime por repositorio."""

    @abstractmethod
    def get_repo_runtime(self, repo_id: str) -> dict[str, str | None] | None:
        """Obtiene metadata runtime almacenada para un repositorio."""

    @abstractmethod
    def touch_repo_last_queried_at(self, repo_id: str) -> int:
        """Actualiza la fecha de última consulta del repositorio."""

    @abstractmethod
    def list_stale_repos(
        self,
        *,
        last_queried_on_or_before: datetime.datetime,
    ) -> list[dict[str, object | None]]:
        """Lista repositorios cuya última consulta es menor o igual a la fecha."""

    @abstractmethod
    def delete_repo_runtime(self, repo_id: str) -> int:
        """Elimina metadata runtime del repositorio y devuelve filas afectadas."""

    @abstractmethod
    def delete_repo_jobs(self, repo_id: str) -> int:
        """Elimina historial de jobs asociados al repositorio y devuelve filas."""

    @abstractmethod
    def delete_repo_data(self, repo_id: str) -> dict[str, int]:
        """Elimina metadata de repositorio y jobs, retornando conteos por tabla."""

    def list_repo_ingest_snapshots(
        self,
        repo_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, object | None]]:
        """Lista snapshots operativos históricos para un repositorio."""
        del repo_id, limit
        return []

    def delete_repo_ingest_snapshots(self, repo_id: str) -> int:
        """Elimina snapshots históricos del repositorio cuando el backend lo soporta."""
        del repo_id
        return 0

    def record_ingest_snapshot(
        self,
        *,
        repo_id: str,
        job_id: str,
        job_status: str,
        error_message: str | None,
        diagnostics: dict[str, object],
        snapshot_at: datetime.datetime,
    ) -> None:
        """Persiste una foto operativa de ingesta cuando el backend lo soporta."""
        del repo_id, job_id, job_status, error_message, diagnostics, snapshot_at
