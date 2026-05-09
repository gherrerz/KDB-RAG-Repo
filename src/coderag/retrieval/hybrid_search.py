"""Recuperación híbrida que combina similitud de vectores y puntuaciones de BM25."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import logging
import re
import unicodedata

from coderag.core.models import RetrievalChunk
from coderag.core.settings import get_settings
from coderag.ingestion.embedding import EmbeddingClient
from coderag.ingestion.index_bm25 import GLOBAL_BM25
from coderag.ingestion.index_chroma import ChromaIndex


VECTOR_COLLECTIONS = ["code_symbols", "code_files", "code_modules"]
LOGGER = logging.getLogger(__name__)
VECTOR_WEIGHT = 0.55
BM25_WEIGHT = 0.45
_EXACT_IDENTIFIER_QUERY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


def _normalize_query(query: str) -> str:
    """Normaliza consultas para reducir ruido ortográfico y de espacios."""
    lowered = query.strip().lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    without_marks = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )
    return " ".join(without_marks.split())


def _empty_result() -> dict:
    """Devuelve un resultado vacío con shape compatible de Chroma."""
    return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


def _canonicalize_identifier(value: str) -> str:
    """Normaliza identificadores de configuración para matching exacto."""
    normalized = _normalize_query(value)
    canonical = re.sub(r"[^a-z0-9]+", "_", normalized)
    return re.sub(r"_+", "_", canonical).strip("_")


def _is_exact_identifier_query(query: str) -> bool:
    """Detecta consultas que parecen un identificador exacto."""
    stripped = query.strip()
    if " " in stripped or len(stripped) < 3:
        return False
    if _EXACT_IDENTIFIER_QUERY_RE.fullmatch(stripped) is None:
        return False
    canonical = _canonicalize_identifier(stripped)
    return canonical.count("_") >= 1 or "." in stripped or "-" in stripped


def _focus_identifiers(query: str) -> tuple[str, ...]:
    """Extrae identificadores relevantes incluso dentro de consultas naturales."""
    return tuple(
        dict.fromkeys(
            _canonicalize_identifier(token)
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", query)
            if len(token) >= 3
            and (
                _canonicalize_identifier(token).count("_") >= 1
                or "." in token
                or "-" in token
            )
        )
    )


def _canonicalize_path(path: str) -> str:
    """Normaliza rutas para comparar nombres de archivo y segmentos."""
    return _canonicalize_identifier(path.replace("\\", "/"))


def _path_exact_match(path: str, canonical_query: str) -> bool:
    """Comprueba si la consulta coincide con el nombre o segmento de la ruta."""
    normalized = path.replace("\\", "/").strip("/")
    if not normalized:
        return False
    segments = [segment for segment in normalized.split("/") if segment]
    for segment in segments:
        stem = segment.rsplit(".", maxsplit=1)[0]
        if _canonicalize_identifier(segment) == canonical_query:
            return True
        if _canonicalize_identifier(stem) == canonical_query:
            return True
    return False


def _is_config_path(path: str) -> bool:
    """Identifica rutas que normalmente contienen configuración runtime."""
    normalized = path.strip().lower()
    if not normalized:
        return False
    if normalized.endswith(
        (
            "settings.py",
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
            ".env",
        )
    ):
        return True
    if normalized.endswith((".yaml", ".yml", ".toml", ".ini", ".cfg")):
        return True
    return normalized.startswith("k8s/") or "/k8s/" in normalized


def _identifier_query_score_adjustment(query: str, chunk: RetrievalChunk) -> float:
    """Refuerza coincidencias exactas de identificadores en código y config."""
    exact_identifier_query = _is_exact_identifier_query(query)
    focus_identifiers = list(_focus_identifiers(query))
    if exact_identifier_query:
        focus_identifiers.append(_canonicalize_identifier(query))
    if not focus_identifiers:
        return 0.0

    metadata = chunk.metadata
    path = str(metadata.get("path", "")).strip()
    symbol_name = _canonicalize_identifier(str(metadata.get("symbol_name", "")))
    symbol_type = str(metadata.get("symbol_type", "")).strip().lower()
    canonical_text = _canonicalize_identifier(chunk.text)
    canonical_path = _canonicalize_path(path)
    config_path = _is_config_path(path)
    normalized_path = path.lower()
    test_path = normalized_path.startswith("tests/") or "/tests/" in normalized_path

    best_adjustment = 0.0
    any_match = False
    for canonical_query in dict.fromkeys(focus_identifiers):
        exact_symbol_match = symbol_name == canonical_query
        exact_text_match = canonical_query in canonical_text
        exact_path_match = _path_exact_match(
            path=path,
            canonical_query=canonical_query,
        )
        if exact_symbol_match or exact_text_match or exact_path_match:
            any_match = True

        adjustment = 0.0
        if exact_symbol_match:
            adjustment += 1.0 if exact_identifier_query else 0.8
        if exact_text_match:
            adjustment += 0.55 if exact_identifier_query else 0.40
        if exact_path_match:
            adjustment += 0.40 if exact_identifier_query else 0.28
        if config_path and (exact_symbol_match or exact_text_match or exact_path_match):
            adjustment += 0.35
        if symbol_type == "config_key" and exact_symbol_match:
            adjustment += 0.25
        if canonical_query in canonical_path and exact_symbol_match:
            adjustment += 0.1
        best_adjustment = max(best_adjustment, adjustment)

    adjustment = best_adjustment
    if (not config_path) and (not any_match):
        adjustment -= 0.5
    if test_path and not any_match:
        adjustment -= 0.35
    return adjustment


def _query_collection(
    chroma: ChromaIndex,
    collection_name: str,
    query_embedding: list[float],
    repo_id: str,
    top_n: int,
) -> tuple[str, dict]:
    """Consulta una colección de Chroma y devuelve su nombre junto al resultado."""
    result = chroma.query(
        collection_name=collection_name,
        query_embedding=query_embedding,
        top_n=top_n,
        where={"repo_id": repo_id},
    )
    return collection_name, result


def _vector_results_empty(results: list[dict]) -> bool:
    """Devuelve True cuando ninguna colección retornó ids vectoriales."""
    for result in results:
        ids = result.get("ids", [[]])
        if ids and ids[0]:
            return False
    return True


def _run_vector_search(
    *,
    chroma: ChromaIndex,
    query_embedding: list[float],
    repo_id: str,
    candidate_top_n: int,
) -> list[dict]:
    """Ejecuta la recuperación vectorial sobre las colecciones configuradas."""
    vector_results: list[dict] = []
    try:
        with ThreadPoolExecutor(max_workers=len(VECTOR_COLLECTIONS)) as executor:
            futures = [
                executor.submit(
                    _query_collection,
                    chroma,
                    collection_name,
                    query_embedding,
                    repo_id,
                    candidate_top_n,
                )
                for collection_name in VECTOR_COLLECTIONS
            ]
            results_by_collection = {
                collection_name: result
                for collection_name, result in [future.result() for future in futures]
            }
        vector_results = [
            results_by_collection.get(
                collection_name,
                _empty_result(),
            )
            for collection_name in VECTOR_COLLECTIONS
        ]
    except Exception as exc:
        LOGGER.warning(
            "Fallo recuperación vectorial concurrente para repo=%s; "
            "usando fallback secuencial. error=%s",
            repo_id,
            exc,
        )
        for collection_name in VECTOR_COLLECTIONS:
            try:
                result = chroma.query(
                    collection_name=collection_name,
                    query_embedding=query_embedding,
                    top_n=candidate_top_n,
                    where={"repo_id": repo_id},
                )
                vector_results.append(result)
            except Exception as inner_exc:
                LOGGER.warning(
                    "Fallo recuperación vectorial en colección=%s repo=%s: %s",
                    collection_name,
                    repo_id,
                    inner_exc,
                )
                vector_results.append(_empty_result())
    return vector_results


def _candidate_top_n(query: str, top_n: int) -> int:
    """Amplía candidatos para consultas exactas de identificadores."""
    if not (_is_exact_identifier_query(query) or _focus_identifiers(query)):
        return top_n
    return max(top_n, min(max(top_n * 4, 40), 100))


def hybrid_search(
    repo_id: str,
    query: str,
    top_n: int = 50,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
) -> list[RetrievalChunk]:
    """Busque datos de repositorios indexados con vector y fusión BM25."""
    candidate_top_n = _candidate_top_n(query=query, top_n=top_n)
    embedder = EmbeddingClient(
        provider=embedding_provider,
        model=embedding_model,
    )
    normalized_query = _normalize_query(query)
    vector_results: list[dict] = []
    query_embedding: list[float] | None = None
    try:
        embeddings = embedder.embed_texts([normalized_query])
        if embeddings:
            query_embedding = embeddings[0]
    except Exception as exc:
        LOGGER.warning(
            "No se pudo generar embedding de consulta para repo=%s: %s",
            repo_id,
            exc,
        )

    if query_embedding is not None:
        chroma = ChromaIndex()
        vector_results = _run_vector_search(
            chroma=chroma,
            query_embedding=query_embedding,
            repo_id=repo_id,
            candidate_top_n=candidate_top_n,
        )
        if _vector_results_empty(vector_results) and GLOBAL_BM25.ensure_repo_loaded(repo_id):
            ChromaIndex.reset_shared_state()
            chroma = ChromaIndex()
            vector_results = _run_vector_search(
                chroma=chroma,
                query_embedding=query_embedding,
                repo_id=repo_id,
                candidate_top_n=candidate_top_n,
            )
    else:
        LOGGER.warning(
            "Consulta sin embedding utilizable para repo=%s; "
            "se priorizará BM25.",
            repo_id,
        )

    fused: dict[str, RetrievalChunk] = {}
    scores: defaultdict[str, float] = defaultdict(float)
    for vector_result in vector_results:
        ids = vector_result.get("ids", [[]])[0]
        docs = vector_result.get("documents", [[]])[0]
        metas = vector_result.get("metadatas", [[]])[0]
        distances = vector_result.get("distances", [[]])[0]

        for item_id, doc, meta, distance in zip(ids, docs, metas, distances):
            score = 1.0 / (1.0 + float(distance))
            weighted_score = score * VECTOR_WEIGHT
            scores[item_id] += weighted_score
            fused[item_id] = RetrievalChunk(
                id=item_id,
                text=doc,
                score=weighted_score,
                metadata=meta,
            )

    settings = get_settings()
    postgres_url = (settings.postgres_url or "").strip()

    if postgres_url:
        from coderag.storage.lexical_store import LexicalStore
        lexical_results = LexicalStore(
            postgres_url, settings.lexical_fts_language
        ).query(repo_id=repo_id, text=normalized_query, top_n=candidate_top_n)
    else:
        GLOBAL_BM25.ensure_repo_loaded(repo_id)
        lexical_results = GLOBAL_BM25.query(
            repo_id=repo_id,
            text=normalized_query,
            top_n=candidate_top_n,
        )

    max_lexical_score = max(
        (float(item["score"]) for item in lexical_results), default=0.0
    )
    for item in lexical_results:
        item_id = str(item["id"])
        lexical_score = float(item["score"])
        normalized_lexical = 0.0
        if max_lexical_score > 0:
            normalized_lexical = lexical_score / max_lexical_score
        weighted_lexical = normalized_lexical * BM25_WEIGHT
        scores[item_id] += weighted_lexical
        fused[item_id] = RetrievalChunk(
            id=item_id,
            text=item["text"],
            score=weighted_lexical,
            metadata=item["metadata"],
        )

    for item_id, chunk in fused.items():
        scores[item_id] += _identifier_query_score_adjustment(
            query=query,
            chunk=chunk,
        )

    ranked = sorted(fused.values(), key=lambda item: scores[item.id], reverse=True)
    for chunk in ranked:
        chunk.score = scores[chunk.id]
    return ranked[:top_n]
