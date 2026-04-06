"""Tests for modular symbol extractors and full-span behavior."""

from src.coderag.ingestion.extractors.java_brace import JavaBraceExtractor
from src.coderag.ingestion.extractors.javascript_brace import JavaScriptBraceExtractor
from src.coderag.ingestion.extractors.python_ast import PythonAstExtractor


def test_python_ast_extractor_resolves_full_function_span() -> None:
    """Python extractor should keep the full function body span."""
    content = (
        "def outer():\n"
        "    x = 1\n"
        "    y = 2\n"
        "    return x + y\n"
    )
    extractor = PythonAstExtractor()
    detections = extractor.detect_symbols(content)

    assert detections
    target = detections[0]
    span = extractor.resolve_span(content, target)

    assert target.symbol_name == "outer"
    assert span.start_line == 1
    assert span.end_line == 4


def test_java_brace_extractor_resolves_balanced_class_span() -> None:
    """Java extractor should close at the matching class brace."""
    content = (
        "public class Demo {\n"
        "    public int sum(int a, int b) {\n"
        "        return a + b;\n"
        "    }\n"
        "}\n"
    )
    extractor = JavaBraceExtractor()
    detections = extractor.detect_symbols(content)

    class_detection = next(item for item in detections if item.symbol_type == "class")
    span = extractor.resolve_span(content, class_detection)

    assert class_detection.symbol_name == "Demo"
    assert span.start_line == 1
    assert span.end_line == 5


def test_javascript_brace_extractor_resolves_function_span() -> None:
    """JS extractor should resolve function body using brace matching."""
    content = (
        "export async function makeUser(name) {\n"
        "  const normalized = name.trim();\n"
        "  return { normalized };\n"
        "}\n"
    )
    extractor = JavaScriptBraceExtractor()
    detections = extractor.detect_symbols(content)

    assert detections
    detection = detections[0]
    span = extractor.resolve_span(content, detection)

    assert detection.symbol_name == "makeUser"
    assert span.start_line == 1
    assert span.end_line == 4


def test_javascript_brace_extractor_detects_typed_arrow_function() -> None:
    """TS arrow exports should be detected as function symbols."""
    content = (
        "export const mapUser = async (input: string): Promise<string> => {\n"
        "  return input.trim();\n"
        "};\n"
    )
    extractor = JavaScriptBraceExtractor()

    detections = extractor.detect_symbols(content)
    target = next(item for item in detections if item.symbol_name == "mapUser")
    span = extractor.resolve_span(content, target)

    assert target.symbol_type == "function"
    assert span.start_line == 1
    assert span.end_line == 3


def test_javascript_brace_extractor_detects_typed_class_method() -> None:
    """TS class methods with return types should be detected as method symbols."""
    content = (
        "export class UserService {\n"
        "  public async build(id: string): Promise<string> {\n"
        "    return id;\n"
        "  }\n"
        "}\n"
    )
    extractor = JavaScriptBraceExtractor()

    detections = extractor.detect_symbols(content)
    target = next(item for item in detections if item.symbol_name == "build")
    span = extractor.resolve_span(content, target)

    assert target.symbol_type == "method"
    assert span.start_line == 2
    assert span.end_line == 4
