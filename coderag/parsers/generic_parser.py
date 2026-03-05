"""Generic parser fallback for non-language-specific chunks."""

from coderag.core.models import ScannedFile, SymbolChunk
from coderag.ingestion.chunker import extract_symbol_chunks


def parse_generic(repo_id: str, file_obj: ScannedFile) -> list[SymbolChunk]:
    """Parse file with generic heuristics into symbol chunks."""
    return extract_symbol_chunks(repo_id, [file_obj])
