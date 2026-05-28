"""Tests for the Kotlin Tree-sitter extractor Fase 1 scope."""

from coderag.ingestion.extractors.kotlin_treesitter import KotlinTreeSitterExtractor


def test_kotlin_treesitter_detects_fase1_symbol_types() -> None:
    """Detect Kotlin class-like declarations and supported callables."""
    content = (
        "class Demo(val value: Int) {\n"
        "    constructor(): this(0)\n"
        "    fun run() {}\n"
        "}\n\n"
        "fun helper() {}\n"
    )
    extractor = KotlinTreeSitterExtractor()

    detections = extractor.detect_symbols(content)
    detected = {(item.symbol_name, item.symbol_type) for item in detections}

    assert ("Demo", "class") in detected
    assert ("Demo", "constructor") in detected
    assert ("run", "method") in detected
    assert ("helper", "function") in detected


def test_kotlin_treesitter_detects_interface_and_enum() -> None:
    """Map Kotlin interface and enum declarations to public symbol types."""
    content = (
        "interface Contract {\n"
        "    fun run()\n"
        "}\n\n"
        "enum class Mode { A, B }\n"
    )
    extractor = KotlinTreeSitterExtractor()

    detections = extractor.detect_symbols(content)
    detected = {(item.symbol_name, item.symbol_type) for item in detections}

    assert ("Contract", "interface") in detected
    assert ("Mode", "enum") in detected


def test_kotlin_treesitter_resolves_multiline_spans() -> None:
    """Resolve spans for Kotlin class, method, and top-level function bodies."""
    content = (
        "class Demo(val value: Int) {\n"
        "    constructor(): this(0)\n"
        "    fun run() {\n"
        "        println(value)\n"
        "    }\n"
        "}\n\n"
        "fun helper() {\n"
        "    println(\"ok\")\n"
        "}\n"
    )
    extractor = KotlinTreeSitterExtractor()

    detections = extractor.detect_symbols(content)
    class_detection = next(item for item in detections if item.symbol_name == "Demo" and item.symbol_type == "class")
    method_detection = next(item for item in detections if item.symbol_name == "run")
    function_detection = next(item for item in detections if item.symbol_name == "helper")

    class_span = extractor.resolve_span(content, class_detection)
    method_span = extractor.resolve_span(content, method_detection)
    function_span = extractor.resolve_span(content, function_detection)

    assert class_span.start_line == 1
    assert class_span.end_line == 6
    assert method_span.start_line == 3
    assert method_span.end_line == 5
    assert function_span.start_line == 8
    assert function_span.end_line == 10