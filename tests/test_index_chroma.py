"""Pruebas de comportamiento de procesamiento por lotes del índice Chroma."""

import base64
from typing import Any
from types import SimpleNamespace

import pytest
from chromadb.errors import InvalidDimensionException

from coderag.ingestion.index_chroma import ChromaIndex


class _FakeCollection:
    """Colección Chroma falsa para pruebas unitarias de llamadas upsert."""

    def __init__(
        self,
        fail_once: bool = False,
        error_once: Exception | None = None,
    ) -> None:
        """Inicialice el estado de colección falsa."""
        self.calls: list[int] = []
        self.attempts: list[int] = []
        self.fail_once = fail_once
        self.error_once = error_once
        self.error_if_batch_size_gt: int | None = None
        self.error_on_large_batch: Exception | None = None
        self.repo_ids: list[str] = []
        self.metadata: dict[str, Any] = {}

    def upsert(
        self,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Registre el tamaño de la llamada y, opcionalmente, simule el error de la primera llamada."""
        self.attempts.append(len(ids))
        if self.fail_once:
            self.fail_once = False
            raise InvalidDimensionException("dim")
        if self.error_once is not None:
            error = self.error_once
            self.error_once = None
            raise error
        if (
            self.error_if_batch_size_gt is not None
            and len(ids) > self.error_if_batch_size_gt
            and self.error_on_large_batch is not None
        ):
            raise self.error_on_large_batch
        self.calls.append(len(ids))

    def query(self, **kwargs: Any) -> dict[str, list[list[Any]]]:
        """Proporcione una respuesta de consulta mínima para que esté completa."""
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def count(self) -> int:
        """Devuelve el total de ids almacenados en la colección falsa."""
        return len(self.repo_ids)

    def get(self, **kwargs: Any) -> dict[str, list[str]]:
        """Devuelve ids filtrados por repo_id para probar borrado selectivo."""
        where = kwargs.get("where") or {}
        repo_id = where.get("repo_id")
        if repo_id is None:
            matches = list(self.repo_ids)
        else:
            matches = [
                item_id
                for item_id in self.repo_ids
                if item_id.startswith(f"{repo_id}:")
            ]
        limit = int(kwargs.get("limit") or len(matches))
        offset = int(kwargs.get("offset") or 0)
        return {"ids": matches[offset:offset + limit]}

    def delete(self, ids: list[str]) -> None:
        """Elimina ids simulados para un repo_id en pruebas."""
        self.repo_ids = [item_id for item_id in self.repo_ids if item_id not in set(ids)]


class _FakeClient:
    """Cliente Chroma falso con tamaño de lote configurable."""

    def __init__(self) -> None:
        """Inicializar mapa de colecciones para cliente falso."""
        self.collections: dict[str, _FakeCollection] = {}
        self.metadata_calls: dict[str, dict[str, str] | None] = {}

    def get_or_create_collection(
        self,
        name: str,
        metadata: dict[str, str] | None = None,
    ) -> _FakeCollection:
        """Devuelve o crea una colección falsa por nombre."""
        self.metadata_calls[name] = metadata
        collection = self.collections.get(name)
        if collection is None:
            collection = _FakeCollection()
            self.collections[name] = collection
        collection.metadata = metadata or {}
        return collection

    def delete_collection(self, name: str) -> None:
        """Elimina una colección falsa."""
        if name in self.collections:
            del self.collections[name]

    def get_max_batch_size(self) -> int:
        """Devuelve un tamaño de lote máximo falso estricto para afirmaciones de prueba."""
        return 3


class _FakeRemoteClient(_FakeClient):
    """Cliente remoto falso que registra los parámetros de construcción."""

    def __init__(self, host: str, port: int, headers: dict[str, str]) -> None:
        """Inicializa un cliente remoto falso con su configuración efectiva."""
        super().__init__()
        self.host = host
        self.port = port
        self.headers = headers


def _prepare_embedded_settings(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
) -> None:
    """Fuerza CHROMA_MODE=embedded para pruebas unitarias locales del índice."""
    monkeypatch.setenv("CHROMA_MODE", "embedded")
    module.get_settings.cache_clear()
    module.ChromaIndex.reset_shared_state()


def test_upsert_is_split_by_chroma_max_batch_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """Divide los upserts en múltiples llamadas que respetan el tamaño máximo de lote."""
    fake_client = _FakeClient()

    import coderag.ingestion.index_chroma as module

    monkeypatch.setattr(
        module.chromadb,
        "PersistentClient",
        lambda *args, **kwargs: fake_client,
    )
    _prepare_embedded_settings(monkeypatch, module)
    try:
        index = ChromaIndex()
    finally:
        module.get_settings.cache_clear()

    ids = [f"id{i}" for i in range(7)]
    docs = ["x"] * 7
    embeds = [[0.1, 0.2]] * 7
    metas = [{"i": i} for i in range(7)]
    index.upsert("code_symbols", ids, docs, embeds, metas)

    calls = fake_client.collections["code_symbols"].calls
    assert calls == [3, 3, 1]


def test_remote_batch_size_override_takes_precedence_over_client_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prioriza el override remoto explícito por encima del límite del cliente."""
    fake_client = _FakeClient()

    import coderag.ingestion.index_chroma as module

    settings = SimpleNamespace(
        chroma_mode="remote",
        chroma_host="chroma.example.local",
        chroma_port=8443,
        chroma_token="",
        chroma_username="",
        chroma_password="",
        chroma_remote_batch_size_override=2,
        resolve_chroma_hnsw_space=lambda: "cosine",
    )

    def _fake_http_client(host: str, port: int, headers: dict[str, str]) -> _FakeClient:
        del host, port, headers
        return fake_client

    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module.chromadb, "HttpClient", _fake_http_client)
    module.ChromaIndex.reset_shared_state()

    index = module.ChromaIndex()
    index.upsert(
        "code_symbols",
        [f"id{i}" for i in range(5)],
        ["x"] * 5,
        [[0.1, 0.2]] * 5,
        [{"i": i} for i in range(5)],
    )

    calls = fake_client.collections["code_symbols"].calls
    assert calls == [2, 2, 1]


