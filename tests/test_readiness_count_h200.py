"""H200: el chequeo de readiness cuenta documentos sin paginar de a 500.

Run 20 (Hexa Studio): ``GET /repos/{id}/status`` y el arranque de
``/query`` y ``/query/retrieval`` contaban los documentos del repositorio
en Chroma paginando por offset de a 500, solo para saber si habia alguno.
Para ``hexa-st-be`` (23.032 simbolos) eran 47 paginas con offset creciente:
3,2 s por llamada (login: 0,05 s) y mas de 10 s con peticiones simultaneas,
lo que agotaba el limite de la plataforma. Un conteo con paginas grandes da
el mismo numero exacto en 0,18 s.
"""

from typing import Any

import pytest

from coderag.core import storage_health
from coderag.ingestion.index_chroma import ChromaIndex


class _CountingCollection:
    """Coleccion falsa que registra cada llamada a ``get``."""

    def __init__(self, total: int) -> None:
        self.ids = [f"repo:{number}" for number in range(total)]
        self.calls: list[dict[str, Any]] = []

    def get(self, **kwargs: Any) -> dict[str, list[str]]:
        self.calls.append(kwargs)
        limit = int(kwargs.get("limit") or len(self.ids))
        offset = int(kwargs.get("offset") or 0)
        return {"ids": self.ids[offset:offset + limit]}


def _count(collection: _CountingCollection, page_size: int) -> int:
    index = ChromaIndex.__new__(ChromaIndex)
    return index._count_collection_pages(
        collection,
        page_size=page_size,
        where={"repo_id": "repo"},
    )


@pytest.mark.parametrize(
    "total",
    [
        0,
        1,
        storage_health.READINESS_COUNT_PAGE_SIZE - 1,
        storage_health.READINESS_COUNT_PAGE_SIZE,
        storage_health.READINESS_COUNT_PAGE_SIZE + 1,
        23032,
    ],
)
def test_the_count_is_exact_at_the_page_edges(total: int) -> None:
    collection = _CountingCollection(total)

    counted = _count(collection, storage_health.READINESS_COUNT_PAGE_SIZE)

    assert counted == total


def test_a_repository_of_the_size_of_hexa_st_be_takes_one_call() -> None:
    """23.032 documentos: antes 47 paginas de 500, ahora una sola llamada."""
    collection = _CountingCollection(23032)

    _count(collection, storage_health.READINESS_COUNT_PAGE_SIZE)

    assert len(collection.calls) == 1
    assert collection.calls[0]["include"] == []
    assert collection.calls[0]["where"] == {"repo_id": "repo"}


def test_the_old_page_size_needed_47_calls() -> None:
    """Fija el costo que se evita: el offset crece en cada pagina."""
    collection = _CountingCollection(23032)

    assert _count(collection, 500) == 23032
    assert len(collection.calls) == 47


def test_readiness_counts_every_query_collection_with_large_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, int]] = []

    class FakeVectorIndex:
        collections = {
            name: object() for name in storage_health.QUERY_COLLECTIONS
        }

        def count_by_repo_id(
            self,
            collection_name: str,
            repo_id: str,
            page_size: int = 500,
        ) -> int:
            seen.append((collection_name, page_size))
            return 7

    monkeypatch.setattr(
        storage_health,
        "build_managed_vector_index",
        lambda: FakeVectorIndex(),
    )

    counts = storage_health._count_query_collections_for_repo("mall")

    assert counts == {name: 7 for name in storage_health.QUERY_COLLECTIONS}
    assert seen == [
        (name, storage_health.READINESS_COUNT_PAGE_SIZE)
        for name in storage_health.QUERY_COLLECTIONS
    ]
    assert storage_health.READINESS_COUNT_PAGE_SIZE >= 20_000


def test_an_explicit_page_size_is_still_honoured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []

    class FakeVectorIndex:
        collections = {"code_symbols": object()}

        def count_by_repo_id(
            self,
            collection_name: str,
            repo_id: str,
            page_size: int = 500,
        ) -> int:
            seen.append(page_size)
            return 1

    monkeypatch.setattr(
        storage_health,
        "build_managed_vector_index",
        lambda: FakeVectorIndex(),
    )

    storage_health._count_chroma_documents_for_repo(
        repo_id="mall",
        collection_name="code_symbols",
        page_size=200,
    )

    assert seen == [200]
