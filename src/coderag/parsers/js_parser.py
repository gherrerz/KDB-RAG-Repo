"""Envoltorio del analizador de JavaScript para el descubrimiento de símbolos."""

from coderag.core.models import ScannedFile, SymbolChunk
from coderag.ingestion.chunker import extract_symbol_chunks


def parse_javascript(repo_id: str, file_obj: ScannedFile) -> list[SymbolChunk]:
    """Analice el archivo JavaScript en fragmentos de símbolos."""
    if file_obj.language not in {"javascript", "typescript"}:
        return []
    return extract_symbol_chunks(repo_id, [file_obj])