def test_embedded_ignores_remote_batch_size_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No aplica el override remoto cuando Chroma corre embebido."""
    fake_client = _FakeClient()

    import coderag.ingestion.index_chroma as module

    monkeypatch.setattr(
        module.chromadb,
        "PersistentClient",
        lambda *args, **kwargs: fake_client,
    )
    monkeypatch.setenv("CHROMA_REMOTE_BATCH_SIZE_OVERRIDE", "2")
    _prepare_embedded_settings(monkeypatch, module)

    try:
        index = module.ChromaIndex()
    finally:
        module.get_settings.cache_clear()

    index.upsert(
        "code_symbols",
        [f"id{i}" for i in range(7)],
        ["x"] * 7,
        [[0.1, 0.2]] * 7,
        [{"i": i} for i in range(7)],
    )

    calls = fake_client.collections["code_symbols"].calls
    assert calls == [3, 3, 1]


def test_collections_are_created_with_configured_hnsw_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propaga CHROMA_HNSW_SPACE al crear/abrir colecciones gestionadas."""
    fake_client = _FakeClient()

    import coderag.ingestion.index_chroma as module

    monkeypatch.setattr(
        module.chromadb,
        "PersistentClient",
        lambda *args, **kwargs: fake_client,
    )
    monkeypatch.setenv("CHROMA_HNSW_SPACE", "l2")
    _prepare_embedded_settings(monkeypatch, module)

    try:
        module.ChromaIndex()
    finally:
        module.get_settings.cache_clear()

    for metadata in fake_client.metadata_calls.values():
        assert metadata == {"hnsw:space": "l2"}


