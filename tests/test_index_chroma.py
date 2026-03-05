"""Tests for Chroma index batching behavior."""

from typing import Any

import pytest
from chromadb.errors import InvalidDimensionException

from coderag.ingestion.index_chroma import ChromaIndex


class _FakeCollection:
    """Fake Chroma collection for unit testing upsert calls."""

    def __init__(self, fail_once: bool = False) -> None:
        """Initialize fake collection state."""
        self.calls: list[int] = []
        self.fail_once = fail_once

    def upsert(
        self,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Record call size and optionally simulate first-call failure."""
        if self.fail_once:
            self.fail_once = False
            raise InvalidDimensionException("dim")
        self.calls.append(len(ids))

    def query(self, **kwargs: Any) -> dict[str, list[list[Any]]]:
        """Provide minimal query response for completeness."""
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


class _FakeClient:
    """Fake Chroma client with configurable batch size."""

    def __init__(self) -> None:
        """Initialize collections map for fake client."""
        self.collections: dict[str, _FakeCollection] = {}

    def get_or_create_collection(self, name: str) -> _FakeCollection:
        """Return or create fake collection by name."""
        collection = self.collections.get(name)
        if collection is None:
            collection = _FakeCollection()
            self.collections[name] = collection
        return collection

    def delete_collection(self, name: str) -> None:
        """Delete fake collection."""
        if name in self.collections:
            del self.collections[name]

    def get_max_batch_size(self) -> int:
        """Return strict fake max batch size for test assertions."""
        return 3


def test_upsert_is_split_by_chroma_max_batch_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """Splits upserts into multiple calls that respect max batch size."""
    fake_client = _FakeClient()

    import coderag.ingestion.index_chroma as module

    monkeypatch.setattr(module.chromadb, "PersistentClient", lambda path: fake_client)
    index = ChromaIndex()

    ids = [f"id{i}" for i in range(7)]
    docs = ["x"] * 7
    embeds = [[0.1, 0.2]] * 7
    metas = [{"i": i} for i in range(7)]
    index.upsert("code_symbols", ids, docs, embeds, metas)

    calls = fake_client.collections["code_symbols"].calls
    assert calls == [3, 3, 1]
