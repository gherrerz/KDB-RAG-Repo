"""Modelos ORM de SQLAlchemy para metadata operativa en PostgreSQL."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Float, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from coderag.storage.postgres_schema import (
    POSTGRES_JOBS_TABLE_NAME,
    POSTGRES_REPOS_TABLE_NAME,
    PostgresDeclarativeBase,
)


class JobRecord(PostgresDeclarativeBase):
    """Representa la metadata persistida de un job de ingesta."""

    __tablename__ = POSTGRES_JOBS_TABLE_NAME

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    progress: Mapped[float] = mapped_column(Float, nullable=False)
    logs: Mapped[str] = mapped_column(Text, nullable=False)
    repo_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnostics: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    created_at: Mapped[Any] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[Any] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )


class RepoRecord(PostgresDeclarativeBase):
    """Representa la metadata runtime persistida de un repositorio."""

    __tablename__ = POSTGRES_REPOS_TABLE_NAME

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    organization: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    branch: Mapped[str] = mapped_column(Text, nullable=False)
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Any] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[Any] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    embedding_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)