def test_remote_client_uses_bearer_token_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construye Authorization Bearer cuando CHROMA_TOKEN está configurado."""
    import coderag.ingestion.index_chroma as module

    captured: dict[str, Any] = {}

    def _fake_http_client(host: str, port: int, headers: dict[str, str]) -> _FakeRemoteClient:
        captured["client"] = _FakeRemoteClient(host, port, headers)
        return captured["client"]

    settings = SimpleNamespace(
        chroma_mode="remote",
        chroma_host="chroma.example.local",
        chroma_port=8443,
        chroma_token="bearer-token",
        chroma_username="",
        chroma_password="",
        resolve_chroma_hnsw_space=lambda: "cosine",
    )

    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module.chromadb, "HttpClient", _fake_http_client)
    module.ChromaIndex.reset_shared_state()

    index = module.ChromaIndex()

    assert index.client is captured["client"]
    assert captured["client"].host == "chroma.example.local"
    assert captured["client"].port == 8443
    assert captured["client"].headers == {
        "Authorization": "Bearer bearer-token"
    }


def test_remote_client_uses_basic_auth_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construye Authorization Basic cuando hay usuario y password."""
    import coderag.ingestion.index_chroma as module

    captured: dict[str, Any] = {}

    def _fake_http_client(host: str, port: int, headers: dict[str, str]) -> _FakeRemoteClient:
        captured["client"] = _FakeRemoteClient(host, port, headers)
        return captured["client"]

    settings = SimpleNamespace(
        chroma_mode="remote",
        chroma_host="chroma.example.local",
        chroma_port=8443,
        chroma_token="",
        chroma_username="svc-user",
        chroma_password="svc-pass",
        resolve_chroma_hnsw_space=lambda: "cosine",
    )

    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module.chromadb, "HttpClient", _fake_http_client)
    module.ChromaIndex.reset_shared_state()

    index = module.ChromaIndex()

    expected = base64.b64encode(b"svc-user:svc-pass").decode("ascii")
    assert index.client is captured["client"]
    assert captured["client"].headers == {
        "Authorization": f"Basic {expected}"
    }


def test_remote_client_wraps_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Envuelve errores remotos con destino y auth mode sanitizados."""
    import coderag.ingestion.index_chroma as module

    settings = SimpleNamespace(
        chroma_mode="remote",
        chroma_host="chroma.example.local",
        chroma_port=8443,
        chroma_token="super-secret-token",
        chroma_username="",
        chroma_password="",
        resolve_chroma_hnsw_space=lambda: "cosine",
    )

    def _raising_http_client(*args: Any, **kwargs: Any) -> _FakeRemoteClient:
        del args, kwargs
        raise RuntimeError("connect timeout")

    monkeypatch.setattr(module.chromadb, "HttpClient", _raising_http_client)

    with pytest.raises(RuntimeError) as exc_info:
        module.build_remote_chroma_client(settings)

    message = str(exc_info.value)
    assert "crear cliente HTTP" in message
    assert "chroma.example.local:8443" in message
    assert "auth=bearer" in message
    assert "super-secret-token" not in message


def test_build_remote_error_message_detects_proxy_reset_signal() -> None:
    """Anota señal e hipótesis operativa cuando el upstream corta la respuesta."""
    import coderag.ingestion.index_chroma as module

    settings = SimpleNamespace(
        chroma_mode="remote",
        chroma_host="chroma.example.local",
        chroma_port=8443,
        chroma_token="",
        chroma_username="svc-user",
        chroma_password="svc-pass",
    )

    message = module.build_remote_chroma_error_message(
        settings,
        operation="upsert",
        exc=RuntimeError("Server disconnected without sending a response"),
        collection_name="code_symbols",
        batch_size=250,
    )

    assert "señal=proxy_reset" in message
    assert "lote=250" in message
    assert "proxy, ingress o service mesh" in message
    assert "svc-pass" not in message


def test_build_remote_error_message_detects_upstream_restart_signal() -> None:
    """Anota señal compatible con upstream no disponible o reiniciando."""
    import coderag.ingestion.index_chroma as module

    settings = SimpleNamespace(
        chroma_mode="remote",
        chroma_host="chroma.example.local",
        chroma_port=8443,
        chroma_token="",
        chroma_username="",
        chroma_password="",
    )

    message = module.build_remote_chroma_error_message(
        settings,
        operation="upsert",
        exc=RuntimeError("503 no healthy upstream"),
        collection_name="code_symbols",
    )

    assert "señal=upstream_reiniciando" in message
    assert "reinicios del pod remoto de Chroma" in message


def test_count_collection_uses_native_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Usa count nativo de la colección cuando el runtime lo expone."""
    fake_client = _FakeClient()

    import coderag.ingestion.index_chroma as module

    monkeypatch.setattr(
        module.chromadb,
        "PersistentClient",
        lambda *args, **kwargs: fake_client,
    )
    _prepare_embedded_settings(monkeypatch, module)
    try:
        index = module.ChromaIndex()
    finally:
        module.get_settings.cache_clear()

    fake_client.collections["code_symbols"].repo_ids = [
        "repo-1:a",
        "repo-1:b",
        "repo-2:c",
    ]

    assert index.count_collection("code_symbols") == 3


