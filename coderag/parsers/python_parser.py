"""Envoltorio del analizador Python para el descubrimiento de símbolos."""

from coderag.core.models import ScannedFile, SymbolChunk
from coderag.ingestion.chunker import extract_symbol_chunks


def parse_python(repo_id: str, file_obj: ScannedFile) -> list[SymbolChunk]:
    """Analice el archivo Python en fragmentos de símbolos."""
    if file_obj.language != "python":
        return []
    return extract_symbol_chunks(repo_id, [file_obj])
