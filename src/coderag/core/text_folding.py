"""Plegado de tildes compartido por el índice y la consulta léxica.

El full-text search de Postgres se configura con ``simple`` (sin stemming ni
stop-words de un idioma concreto). Esa configuración no elimina diacríticos,
así que el texto se pliega antes de llegar a ``to_tsvector`` y
``plainto_tsquery`` para que ``función`` y ``funcion`` produzcan el mismo lexema.
"""

from __future__ import annotations

import unicodedata


def fold_accents(value: str) -> str:
    """Elimina marcas diacríticas (NFD) sin alterar mayúsculas ni espacios.

    Es idempotente: aplicarla dos veces produce el mismo resultado, por lo que
    puede usarse al indexar y al consultar sin riesgo de doble plegado.
    """
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )


def normalize_search_text(value: str) -> str:
    """Normaliza una consulta: minúsculas, sin tildes y espacios colapsados."""
    return " ".join(fold_accents(value.strip().lower()).split())


def accent_translation_tables() -> tuple[str, str]:
    """Devuelve ``(origen, destino)`` para ``translate()`` de Postgres.

    Cubre las letras latinas acentuadas de ``U+00C0`` a ``U+024F`` (Latin-1,
    Latin Extended-A y Extended-B), con el mismo resultado que
    :func:`fold_accents`. Permite plegar tildes dentro de una sentencia SQL sin
    depender de la extensión ``unaccent`` (que exigiría un cambio de esquema).
    """
    source: list[str] = []
    target: list[str] = []
    for codepoint in range(0xC0, 0x250):
        char = chr(codepoint)
        folded = fold_accents(char)
        if folded != char and len(folded) == 1:
            source.append(char)
            target.append(folded)
    return "".join(source), "".join(target)