def test_get_collection_metadata_returns_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entrega una copia defensiva de metadata de la colección gestionada."""
    fake_client = _FakeClient()

    import coderag.ingestion.index_chroma as module

    monkeypatch.setattr(
        module.chromadb,
        "PersistentClient",
        lambda *args, **kwargs: fake_client,
    )
    _prepare_embedded_settings(monkeypatch, module)
    try:
        index = module.ChromaIndex()
    finally:
        module.get_settings.cache_clear()

    metadata = index.get_collection_metadata("code_symbols")
    metadata["mutated"] = True

    assert index.get_collection_metadata("code_symbols") == {
        "hnsw:space": "cosine"
    }


def test_count_collection_supports_filtered_where(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cuenta subconjuntos usando where cuando no aplica count nativo global."""
    fake_client = _FakeClient()

    import coderag.ingestion.index_chroma as module

    monkeypatch.setattr(
        module.chromadb,
        "PersistentClient",
        lambda *args, **kwargs: fake_client,
    )
    _prepare_embedded_settings(monkeypatch, module)
    try:
        index = module.ChromaIndex()
    finally:
        module.get_settings.cache_clear()

    fake_client.collections["code_symbols"].repo_ids = [
        "repo-1:a",
        "repo-1:b",
        "repo-2:c",
    ]

    assert index.count_collection(
        "code_symbols",
        page_size=1,
        where={"repo_id": "repo-1"},
    ) == 2


def test_remote_upsert_wraps_payload_too_large_with_batch_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incluye señal e instrucción de tuning cuando el payload remoto es grande."""
    import coderag.ingestion.index_chroma as module

    fake_client = _FakeClient()
    fake_client.collections["code_symbols"] = _FakeCollection(
        error_once=RuntimeError("413 request entity too large")
    )

    settings = SimpleNamespace(
        chroma_mode="remote",
        chroma_host="chroma.example.local",
        chroma_port=8443,
        chroma_token="",
        chroma_username="",
        chroma_password="",
        chroma_remote_batch_size_override=2,
        resolve_chroma_hnsw_space=lambda: "cosine",
    )

    def _fake_http_client(host: str, port: int, headers: dict[str, str]) -> _FakeClient:
        del host, port, headers
        return fake_client

    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module.chromadb, "HttpClient", _fake_http_client)
    module.ChromaIndex.reset_shared_state()

    index = module.ChromaIndex()

    with pytest.raises(RuntimeError) as exc_info:
        index.upsert(
            "code_symbols",
            [f"id{i}" for i in range(5)],
            ["x"] * 5,
            [[0.1, 0.2]] * 5,
            [{"i": i} for i in range(5)],
        )

    message = str(exc_info.value)
    assert "señal=payload_grande" in message
    assert "lote=2" in message
    assert "CHROMA_REMOTE_BATCH_SIZE_OVERRIDE" in message


def test_remote_upsert_splits_recoverable_payload_error_until_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recupera writes remotos grandes partiendo el lote hasta que entran."""
    import coderag.ingestion.index_chroma as module

    fake_client = _FakeClient()
    collection = _FakeCollection()
    collection.error_if_batch_size_gt = 1
    collection.error_on_large_batch = RuntimeError("413 request entity too large")
    fake_client.collections["code_symbols"] = collection

    settings = SimpleNamespace(
        chroma_mode="remote",
        chroma_host="chroma.example.local",
        chroma_port=8443,
        chroma_token="",
        chroma_username="",
        chroma_password="",
        chroma_remote_batch_size_override=4,
        chroma_remote_min_batch_size=1,
        chroma_remote_max_split_depth=6,
        resolve_chroma_hnsw_space=lambda: "cosine",
    )

    def _fake_http_client(host: str, port: int, headers: dict[str, str]) -> _FakeClient:
        del host, port, headers
        return fake_client

    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module.chromadb, "HttpClient", _fake_http_client)
    module.ChromaIndex.reset_shared_state()

    index = module.ChromaIndex()
    index.upsert(
        "code_symbols",
        [f"id{i}" for i in range(4)],
        ["x"] * 4,
        [[0.1, 0.2]] * 4,
        [{"i": i} for i in range(4)],
    )

    assert collection.calls == [1, 1, 1, 1]
    assert 4 in collection.attempts
    assert 2 in collection.attempts


