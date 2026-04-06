"""Registry that resolves a symbol extractor for each language."""

from src.coderag.ingestion.extractors.base import SymbolExtractor
from src.coderag.ingestion.extractors.generic_fallback import GenericFallbackExtractor
from src.coderag.ingestion.extractors.java_brace import JavaBraceExtractor
from src.coderag.ingestion.extractors.javascript_brace import JavaScriptBraceExtractor
from src.coderag.ingestion.extractors.python_ast import PythonAstExtractor


class LanguageExtractorRegistry:
    """Holds extractor strategy instances indexed by normalized language."""

    def __init__(self) -> None:
        """Create registry with built-in extractor mappings."""
        self._fallback = GenericFallbackExtractor()
        self._extractors: dict[str, SymbolExtractor] = {
            "python": PythonAstExtractor(),
            "java": JavaBraceExtractor(),
            "javascript": JavaScriptBraceExtractor(),
            "typescript": JavaScriptBraceExtractor(),
            "js": JavaScriptBraceExtractor(),
            "ts": JavaScriptBraceExtractor(),
        }

    def get(self, language: str | None) -> SymbolExtractor:
        """Resolve extractor by language with fallback behavior."""
        normalized = self._normalize(language)
        return self._extractors.get(normalized, self._fallback)

    def register(self, language: str, extractor: SymbolExtractor) -> None:
        """Register a custom extractor for a language key."""
        self._extractors[self._normalize(language)] = extractor

    @staticmethod
    def _normalize(language: str | None) -> str:
        """Normalize language token for consistent lookups."""
        return (language or "").strip().lower()


DEFAULT_LANGUAGE_EXTRACTOR_REGISTRY = LanguageExtractorRegistry()
