"""Ayudantes de indexación y recuperación BM25 para una coincidencia exacta de términos."""

import json
from pathlib import Path
import re
import unicodedata

from rank_bm25 import BM25Okapi

from coderag.core.settings import get_settings


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_.\-/]+")
CAMEL_BOUNDARY_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

QUERY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "dependencia": ("dependencias", "dependency", "dependencies", "deps", "requirement", "requirements"),
    "dependencias": ("dependencia", "dependency", "dependencies", "deps", "requirement", "requirements"),
    "dependency": ("dependencies", "dependencia", "dependencias", "deps", "requirement", "requirements"),
    "dependencies": ("dependency", "dependencia", "dependencias", "deps", "requirement", "requirements"),
    "requirement": ("requirements", "dependency", "dependencies", "dependencia", "dependencias"),
    "requirements": ("requirement", "dependency", "dependencies", "dependencia", "dependencias"),
    "libreria": ("librerias", "library", "libraries", "package", "packages"),
    "librerias": ("libreria", "library", "libraries", "package", "packages"),
    "library": ("libraries", "libreria", "librerias", "package", "packages"),
    "libraries": ("library", "libreria", "librerias", "package", "packages"),
    "paquete": ("paquetes", "package", "packages"),
    "paquetes": ("paquete", "package", "packages"),
    "package": ("packages", "paquete", "paquetes"),
    "packages": ("package", "paquete", "paquetes"),
}


def _strip_accents(value: str) -> str:
    """Elimina acentos para robustez multilingüe en matching léxico."""
    normalized = unicodedata.normalize("NFD", value)
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def _normalize_token(token: str) -> str:
    """Normaliza token para indexado/consulta BM25."""
    return _strip_accents(token.strip().lower())


def _split_identifier_tokens(token: str) -> list[str]:
    """Descompone identificadores (camelCase, snake_case, kebab-case, rutas)."""
    normalized = CAMEL_BOUNDARY_PATTERN.sub(" ", token)
    pieces = re.split(r"[^A-Za-z0-9]+", normalized)
    return [piece for piece in pieces if piece]


def _singular_variants(token: str) -> set[str]:
    """Genera variantes singulares/plurales simples para mejorar recall léxico."""
    variants: set[str] = set()
    if len(token) <= 2:
        return variants

    if token.endswith("ies") and len(token) > 4:
        variants.add(f"{token[:-3]}y")
    if token.endswith("es") and len(token) > 3:
        variants.add(token[:-2])
    if token.endswith("s") and len(token) > 3:
        variants.add(token[:-1])
    return {variant for variant in variants if variant and variant != token}


def _expand_query_tokens(tokens: list[str]) -> list[str]:
    """Expande tokens de consulta con variantes y sinónimos técnicos ES/EN."""
    expanded: list[str] = []
    seen: set[str] = set()

    def _push(value: str) -> None:
        normalized = _normalize_token(value)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        expanded.append(normalized)

    for token in tokens:
        _push(token)
        for variant in _singular_variants(token):
            _push(variant)
        for synonym in QUERY_SYNONYMS.get(token, ()):  # generic software terms
            _push(synonym)

    return expanded


def tokenize(text: str) -> list[str]:
    """Tokeniza texto de forma robusta para búsquedas léxicas de código."""
    tokens: list[str] = []
    for match in TOKEN_PATTERN.findall(text):
        raw = match.strip()
        if not raw:
            continue

        whole = _normalize_token(raw)
        if whole:
            tokens.append(whole)

        for part in _split_identifier_tokens(raw):
            normalized_part = _normalize_token(part)
            if normalized_part:
                tokens.append(normalized_part)
    return tokens


