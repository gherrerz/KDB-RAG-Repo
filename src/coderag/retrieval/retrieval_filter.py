"""Filtro opcional por ruta y lenguaje para consultas retrieval-only.

Un único helper (:class:`RetrievalFilter`) se usa en las tres capas donde el
filtro debe cumplirse, de modo que compartan exactamente la misma semántica:

- pata vectorial (Chroma): el lenguaje se empuja al ``where`` de la consulta;
- pata léxica (Postgres): no hay columna de lenguaje, así que se deriva de la
  extensión con ``LANG_MAP`` y se filtra en Python tras la consulta;
- garantía final: se reaplica sobre los chunks, citas y registros de grafo que
  salen del pipeline (expansión de grafo y atajos graph-first/componente).

Semántica de ``path_globs``:

- Se comparan contra la ruta relativa al repositorio con separador ``/``
  (las ``\\`` se normalizan y se ignora un ``./`` o ``/`` inicial).
- Distinguen mayúsculas y minúsculas.
- ``*`` y ``?`` no cruzan ``/``; ``[abc]`` y ``[!abc]`` son clases de
  caracteres; ``**`` cruza directorios. ``**/`` equivale a "cero o más
  directorios", por lo que ``**/*.tsx`` acepta ``app.tsx`` y ``src/app.tsx``.
- Clases de caracteres (misma semántica que ``fnmatch``, y la traducción es
  total: nunca lanza ``re.error``): ``[!x]`` niega y ``[a-c]`` es un rango.
  Un ``]`` justo tras ``[`` o ``[!`` es un miembro literal (``[]a]``). Dentro
  de la clase ``^``, ``[``, ``&``, ``~`` y ``|`` son literales (y ``-`` lo
  es al inicio o al final), por lo que ``[^x]`` acepta ``^`` y ``x`` y no
  niega. Las ``\\`` del glob ya se normalizaron a ``/``, así que no llegan a
  la clase. Una ``[`` sin ``]`` de cierre, o cuya clase trae un rango
  invertido (``[z-a]``), no abre una clase: se toma como ``[`` literal y el
  resto del glob se interpreta con normalidad. Este último caso difiere de
  ``fnmatch``, que descarta el rango y deja una clase vacía que nunca
  coincide.
- No hay coincidencia implícita por nombre base: ``*.tsx`` solo acepta
  archivos en la raíz. Un glob terminado en ``/`` equivale a ``<glob>**``.
- Varios globs se combinan con OR.

Semántica de ``languages``: etiquetas de ``LANG_MAP`` (por ejemplo
``typescript``), sin distinguir mayúsculas; varias se combinan con OR. Los
resúmenes de módulo tienen lenguaje ``module``. ``path_globs`` y ``languages``
se combinan con AND. Sin ninguno de los dos no hay filtro.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

from coderag.core.models import RetrievalChunk
from coderag.ingestion.repo_scanner import detect_language

MODULE_LANGUAGE = "module"


def normalize_repo_path(path: str) -> str:
    """Normaliza una ruta a separador ``/`` sin prefijo ``./`` ni ``/``."""
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def _escape_class_char(char: str) -> str:
    """Escapa un carácter para usarlo dentro de una clase de regex."""
    return "\\" + char if char in "\\[]^-&~|" else char


def _translate_bracket_class(
    pattern: str,
    start: int,
) -> tuple[str, int] | None:
    """Traduce la clase ``[...]`` que abre en ``start`` a regex.

    Devuelve ``(regex, índice tras el cierre)`` o ``None`` cuando la ``[`` no
    abre una clase válida (sin cierre o con un rango invertido) y debe
    tratarse como literal. El resultado siempre compila.
    """
    index = start + 1
    negated = pattern.startswith("!", index)
    if negated:
        index += 1
    # Un `]` inicial es miembro literal, no el cierre de la clase.
    closing = pattern.find("]", index + 1)
    if closing == -1:
        return None
    body = pattern[index:closing]
    members: list[str] = []
    position = 0
    while position < len(body):
        if position + 2 < len(body) and body[position + 1] == "-":
            low, high = body[position], body[position + 2]
            if low > high:
                return None
            members.append(
                f"{_escape_class_char(low)}-{_escape_class_char(high)}"
            )
            position += 3
        else:
            members.append(_escape_class_char(body[position]))
            position += 1
    prefix = "^" if negated else ""
    return "[" + prefix + "".join(members) + "]", closing + 1


@lru_cache(maxsize=256)
def _compile_glob(glob: str) -> re.Pattern[str]:
    """Traduce un glob con soporte ``**`` a una expresión regular anclada."""
    pattern = normalize_repo_path(glob)
    if pattern.endswith("/"):
        pattern += "**"
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**", index):
            if pattern.startswith("**/", index):
                parts.append("(?:.*/)?")
                index += 3
            else:
                parts.append(".*")
                index += 2
        elif char == "*":
            parts.append("[^/]*")
            index += 1
        elif char == "?":
            parts.append("[^/]")
            index += 1
        elif char == "[":
            translated = _translate_bracket_class(pattern, index)
            if translated is None:
                parts.append(re.escape(char))
                index += 1
            else:
                class_regex, index = translated
                parts.append(class_regex)
        else:
            parts.append(re.escape(char))
            index += 1
    return re.compile("".join(parts))


def validate_path_glob(glob: str) -> None:
    """Verifica que el glob se pueda traducir y compilar.

    La traducción es total, así que esta guarda es defensiva: convierte un
    ``re.error`` inesperado en ``ValueError`` para que la validación de la
    solicitud responda 422 en vez de fallar con 500 al filtrar.
    """
    try:
        _compile_glob(glob)
    except re.error as exc:
        raise ValueError(f"Glob de path_globs inválido: {exc}") from exc


def path_matches_glob(path: str, glob: str) -> bool:
    """Indica si una ruta relativa cumple un glob (semántica del módulo)."""
    return _compile_glob(glob).fullmatch(normalize_repo_path(path)) is not None


def language_for_path(path: str) -> str:
    """Deriva la etiqueta de lenguaje desde la extensión (``LANG_MAP``)."""
    return detect_language(Path(normalize_repo_path(path)))


def chunk_language(metadata: dict[str, Any]) -> str:
    """Resuelve el lenguaje efectivo de un chunk a partir de su metadata.

    Prioriza el ``language`` persistido (Chroma) y, si falta (léxico), lo
    deriva de la ruta. Los resúmenes de módulo se reportan como ``module``.
    """
    if str(metadata.get("entity_type") or "") == "module":
        return MODULE_LANGUAGE
    stored = str(metadata.get("language") or "").strip().lower()
    if stored:
        return stored
    return language_for_path(str(metadata.get("path") or ""))


@dataclass(frozen=True)
class RetrievalFilter:
    """Filtro inmutable por globs de ruta y lenguajes (ambos opcionales)."""

    path_globs: tuple[str, ...] = ()
    languages: frozenset[str] = frozenset()

    @classmethod
    def from_request(
        cls,
        path_globs: Sequence[str] | None,
        languages: Sequence[str] | None,
    ) -> "RetrievalFilter | None":
        """Construye el filtro o devuelve ``None`` si no hay criterios."""
        globs = tuple(
            dict.fromkeys(
                item.strip() for item in (path_globs or ()) if item.strip()
            )
        )
        langs = frozenset(
            item.strip().lower() for item in (languages or ()) if item.strip()
        )
        if not globs and not langs:
            return None
        return cls(path_globs=globs, languages=langs)

    def matches_path(self, path: str) -> bool:
        """Cumple los globs de ruta (siempre verdadero si no hay globs)."""
        if not self.path_globs:
            return True
        return any(path_matches_glob(path, glob) for glob in self.path_globs)

    def matches_metadata(self, metadata: dict[str, Any]) -> bool:
        """Cumple ruta y lenguaje según la metadata de un chunk."""
        if not self.matches_path(str(metadata.get("path") or "")):
            return False
        if not self.languages:
            return True
        return chunk_language(metadata) in self.languages

    def matches_path_only_entry(self, path: str) -> bool:
        """Cumple ruta y lenguaje para entradas que solo traen una ruta."""
        return self.matches_metadata({"path": path})

    def filter_chunks(
        self,
        chunks: Iterable[RetrievalChunk],
    ) -> list[RetrievalChunk]:
        """Conserva los chunks que cumplen el filtro."""
        return [
            chunk for chunk in chunks if self.matches_metadata(chunk.metadata)
        ]

    def filter_graph_records(self, records: Iterable[dict]) -> list[dict]:
        """Descarta registros de grafo cuya ruta de archivo incumple el filtro.

        Un registro sin ruta de repositorio (por ejemplo un símbolo externo
        sin ``source_path``) no puede violar el filtro y se conserva.
        """
        kept: list[dict] = []
        for record in records:
            props = record.get("props") or {}
            path = str(
                props.get("path")
                or record.get("source_path")
                or props.get("source_path")
                or ""
            ).strip()
            if path and not self.matches_path_only_entry(path):
                continue
            kept.append(record)
        return kept

    def chroma_where(self, repo_id: str) -> dict[str, Any]:
        """Arma el ``where`` de Chroma: repo y, si aplica, lenguajes.

        Los globs no se pueden expresar en Chroma y se aplican en Python.
        """
        repo_clause: dict[str, Any] = {"repo_id": repo_id}
        if not self.languages:
            return repo_clause
        return {
            "$and": [
                repo_clause,
                {"language": {"$in": sorted(self.languages)}},
            ]
        }

    def describe(self) -> dict[str, list[str]]:
        """Representación estable para diagnostics."""
        return {
            "path_globs": list(self.path_globs),
            "languages": sorted(self.languages),
        }
