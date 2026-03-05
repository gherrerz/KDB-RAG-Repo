"""Core tests for ingestion and retrieval components."""

from coderag.core.models import RetrievalChunk, ScannedFile
from coderag.ingestion.chunker import extract_symbol_chunks
from coderag.ingestion.index_bm25 import BM25Index
from coderag.retrieval.context_assembler import assemble_context


def test_extract_symbol_chunks_java_class_method_constructor() -> None:
    """Extracts Java class, constructor, and method symbols."""
    scanned = [
        ScannedFile(
            path="src/AuthService.java",
            language="java",
            content=(
                "public class AuthService {\n"
                "    public AuthService() { }\n"
                "    public String authenticate(String user) { return user; }\n"
                "}\n"
            ),
        )
    ]
    chunks = extract_symbol_chunks(repo_id="repo1", scanned_files=scanned)
    pairs = {(item.symbol_type, item.symbol_name) for item in chunks}
    assert ("class", "AuthService") in pairs
    assert ("constructor", "AuthService") in pairs
    assert ("method", "authenticate") in pairs


def test_extract_symbol_chunks_python_def_and_class() -> None:
    """Extracts class and function symbols from Python content."""
    scanned = [
        ScannedFile(
            path="app/main.py",
            language="python",
            content="class Service:\n    pass\n\n\ndef run():\n    return 1\n",
        )
    ]
    chunks = extract_symbol_chunks(repo_id="repo1", scanned_files=scanned)
    names = {item.symbol_name for item in chunks}
    assert "Service" in names
    assert "run" in names


def test_bm25_returns_ranked_documents() -> None:
    """Returns top document matching exact query terms."""
    index = BM25Index()
    index.build(
        repo_id="r1",
        docs=["def process_payment(order)", "class UserRepository"],
        metadatas=[{"id": "a"}, {"id": "b"}],
    )
    result = index.query(repo_id="r1", text="payment", top_n=1)
    assert result
    assert result[0]["id"] == "a"


def test_assemble_context_applies_token_limit() -> None:
    """Truncates assembled context to configured token budget."""
    chunks = [
        RetrievalChunk(
            id="1",
            text="A" * 1000,
            score=1.0,
            metadata={"path": "a.py", "start_line": 1, "end_line": 2},
        )
    ]
    context = assemble_context(chunks=chunks, graph_records=[], max_tokens=30)
    assert len(context) <= 120