class BM25Index:
    """Índices BM25 en memoria con ámbito de repositorio."""

    def __init__(self) -> None:
        """Inicialice el almacén vacío para los corpus del repositorio."""
        self._by_repo: dict[str, tuple[BM25Okapi, list[str], list[dict]]] = {}

    def build(self, repo_id: str, docs: list[str], metadatas: list[dict]) -> None:
        """Cree el índice BM25 para un repositorio."""
        corpus = [tokenize(doc) for doc in docs]
        self._by_repo[repo_id] = (BM25Okapi(corpus), docs, metadatas)

    @staticmethod
    def _snapshot_root() -> Path:
        """Devuelve el directorio donde se guardan snapshots de BM25."""
        settings = get_settings()
        root = settings.workspace_path.parent / "bm25"
        root.mkdir(parents=True, exist_ok=True)
        return root

    @classmethod
    def _snapshot_path(cls, repo_id: str) -> Path:
        """Construye la ruta de snapshot para un repositorio."""
        return cls._snapshot_root() / f"{repo_id}.json"

    def persist_repo(self, repo_id: str) -> bool:
        """Persiste docs y metadatas del repo a disco para recuperación post-reinicio."""
        payload = self._by_repo.get(repo_id)
        if payload is None:
            return False

        _bm25, docs, metadatas = payload
        snapshot = {
            "repo_id": repo_id,
            "docs": docs,
            "metadatas": metadatas,
        }
        path = self._snapshot_path(repo_id)
        path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        return True

    def load_repo(self, repo_id: str) -> bool:
        """Carga índice BM25 desde snapshot persistido y lo reconstruye en memoria."""
        path = self._snapshot_path(repo_id)
        if not path.exists():
            return False

        data = json.loads(path.read_text(encoding="utf-8"))
        docs = data.get("docs") or []
        metadatas = data.get("metadatas") or []
        if not docs or len(docs) != len(metadatas):
            return False

        self.build(repo_id=repo_id, docs=docs, metadatas=metadatas)
        return True

    def ensure_repo_loaded(self, repo_id: str) -> bool:
        """Garantiza que el repositorio esté disponible en memoria cargándolo si existe snapshot."""
        if self.has_repo(repo_id):
            return True
        return self.load_repo(repo_id)

    def has_repo_snapshot(self, repo_id: str) -> bool:
        """Indica si existe snapshot persistido para un repositorio."""
        return self._snapshot_path(repo_id).exists()

    def delete_repo(self, repo_id: str) -> dict[str, int]:
        """Elimina índice BM25 de memoria y snapshot de disco para un repositorio."""
        docs_removed = 0
        payload = self._by_repo.pop(repo_id, None)
        if payload is not None:
            _bm25, docs, _metadatas = payload
            docs_removed = len(docs)

        snapshot_removed = 0
        path = self._snapshot_path(repo_id)
        if path.exists():
            path.unlink()
            snapshot_removed = 1

        return {
            "docs_removed": docs_removed,
            "snapshot_removed": snapshot_removed,
        }

    def query(self, repo_id: str, text: str, top_n: int = 50) -> list[dict]:
        """Devuelve las principales coincidencias de BM25 para el repositorio y la consulta."""
        if repo_id not in self._by_repo:
            return []

        bm25, docs, metadatas = self._by_repo[repo_id]
        base_tokens = tokenize(text)
        query_tokens = _expand_query_tokens(base_tokens)
        if not query_tokens:
            query_tokens = base_tokens
        scores = bm25.get_scores(query_tokens)
        pairs = list(enumerate(scores))
        pairs.sort(key=lambda item: item[1], reverse=True)
        result: list[dict] = []
        for index, score in pairs[:top_n]:
            result.append(
                {
                    "id": metadatas[index].get("id"),
                    "text": docs[index],
                    "score": float(score),
                    "metadata": metadatas[index],
                }
            )
        return result

    def clear(self) -> None:
        """Elimine todos los corpus BM25 del repositorio de la memoria."""
        self._by_repo.clear()

    def has_repo(self, repo_id: str) -> bool:
        """Indica si el repositorio tiene un índice BM25 cargado en memoria."""
        return repo_id in self._by_repo

    def repo_count(self) -> int:
        """Devuelve la cantidad de repositorios indexados en memoria."""
        return len(self._by_repo)


GLOBAL_BM25 = BM25Index()
