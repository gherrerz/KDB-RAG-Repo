"""Envoltorio del analizador Java para el descubrimiento de símbolos."""

from coderag.core.models import ScannedFile, SymbolChunk
from coderag.ingestion.chunker import extract_symbol_chunks


def parse_java(repo_id: str, file_obj: ScannedFile) -> list[SymbolChunk]:
    """Analice el archivo Java en fragmentos de símbolos."""
    if file_obj.language != "java":
        return []
    return extract_symbol_chunks(repo_id, [file_obj])