def test_remote_request_budget_reduces_initial_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reduce el lote inicial remoto cuando la estimación supera el budget."""
    fake_client = _FakeClient()

    import coderag.ingestion.index_chroma as module

    settings = SimpleNamespace(
        chroma_mode="remote",
        chroma_host="chroma.example.local",
        chroma_port=8443,
        chroma_token="",
        chroma_username="",
        chroma_password="",
        chroma_remote_batch_size_override=0,
        chroma_max_request_bytes=1500,
        resolve_chroma_hnsw_space=lambda: "cosine",
    )

    def _fake_http_client(host: str, port: int, headers: dict[str, str]) -> _FakeClient:
        del host, port, headers
        return fake_client

    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module.chromadb, "HttpClient", _fake_http_client)
    module.ChromaIndex.reset_shared_state()

    index = module.ChromaIndex()
    index.upsert(
        "code_symbols",
        [f"id{i}" for i in range(4)],
        ["x" * 1000] * 4,
        [[0.1, 0.2]] * 4,
        [{"i": i} for i in range(4)],
    )

    assert fake_client.collections["code_symbols"].calls == [1, 1, 1, 1]


def test_remote_init_wraps_collection_open_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incluye colección y auth mode cuando falla el bootstrap remoto."""
    import coderag.ingestion.index_chroma as module

    class _FailingRemoteClient(_FakeRemoteClient):
        def get_or_create_collection(
            self,
            name: str,
            metadata: dict[str, str] | None = None,
        ) -> _FakeCollection:
            del name, metadata
            raise RuntimeError("401 unauthorized")

    def _fake_http_client(
        host: str,
        port: int,
        headers: dict[str, str],
    ) -> _FailingRemoteClient:
        return _FailingRemoteClient(host, port, headers)

    settings = SimpleNamespace(
        chroma_mode="remote",
        chroma_host="chroma.example.local",
        chroma_port=8443,
        chroma_token="",
        chroma_username="svc-user",
        chroma_password="svc-pass",
        resolve_chroma_hnsw_space=lambda: "cosine",
    )

    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module.chromadb, "HttpClient", _fake_http_client)
    module.ChromaIndex.reset_shared_state()

    with pytest.raises(RuntimeError) as exc_info:
        module.ChromaIndex()

    message = str(exc_info.value)
    assert "abrir colección gestionada" in message
    assert "auth=basic" in message
    assert "colección=code_symbols" in message
    assert "svc-pass" not in message


