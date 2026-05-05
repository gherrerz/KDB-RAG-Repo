"""Tests for modular symbol extractors and full-span behavior."""

from coderag.ingestion.extractors.java_brace import JavaBraceExtractor
from coderag.ingestion.extractors.javascript_brace import JavaScriptBraceExtractor
from coderag.ingestion.extractors.python_ast import PythonAstExtractor


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


def test_javascript_brace_extractor_maps_anonymous_default_export_by_filename() -> None:
    """Default exports anónimos usan un nombre sintético estable por archivo."""
    content = (
        "export default () => {\n"
        "  return <div />;\n"
        "};\n"
    )
    extractor = JavaScriptBraceExtractor()

    detections = extractor.detect_symbols(content, path="app/page.tsx")
    target = next(item for item in detections if item.symbol_name == "Page")
    span = extractor.resolve_span(content, target)

    assert target.symbol_type == "function"
    assert span.start_line == 1
    assert span.end_line == 3


def test_javascript_brace_extractor_detects_next_route_handlers_by_http_verb() -> None:
    """Indexa handlers de route.ts por verbo HTTP exportado."""
    content = (
        "export async function GET() {\n"
        "  return Response.json({ ok: true });\n"
        "}\n\n"
        "export async function POST() {\n"
        "  return Response.json({ created: true });\n"
        "}\n"
    )
    extractor = JavaScriptBraceExtractor()

    detections = extractor.detect_symbols(content, path="app/api/users/route.ts")
    names = {item.symbol_name for item in detections}

    assert "GET" in names
    assert "POST" in names


def test_javascript_brace_extractor_detects_styled_component_assignment() -> None:
    """Reconoce styled-components simples como símbolos frontend recuperables."""
    content = "const Button = styled.button`color: red;`;\n"
    extractor = JavaScriptBraceExtractor()

    detections = extractor.detect_symbols(content, path="components/button.tsx")

    assert any(item.symbol_name == "Button" for item in detections)


def test_javascript_brace_extractor_detects_simple_default_hoc_wrapper() -> None:
    """Reconoce HOCs simples exportados por default usando el símbolo envuelto."""
    content = "export default memo(Dashboard);\n"
    extractor = JavaScriptBraceExtractor()

    detections = extractor.detect_symbols(content, path="pages/index.tsx")

    assert any(item.symbol_name == "Dashboard" for item in detections)


def test_javascript_brace_extractor_maps_next_special_files_to_framework_names() -> None:
    """Normaliza nombres sintéticos para archivos especiales de Next.js."""
    content = (
        "export default function() {\n"
        "  return <html />;\n"
        "}\n"
    )
    extractor = JavaScriptBraceExtractor()

    app_detections = extractor.detect_symbols(content, path="pages/_app.tsx")
    middleware_detections = extractor.detect_symbols(
        "export default () => { return NextResponse.next(); };\n",
        path="middleware.ts",
    )

    assert any(item.symbol_name == "_App" for item in app_detections)
    assert any(item.symbol_name == "Middleware" for item in middleware_detections)
