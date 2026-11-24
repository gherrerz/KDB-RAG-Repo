"""Pruebas del plegado de tildes compartido por índice y consulta léxica."""

from coderag.core.text_folding import (
    accent_translation_tables,
    fold_accents,
    normalize_search_text,
)


def test_fold_accents_removes_diacritics_and_keeps_case() -> None:
    """Quita tildes, diéresis y virguilla sin tocar mayúsculas ni espacios."""
    assert fold_accents("Función  Ñandú Pingüino") == "Funcion  Nandu Pinguino"


def test_fold_accents_is_idempotent() -> None:
    """Plegar dos veces equivale a plegar una."""
    once = fold_accents("Configuración del árbol")
    assert fold_accents(once) == once


def test_fold_accents_keeps_non_latin_text() -> None:
    """Texto sin marcas combinantes queda igual."""
    assert fold_accents("def foo_bar(): pass") == "def foo_bar(): pass"


def test_normalize_search_text_lowercases_and_collapses_spaces() -> None:
    """La consulta queda en minúsculas, sin tildes y con espacios simples."""
    assert normalize_search_text("  ¿Dónde   está la SESIÓN? ") == (
        "¿donde esta la sesion?"
    )


def test_accent_translation_tables_match_fold_accents() -> None:
    """Las tablas de ``translate()`` producen lo mismo que ``fold_accents``."""
    source, target = accent_translation_tables()
    table = str.maketrans(source, target)
    sample = "áéíóúüñÁÉÍÓÚÜÑàèâêôçãõ"

    assert len(source) == len(target)
    assert sample.translate(table) == fold_accents(sample)


def test_accent_translation_tables_are_safe_sql_literals() -> None:
    """Las tablas no traen comillas, backslash ni ``:`` (bind de text())."""
    source, target = accent_translation_tables()
    for forbidden in ("'", "\\", ":", "%"):
        assert forbidden not in source + target
