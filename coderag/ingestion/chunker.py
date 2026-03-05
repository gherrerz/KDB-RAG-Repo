"""Chunk builders for symbols, files, and modules."""

import hashlib
import re

from coderag.core.models import ScannedFile, SymbolChunk


def _chunk_id(repo_id: str, path: str, name: str, start_line: int) -> str:
    """Build deterministic chunk ID for symbol snippets."""
    value = f"{repo_id}:{path}:{name}:{start_line}".encode("utf-8")
    return hashlib.sha1(value).hexdigest()


def extract_symbol_chunks(repo_id: str, scanned_files: list[ScannedFile]) -> list[SymbolChunk]:
    """Extract approximate symbol-level chunks using regex heuristics."""
    chunks: list[SymbolChunk] = []
    for file_obj in scanned_files:
        lines = file_obj.content.splitlines()
        for index, line in enumerate(lines):
            py_match = re.match(r"\s*(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            js_match = re.match(r"\s*function\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            java_match = re.match(
                r"\s*(public|private|protected)?\s*(class|interface)\s+"
                r"([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            java_method_match = re.match(
                r"\s*(public|private|protected)?\s*(static\s+)?"
                r"([A-Za-z_][A-Za-z0-9_<>\[\]]*\s+)+"
                r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*(\{|$)",
                line,
            )
            java_constructor_match = re.match(
                r"\s*(public|private|protected)\s+"
                r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*(\{|$)",
                line,
            )

            symbol_type = ""
            symbol_name = ""
            if py_match:
                symbol_type = "class" if py_match.group(1) == "class" else "function"
                symbol_name = py_match.group(2)
            elif js_match:
                symbol_type = "function"
                symbol_name = js_match.group(1)
            elif java_match:
                symbol_type = java_match.group(2)
                symbol_name = java_match.group(3)
            elif file_obj.language == "java" and java_constructor_match:
                symbol_type = "constructor"
                symbol_name = java_constructor_match.group(2)
            elif file_obj.language == "java" and java_method_match:
                symbol_type = "method"
                symbol_name = java_method_match.group(4)
            else:
                continue

            start_line = index + 1
            end_line = min(start_line + 30, len(lines))
            snippet = "\n".join(lines[start_line - 1 : end_line])
            chunks.append(
                SymbolChunk(
                    id=_chunk_id(repo_id, file_obj.path, symbol_name, start_line),
                    repo_id=repo_id,
                    path=file_obj.path,
                    language=file_obj.language,
                    symbol_name=symbol_name,
                    symbol_type=symbol_type,
                    start_line=start_line,
                    end_line=end_line,
                    snippet=snippet,
                )
            )
    return chunks
