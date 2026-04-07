"""Language-specific symbol extraction strategies."""

from coderag.ingestion.extractors.base import SymbolDetection, SymbolExtractor, SymbolSpan
from coderag.ingestion.extractors.registry import (
    DEFAULT_LANGUAGE_EXTRACTOR_REGISTRY,
    LanguageExtractorRegistry,
)

__all__ = [
    "DEFAULT_LANGUAGE_EXTRACTOR_REGISTRY",
    "LanguageExtractorRegistry",
    "SymbolDetection",
    "SymbolExtractor",
    "SymbolSpan",
]
