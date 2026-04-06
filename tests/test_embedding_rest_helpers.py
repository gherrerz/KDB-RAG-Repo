"""Pruebas unitarias para helpers REST internos de embeddings."""

from src.coderag.ingestion.embedding import (
    _extract_gemini_embeddings,
    _extract_vertex_embeddings,
    _model_path,
    _timeout_value,
    _vertex_model_name,
)


def test_timeout_value_enforces_minimum() -> None:
    """Aplica piso de 1 segundo y default estable."""
    assert _timeout_value(None) == 20.0
    assert _timeout_value(0.2) == 1.0
    assert _timeout_value(8.0) == 8.0


def test_model_path_adds_models_prefix() -> None:
    """Normaliza model path para Gemini REST."""
    assert _model_path("text-embedding-004") == "models/text-embedding-004"
    assert _model_path("models/text-embedding-004") == "models/text-embedding-004"


def test_vertex_model_name_removes_models_prefix() -> None:
    """Normaliza model name para endpoint Vertex publisher models."""
    assert _vertex_model_name("models/text-embedding-005") == "text-embedding-005"
    assert _vertex_model_name("text-embedding-005") == "text-embedding-005"


def test_extract_gemini_embeddings_returns_float_vectors() -> None:
    """Parsea lista embeddings.values a vectores float."""
    payload = {
        "embeddings": [
            {"values": [0.1, 0.2]},
            {"values": [0.3, 0.4]},
        ]
    }

    assert _extract_gemini_embeddings(payload) == [[0.1, 0.2], [0.3, 0.4]]


def test_extract_vertex_embeddings_supports_both_shapes() -> None:
    """Soporta shapes predictions.embeddings.values y predictions.values."""
    payload = {
        "predictions": [
            {"embeddings": {"values": [0.1, 0.2, 0.3]}},
            {"values": [0.4, 0.5, 0.6]},
        ]
    }

    assert _extract_vertex_embeddings(payload) == [
        [0.1, 0.2, 0.3],
        [0.4, 0.5, 0.6],
    ]
