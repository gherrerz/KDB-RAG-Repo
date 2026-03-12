"""Ayudantes de indexación y recuperación BM25 para una coincidencia exacta de términos."""

from collections import defaultdict

from rank_bm25 import BM25Okapi


def tokenize(text: str) -> list[str]:
    """Tokenice el texto con una simple normalización de espacios en blanco."""
    return text.lower().replace("\n", " ").split()


class BM25Index:
    """Índices BM25 en memoria con ámbito de repositorio."""

    def __init__(self) -> None:
        """Inicialice el almacén vacío para los corpus del repositorio."""
        self._by_repo: dict[str, tuple[BM25Okapi, list[str], list[dict]]] = {}

    def build(self, repo_id: str, docs: list[str], metadatas: list[dict]) -> None:
        """Cree el índice BM25 para un repositorio."""
        corpus = [tokenize(doc) for doc in docs]
        self._by_repo[repo_id] = (BM25Okapi(corpus), docs, metadatas)

    def query(self, repo_id: str, text: str, top_n: int = 50) -> list[dict]:
        """Devuelve las principales coincidencias de BM25 para el repositorio y la consulta."""
        if repo_id not in self._by_repo:
            return []

        bm25, docs, metadatas = self._by_repo[repo_id]
        scores = bm25.get_scores(tokenize(text))
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
