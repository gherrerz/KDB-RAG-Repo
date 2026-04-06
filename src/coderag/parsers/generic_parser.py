"""Respaldo genérico del analizador para fragmentos no específicos del idioma."""

from src.coderag.core.models import ScannedFile, SymbolChunk
from src.coderag.ingestion.chunker import extract_symbol_chunks


def parse_generic(repo_id: str, file_obj: ScannedFile) -> list[SymbolChunk]:
    """Analiza el archivo con heurística genérica en fragmentos de símbolos."""
    return extract_symbol_chunks(repo_id, [file_obj])
