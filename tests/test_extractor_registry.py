"""Tests for language extractor registry extensibility and fallback behavior."""

from coderag.ingestion.extractors.base import SymbolDetection, SymbolSpan
from coderag.ingestion.extractors.registry import LanguageExtractorRegistry


class _DummyExtractor:
    """Minimal extractor used to validate registry extension points."""

    def detect_symbols(self, content: str) -> list[SymbolDetection]:
        """Return a deterministic symbol for test assertions."""
        return [
            SymbolDetection(
                symbol_name="dummy",
                symbol_type="function",
                start_line=1,
            )
        ]

    def resolve_span(
        self,
        content: str,
        detection: SymbolDetection,
    ) -> SymbolSpan:
        """Return deterministic span for test assertions."""
        return SymbolSpan(start_line=detection.start_line, end_line=detection.start_line)


def test_registry_resolves_alias_to_same_js_ts_extractor() -> None:
    """JS and TS aliases should resolve to a shared extractor class."""
    registry = LanguageExtractorRegistry()

    js_extractor = registry.get("js")
    ts_extractor = registry.get("ts")

    assert js_extractor.__class__ == ts_extractor.__class__


def test_registry_returns_fallback_for_unknown_language() -> None:
    """Unknown languages should use the generic fallback extractor."""
    registry = LanguageExtractorRegistry()

    fallback_extractor = registry.get("unknown_lang")
    detections = fallback_extractor.detect_symbols("def run():\n    pass\n")

    assert detections
    assert detections[0].symbol_name == "run"


def test_registry_register_allows_custom_language_strategy() -> None:
    """Custom extractors can be registered without editing chunker orchestration."""
    registry = LanguageExtractorRegistry()
    custom = _DummyExtractor()

    registry.register("ruby", custom)
    resolved = registry.get("ruby")

    assert resolved is custom
    assert resolved.detect_symbols("anything")[0].symbol_name == "dummy"
