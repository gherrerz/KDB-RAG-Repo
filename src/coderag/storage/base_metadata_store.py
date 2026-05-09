"""Contrato abstracto para el almacén de metadatos operativos."""

from __future__ import annotations

from abc import ABC, abstractmethod

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
    def delete_repo_runtime(self, repo_id: str) -> int:
        """Elimina metadata runtime del repositorio y devuelve filas afectadas."""

    @abstractmethod
    def delete_repo_jobs(self, repo_id: str) -> int:
        """Elimina historial de jobs asociados al repositorio y devuelve filas."""

    @abstractmethod
    def delete_repo_data(self, repo_id: str) -> dict[str, int]:
        """Elimina metadata de repositorio y jobs, retornando conteos por tabla."""
