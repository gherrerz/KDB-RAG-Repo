"""Tests for the Swift Tree-sitter extractor Fase 1 scope."""

from coderag.ingestion.extractors.swift_treesitter import SwiftTreeSitterExtractor


def test_swift_treesitter_detects_fase1_symbol_types() -> None:
    """Detect Swift class-like declarations and supported callables."""
    content = (
        "class Demo {\n"
        "    init() {}\n"
        "    func run() {}\n"
        "}\n\n"
        "extension Demo {\n"
        "    func extendedRun() {}\n"
        "}\n\n"
        "struct Model {}\n\n"
        "protocol Contract {\n"
        "    func contractRun()\n"
        "}\n\n"
        "enum Mode {\n"
        "    case ready\n"
        "}\n\n"
        "func helper() {}\n"
    )
    extractor = SwiftTreeSitterExtractor()

    detections = extractor.detect_symbols(content)
    detected = {(item.symbol_name, item.symbol_type) for item in detections}

    assert ("Demo", "class") in detected
    assert ("Demo", "constructor") in detected
    assert ("Demo", "extension") in detected
    assert ("run", "method") in detected
    assert ("extendedRun", "method") in detected
    assert ("contractRun", "method") in detected
    assert ("Model", "struct") in detected
    assert ("Contract", "protocol") in detected
    assert ("Mode", "enum") in detected
    assert ("helper", "function") in detected


def test_swift_treesitter_resolves_protocol_method_span() -> None:
    """Resolve spans for Swift protocol methods declared without body."""
    content = (
        "protocol Contract {\n"
        "    func contractRun()\n"
        "}\n"
    )
    extractor = SwiftTreeSitterExtractor()

    detections = extractor.detect_symbols(content)
    method_detection = next(
        item for item in detections if item.symbol_name == "contractRun"
    )

    method_span = extractor.resolve_span(content, method_detection)

    assert method_span.start_line == 2
    assert method_span.end_line == 2


def test_swift_treesitter_resolves_multiline_spans() -> None:
    """Resolve spans for Swift class, method, and top-level function bodies."""
    content = (
        "class Demo {\n"
        "    init() {}\n"
        "    func run() {\n"
        "        print(\"ok\")\n"
        "    }\n"
        "}\n\n"
        "func helper() {\n"
        "    print(\"ok\")\n"
        "}\n"
    )
    extractor = SwiftTreeSitterExtractor()

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


def test_swift_treesitter_resolves_extension_and_method_spans() -> None:
    """Resolve spans for Swift extension declarations and their methods."""
    content = (
        "extension Demo {\n"
        "    func extendedRun() {\n"
        "        print(\"ok\")\n"
        "    }\n"
        "}\n"
    )
    extractor = SwiftTreeSitterExtractor()

    detections = extractor.detect_symbols(content)
    extension_detection = next(
        item
        for item in detections
        if item.symbol_name == "Demo" and item.symbol_type == "extension"
    )
    method_detection = next(
        item for item in detections if item.symbol_name == "extendedRun"
    )

    extension_span = extractor.resolve_span(content, extension_detection)
    method_span = extractor.resolve_span(content, method_detection)

    assert extension_span.start_line == 1
    assert extension_span.end_line == 5
    assert method_span.start_line == 2
    assert method_span.end_line == 4