def test_count_by_repo_id_wraps_remote_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incluye operación y colección cuando falla el conteo remoto."""
    import coderag.ingestion.index_chroma as module

    captured: dict[str, Any] = {}

    def _fake_http_client(
        host: str,
        port: int,
        headers: dict[str, str],
    ) -> _FakeRemoteClient:
        captured["client"] = _FakeRemoteClient(host, port, headers)
        return captured["client"]

    settings = SimpleNamespace(
        chroma_mode="remote",
        chroma_host="chroma.example.local",
        chroma_port=8443,
        chroma_token="",
        chroma_username="",
        chroma_password="",
        resolve_chroma_hnsw_space=lambda: "cosine",
    )

    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module.chromadb, "HttpClient", _fake_http_client)
    module.ChromaIndex.reset_shared_state()

    index = module.ChromaIndex()

    def _raise_get(**kwargs: Any) -> dict[str, list[str]]:
        del kwargs
        raise RuntimeError("connection refused")

    index.collections["code_symbols"].get = _raise_get

    with pytest.raises(RuntimeError) as exc_info:
        index.count_by_repo_id("code_symbols", "repo-1")

    message = str(exc_info.value)
    assert "contar documentos por repo_id" in message
    assert "auth=none" in message
    assert "colección=code_symbols" in message
    assert "chroma.example.local:8443" in message


def test_upsert_recovers_from_dimension_message_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lanza error controlado sin borrar la colección ante mismatch dimensional."""
    fake_client = _FakeClient()
    fake_client.collections["code_symbols"] = _FakeCollection(
        error_once=RuntimeError(
            "Embedding dimension 256 does not match collection dimensionality 1536"
        )
    )

    import coderag.ingestion.index_chroma as module

    monkeypatch.setattr(
        module.chromadb,
        "PersistentClient",
        lambda *args, **kwargs: fake_client,
    )
    _prepare_embedded_settings(monkeypatch, module)
    try:
        index = ChromaIndex()
    finally:
        module.get_settings.cache_clear()

    ids = ["id1", "id2"]
    docs = ["x", "y"]
    embeds = [[0.1, 0.2], [0.2, 0.1]]
    metas = [{"i": 1}, {"i": 2}]
    with pytest.raises(RuntimeError) as exc_info:
        index.upsert("code_symbols", ids, docs, embeds, metas)

    message = str(exc_info.value)
    assert "Dimensión de embeddings incompatible" in message


def test_upsert_recovers_from_collection_expect_dimension_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lanza error controlado para el formato de mismatch dimensional de Chroma v1."""
    fake_client = _FakeClient()
    fake_client.collections["code_symbols"] = _FakeCollection(
        error_once=RuntimeError(
            "Collection expecting embedding with dimension of 3072, got 768"
        )
    )

    import coderag.ingestion.index_chroma as module

    monkeypatch.setattr(
        module.chromadb,
        "PersistentClient",
        lambda *args, **kwargs: fake_client,
    )
    _prepare_embedded_settings(monkeypatch, module)
    try:
        index = ChromaIndex()
    finally:
        module.get_settings.cache_clear()

    ids = ["id1", "id2"]
    docs = ["x", "y"]
    embeds = [[0.1, 0.2], [0.2, 0.1]]
    metas = [{"i": 1}, {"i": 2}]
    with pytest.raises(RuntimeError) as exc_info:
        index.upsert("code_symbols", ids, docs, embeds, metas)

    message = str(exc_info.value)
    assert "Dimensión de embeddings incompatible" in message


def test_delete_by_repo_id_removes_documents_from_all_collections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Elimina documentos filtrados por repo_id y devuelve conteo total agregado."""
    fake_client = _FakeClient()

    import coderag.ingestion.index_chroma as module

    monkeypatch.setattr(
        module.chromadb,
        "PersistentClient",
        lambda *args, **kwargs: fake_client,
    )
    _prepare_embedded_settings(monkeypatch, module)
    try:
        index = ChromaIndex()
    finally:
        module.get_settings.cache_clear()

    for collection in fake_client.collections.values():
        collection.repo_ids = ["r1:a", "r1:b", "other:c"]

    result = index.delete_by_repo_id("r1")

    assert result["total"] == 10
    assert result["code_symbols"] == 2
    assert result["code_files"] == 2
    assert result["code_modules"] == 2
    assert result["docs_misc"] == 2
    assert result["infra_ci"] == 2
    for collection in fake_client.collections.values():
        assert collection.repo_ids == ["other:c"]
