"""Java parser wrapper for symbol discovery."""

from coderag.core.models import ScannedFile, SymbolChunk
from coderag.ingestion.chunker import extract_symbol_chunks


def parse_java(repo_id: str, file_obj: ScannedFile) -> list[SymbolChunk]:
    """Parse Java file into symbol chunks."""
    if file_obj.language != "java":
        return []
    return extract_symbol_chunks(repo_id, [file_obj])
