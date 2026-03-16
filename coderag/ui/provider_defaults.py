"""Catálogo compartido de modelos por defecto para providers de UI."""

from coderag.core.provider_model_catalog import (
    default_embedding_model,
    default_llm_model,
    embedding_models_for_provider,
    llm_models_for_provider,
)


__all__ = [
    "default_embedding_model",
    "default_llm_model",
    "embedding_models_for_provider",
    "llm_models_for_provider",
]