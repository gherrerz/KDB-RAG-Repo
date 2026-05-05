"""Pruebas de soporte de descubrimiento de módulos en el servicio de consultas."""

from pathlib import Path
from time import monotonic

import pytest

import coderag.api.query_service as query_service
from coderag.core.models import Citation, InventoryItem, InventoryQueryResponse, RetrievalChunk


def test_is_module_query_detects_spanish_and_english_terms() -> None:
    """Identifica intenciones de consulta relacionadas con módulos en variantes comunes."""
    assert query_service._is_module_query("Cuales son los modulos?")
    assert query_service._is_module_query("list repository modules")
    assert not query_service._is_module_query("donde se define auth")


def test_discover_repo_modules_uses_persisted_graph_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Devuelve módulos persistidos en grafo y mantiene filtros de nombres excluidos."""

    class _FakeGraphBuilder:
        def query_repo_modules(self, repo_id: str) -> list[str]:
            assert repo_id == "repo1"
            return ["api-service", "web-client", "docs", "node_modules"]

        def close(self) -> None:
            return None

    monkeypatch.setattr(query_service, "GraphBuilder", _FakeGraphBuilder)

    modules = query_service._discover_repo_modules("repo1")
    assert "api-service" in modules
    assert "web-client" in modules
    assert "docs" not in modules
    assert "node_modules" not in modules


def test_is_inventory_query_detection() -> None:
    """Solo detecta inventario cuando el usuario lo pide explícitamente."""
    assert query_service._is_inventory_query(
        "dame el inventario de services del modulo api-service"
    )
    assert query_service._is_inventory_query("show inventory of controllers")
    assert query_service._is_inventory_query(
        "inventario: cuales son los service del modulo api-service"
    )
    assert not query_service._is_inventory_query("list all controllers in module")
    assert not query_service._is_inventory_query("que hace autenticacion")


def test_extract_inventory_target_for_es_and_en() -> None:
    """Extrae el token de destino de inventario normalizado de la consulta del usuario."""
    assert query_service._extract_inventory_target("todos los services del modulo") == "service"
    assert query_service._extract_inventory_target("all controllers in api-service") == "controller"
    assert (
        query_service._extract_inventory_target(
            "cuales son todos los controladores de mall-portal"
        )
        == "controlador"
    )
    assert (
        query_service._extract_inventory_target(
            "cuales son los componentes de la carpeta core"
        )
        == "componente"
    )
    assert (
        query_service._extract_inventory_target(
            "cuales son todas las clases de ingestion"
        )
        == "clase"
    )
    assert (
        query_service._extract_inventory_target(
            "cuales son las dependencias del proyecto"
        )
        == "dependencia"
    )
    assert (
        query_service._extract_inventory_target(
            "which dependencies are used by this project"
        )
        == "dependency"
    )


def test_extract_inventory_target_explicit_type_specification() -> None:
    """Prioriza especificadores de tipo explícito ('de tipo X') sobre términos genéricos."""
    # User's original query: "me puedes listar todos los componentes de tipo controller de mall-portal"
    # Should extract "controller", not "componentes"
    assert (
        query_service._extract_inventory_target(
            "me puedes listar todos los componentes de tipo controller de mall-portal"
        )
        == "controller"
    )
    # Direct type specification with "tipo"
    assert (
        query_service._extract_inventory_target(
            "componentes tipo model en mall-portal"
        )
        == "model"
    )
    # Variant: "de tipo" with different component term
    assert (
        query_service._extract_inventory_target(
            "elementos de tipo handler"
        )
        == "handler"
    )
    # Variant: just "tipo" (shorter form)
    assert (
        query_service._extract_inventory_target(
            "dame los servicios tipo repository"
        )
        == "repository"
    )


def test_is_inventory_explain_query_detection() -> None:
    """Detecta inventario compuesto + solicitudes de explicación."""
    assert query_service._is_inventory_explain_query(
        "cuales son los componentes de core y que funcion cumplen"
    )
    assert query_service._is_inventory_explain_query(
        "list all services and explain what each one does"
    )
    assert not query_service._is_inventory_explain_query(
        "cuales son los componentes de core"
    )


def test_inventory_term_aliases_expand_for_multilingual_queries() -> None:
    """Amplía el objetivo del inventario para incluir alias en plural y en varios idiomas."""
    aliases = query_service._inventory_term_aliases("servicios")
    assert "servicio" in aliases
    assert "service" in aliases
    assert "services" in aliases
    assert "servicees" not in aliases


def test_inventory_term_aliases_expand_for_controllers() -> None:
    """Expande controladores en plural (español) a variantes canónicas en español/inglés."""
    aliases = query_service._inventory_term_aliases("controladores")
    assert "controlador" in aliases
    assert "controladores" in aliases
    assert "controller" in aliases
    assert "controllers" in aliases


def test_inventory_term_aliases_expand_for_classes() -> None:
    """Expande clases en plural (español) a variantes canónicas en español/inglés."""
    aliases = query_service._inventory_term_aliases("clases")
    assert "clase" in aliases
    assert "clases" in aliases
    assert "class" in aliases
    assert "classes" in aliases


def test_inventory_term_aliases_expand_for_dependencies() -> None:
    """Expande dependencias a variantes canónicas en español/inglés."""
    aliases = query_service._inventory_term_aliases("dependencias")
    assert "dependencia" in aliases
    assert "dependencias" in aliases
    assert "dependency" in aliases
    assert "dependencies" in aliases


def test_query_inventory_entities_merges_alias_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fusiona y deduplica coincidencias de inventario provenientes de términos de alias."""

    class _Graph:
        def __init__(self) -> None:
            self.seen_terms: list[str] = []

        def query_inventory(
            self,
            repo_id: str,
            target_term: str,
            module_name: str | None,
            limit: int,
        ) -> list[dict]:
            self.seen_terms.append(target_term)
            if target_term == "service":
                return [
                    {
                        "label": "HomeService.java",
                        "path": "src/HomeService.java",
                        "start_line": 1,
                        "end_line": 1,
                    }
                ]
            if target_term == "servicio":
                return [
                    {
                        "label": "HomeService.java",
                        "path": "src/HomeService.java",
                        "start_line": 1,
                        "end_line": 1,
                    },
                    {
                        "label": "OrderService.java",
                        "path": "src/OrderService.java",
                        "start_line": 1,
                        "end_line": 1,
                    },
                ]
            return []

        def close(self) -> None:
            return None

    graph = _Graph()
    monkeypatch.setattr(query_service, "GraphBuilder", lambda: graph)

    entities = query_service._query_inventory_entities(
        repo_id="repo1",
        target_term="servicios",
        module_name=None,
    )
    paths = [item["path"] for item in entities]

    assert "service" in graph.seen_terms
    assert "servicio" in graph.seen_terms
    assert paths == ["src/HomeService.java", "src/OrderService.java"]


def test_query_inventory_entities_uses_module_file_listing_for_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Utiliza un listado directo de archivos de módulos para solicitudes de inventario de componentes amplios."""

    class _Graph:
        def __init__(self) -> None:
            self.called_module_files = False

        def query_module_files(
            self,
            repo_id: str,
            module_name: str,
            limit: int,
        ) -> list[dict]:
            self.called_module_files = True
            assert repo_id == "repo1"
            assert module_name == "core"
            return [
                {
                    "label": "settings.py",
                    "path": "core/settings.py",
                    "start_line": 1,
                    "end_line": 1,
                }
            ]

        def query_inventory(self, *args, **kwargs) -> list[dict]:
            raise AssertionError("query_inventory should not run for broad components")

        def close(self) -> None:
            return None

    graph = _Graph()
    monkeypatch.setattr(query_service, "GraphBuilder", lambda: graph)

    entities = query_service._query_inventory_entities(
        repo_id="repo1",
        target_term="componentes",
        module_name="core",
    )

    assert graph.called_module_files
    assert len(entities) == 1
    assert entities[0]["path"] == "core/settings.py"


def test_extract_module_name_is_generic() -> None:
    """Extrae nombres de módulos de frases de consulta genéricas en español/inglés."""
    assert query_service._extract_module_name("modulo api-service") == "api-service"
    assert query_service._extract_module_name("in web/client") == "web/client"
    assert (
        query_service._extract_module_name(
            "cuales son los componentes de la carpeta core y que funcion cumplen"
        )
        == "core"
    )
    assert (
        query_service._extract_module_name(
            "traeme todos los servicios de mall-portal"
        )
        == "mall-portal"
    )
    assert (
        query_service._extract_module_name(
            "cuales son las dependencias del proyecto"
        )
        is None
    )
    assert (
        query_service._extract_module_name(
            "which dependencies are used by the project"
        )
        is None
    )


def test_build_purpose_from_source_uses_python_docstring(tmp_path: Path) -> None:
    """Utiliza la cadena de documentación del módulo como propósito del componente cuando esté disponible."""
    file_path = tmp_path / "service.py"
    file_path.write_text(
        '"""Orquesta validaciones de consultas y enrutamiento."""\n\n'
        "def run() -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )

    purpose = query_service._build_purpose_from_source(file_path)

    assert purpose is not None
    assert "Orquesta validaciones" in purpose


def test_build_purpose_from_source_uses_filename_heuristic(tmp_path: Path) -> None:
    """Usa una alternativa basada en heurística de nombre de archivo cuando el código fuente no aporta pistas descriptivas."""
    file_path = tmp_path / "logging.py"
    file_path.write_text("x = 1\n", encoding="utf-8")

    purpose = query_service._build_purpose_from_source(file_path)

    assert purpose is not None
    assert "logging" in purpose.lower()


def test_resolve_module_scope_prefers_nested_graph_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resuelve el token del módulo en una ruta canónica anidada usando Neo4j."""

    class _FakeGraphBuilder:
        def query_repo_modules(self, repo_id: str) -> list[str]:
            assert repo_id == "repo1"
            return ["src/coderag/core", "src/coderag/api"]

        def close(self) -> None:
            return None

    monkeypatch.setattr(query_service, "GraphBuilder", _FakeGraphBuilder)

    scope = query_service._resolve_module_scope(repo_id="repo1", module_name="core")
    assert scope == "src/coderag/core"


def test_extractive_fallback_limits_non_inventory_results() -> None:
    """Muestra una lista extractiva compacta para consultas que no son de inventario."""
    citations = [
        Citation(
            path=f"src/File{i}.java",
            start_line=1,
            end_line=1,
            score=1.0,
            reason="inventory_graph_match",
        )
        for i in range(1, 8)
    ]
    answer = query_service._build_extractive_fallback(citations)
    assert "1. src/File1.java" in answer
    assert "5. src/File5.java" in answer
    assert "6. src/File6.java" not in answer


def test_extractive_fallback_lists_all_inventory_results() -> None:
    """Crea una respuesta estructurada de inventario completo en modo extractivo."""
    citations = [
        Citation(
            path=f"src/File{i}.java",
            start_line=1,
            end_line=1,
            score=1.0,
            reason="inventory_graph_match",
        )
        for i in range(1, 8)
    ]
    answer = query_service._build_extractive_fallback(
        citations,
        inventory_mode=True,
        inventory_target="controller",
        query="dame todos los controllers",
    )
    assert "1) Respuesta principal:" in answer
    assert "2) Componentes/archivos clave:" in answer
    assert "3) Organización observada en el contexto:" in answer
    assert "4) Citas de archivos con líneas:" in answer
    assert "- File1.java" in answer
    assert "- File7.java" in answer
    assert "Consulta original: dame todos los controllers" in answer


def test_extractive_fallback_verification_failed_message() -> None:
    """Utiliza el mensaje de verificación_fallida y evita el texto no_configurado."""
    citations = [
        Citation(
            path="src/AuthService.java",
            start_line=10,
            end_line=20,
            score=0.95,
            reason="hybrid_rag_match",
        )
    ]
    answer = query_service._build_extractive_fallback(
        citations,
        fallback_reason="verification_failed",
    )
    assert "OpenAI no está configurado" not in answer
    assert "No se pudo validar completamente" in answer


def test_run_query_uses_inventory_short_circuit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delega las intenciones del inventario para graficar primero la ruta del inventario."""

    def _fail_hybrid(*args, **kwargs):
        raise AssertionError("hybrid_search should not run for inventory query")

    def _fake_inventory(
        repo_id: str,
        query: str,
        page: int,
        page_size: int,
    ) -> query_service.InventoryQueryResponse:
        assert repo_id == "repo1"
        assert page == 1
        assert page_size > 0
        return query_service.InventoryQueryResponse(
            answer="inventario",
            target="modelo",
            module_name="mall-mbg",
            total=2,
            page=1,
            page_size=80,
            items=[],
            citations=[
                Citation(
                    path="mall-mbg/src/main/java/com/macro/mall/model/A.java",
                    start_line=1,
                    end_line=1,
                    score=1.0,
                    reason="inventory_graph_match",
                )
            ],
            diagnostics={"inventory_count": 2},
        )

    monkeypatch.setattr(query_service, "hybrid_search", _fail_hybrid)
    monkeypatch.setattr(query_service, "run_inventory_query", _fake_inventory)

    result = query_service.run_query(
        repo_id="repo1",
        query="inventario: cuales son los modelos de mall-mbg",
        top_n=80,
        top_k=20,
    )

    assert result.answer == "inventario"
    assert result.diagnostics["inventory_route"] == "graph_first"
    assert result.diagnostics["inventory_total"] == 2


def test_run_query_inventory_without_target_falls_back_to_general(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mantiene la ruta general de control de calidad cuando la intención del inventario no tiene un objetivo extraíble."""

    def _fake_hybrid(
        repo_id: str,
        query: str,
        top_n: int,
        **kwargs,
    ) -> list[RetrievalChunk]:
        _ = kwargs
        return [
            RetrievalChunk(
                id="c1",
                text="Core package contains settings and models.",
                score=0.9,
                metadata={"path": "core/settings.py", "start_line": 1, "end_line": 20},
            )
        ]

    monkeypatch.setattr(query_service, "hybrid_search", _fake_hybrid)
    monkeypatch.setattr(
        query_service,
        "rerank",
        lambda query, chunks, top_k: chunks,
    )
    monkeypatch.setattr(query_service, "expand_with_graph", lambda chunks: [])
    monkeypatch.setattr(query_service, "assemble_context", lambda chunks, graph_records, max_tokens: "ctx")

    class _AnswerClient:
        def __init__(self, *args, **kwargs) -> None:
            _ = args, kwargs
            self.provider = "openai"
            self.answer_model = "gpt-test"
            self.verifier_model = "gpt-test"

        enabled = False

    monkeypatch.setattr(query_service, "AnswerClient", _AnswerClient)

    def _fail_inventory(*args, **kwargs):
        raise AssertionError("run_inventory_query should not run without target")

    monkeypatch.setattr(query_service, "run_inventory_query", _fail_inventory)
    monkeypatch.setattr(query_service, "_is_inventory_query", lambda query: True)
    monkeypatch.setattr(query_service, "_extract_inventory_target", lambda query: None)

    result = query_service.run_query(
        repo_id="repo1",
        query="cuales son en core?",
        top_n=20,
        top_k=5,
    )

    assert result.diagnostics["inventory_intent"] is True
    assert result.diagnostics["inventory_route"] == "fallback_to_general"


def test_run_inventory_query_applies_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Devuelve el segmento de página solicitado para entidades de inventario."""

    discovered = [
        {
            "label": f"Model{i}.java",
            "path": f"mall-mbg/src/main/java/com/macro/mall/model/Model{i}.java",
            "kind": "file",
            "start_line": 1,
            "end_line": 1,
        }
        for i in range(1, 6)
    ]

    monkeypatch.setattr(query_service, "_query_inventory_entities", lambda **_: discovered)

    result = query_service.run_inventory_query(
        repo_id="repo1",
        query="cuales son todos los modelos de mall-mbg",
        page=2,
        page_size=2,
    )

    assert result.total == 5
    assert result.page == 2
    assert result.page_size == 2
    assert len(result.items) == 2
    assert result.items[0].label == "Model3.java"
    assert result.items[1].label == "Model4.java"


def test_run_inventory_query_includes_component_purposes_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agrega una sección de propósito por componente cuando la consulta pregunta qué hace cada elemento."""

    discovered = [
        {
            "label": "Settings",
            "path": "src/coderag/core/settings.py",
            "kind": "file",
            "start_line": 1,
            "end_line": 30,
        },
        {
            "label": "Models",
            "path": "src/coderag/core/models.py",
            "kind": "file",
            "start_line": 1,
            "end_line": 30,
        },
    ]

    monkeypatch.setattr(query_service, "_query_inventory_entities", lambda **_: discovered)
    monkeypatch.setattr(
        query_service,
        "_describe_inventory_components",
        lambda **_: [
            ("settings.py", "centraliza configuración"),
            ("models.py", "define modelos de datos"),
        ],
    )

    result = query_service.run_inventory_query(
        repo_id="repo1",
        query="cuales son los componentes de core y que funcion cumple cada uno",
        page=1,
        page_size=10,
    )

    assert "3) Función probable de cada componente:" in result.answer
    assert "settings.py" in result.answer
    assert "modelos de datos" in result.answer
    assert result.diagnostics["inventory_explain"] is True
    assert result.diagnostics["inventory_purpose_count"] == 2


def test_run_inventory_query_auto_enriches_dependency_inventory_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En consultas de dependencias, agrega contexto funcional sin requerir modo explain explícito."""

    discovered = [
        {
            "label": "requirements.txt",
            "path": "requirements.txt",
            "kind": "file",
            "start_line": 1,
            "end_line": 20,
        }
    ]

    monkeypatch.setattr(query_service, "_query_inventory_entities", lambda **_: discovered)
    monkeypatch.setattr(
        query_service,
        "_describe_inventory_components",
        lambda **_: [(
            "requirements.txt",
            "Declara dependencias Python del proyecto para instalación y despliegue.",
        )],
    )

    result = query_service.run_inventory_query(
        repo_id="repo1",
        query="cuales son las dependencias del proyecto",
        page=1,
        page_size=20,
    )

    assert "Función probable de cada componente" in result.answer
    assert "requirements.txt" in result.answer
    assert result.diagnostics["inventory_explain"] is False
    assert result.diagnostics["inventory_purpose_count"] == 1


def test_describe_inventory_components_uses_persisted_purpose_summaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lee propósitos persistidos desde grafo sin depender del workspace."""

    class _FakeGraphBuilder:
        def query_file_purpose_summaries(
            self,
            repo_id: str,
            paths: list[str],
        ) -> dict[str, dict[str, str]]:
            assert repo_id == "repo1"
            assert paths == [
                "src/coderag/core/settings.py",
                "src/coderag/core/models.py",
            ]
            return {
                "src/coderag/core/settings.py": {
                    "purpose_summary": "Centraliza configuración del componente.",
                    "purpose_source": "filename_heuristic",
                },
                "src/coderag/core/models.py": {
                    "purpose_summary": "Define estructuras de datos del dominio.",
                    "purpose_source": "filename_heuristic",
                },
            }

        def close(self) -> None:
            return None

    monkeypatch.setattr(query_service, "GraphBuilder", _FakeGraphBuilder)

    descriptions = query_service._describe_inventory_components(
        repo_id="repo1",
        citations=[
            Citation(
                path="src/coderag/core/settings.py",
                start_line=1,
                end_line=20,
                score=1.0,
                reason="inventory_graph_match",
            ),
            Citation(
                path="src/coderag/core/models.py",
                start_line=1,
                end_line=20,
                score=0.9,
                reason="inventory_graph_match",
            ),
        ],
        pipeline_started_at=monotonic(),
        budget_seconds=5.0,
        query="cuales son los componentes del modulo core y que funcion cumple cada uno",
    )

    assert descriptions == [
        ("settings.py", "Centraliza configuración del componente."),
        ("models.py", "Define estructuras de datos del dominio."),
    ]


def test_run_query_retries_with_raw_citations_if_filtered_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si el filtrado elimina todo, reutiliza citas crudas para evitar fallback vacío."""

    def _fake_hybrid(
        repo_id: str,
        query: str,
        top_n: int,
        **kwargs,
    ) -> list[RetrievalChunk]:
        _ = kwargs
        return [
            RetrievalChunk(
                id="c1",
                text="Doc",
                score=0.9,
                metadata={"path": "docs", "start_line": 1, "end_line": 10},
            )
        ]

    monkeypatch.setattr(query_service, "hybrid_search", _fake_hybrid)
    monkeypatch.setattr(
        query_service,
        "rerank",
        lambda query, chunks, top_k: chunks,
    )
    monkeypatch.setattr(query_service, "expand_with_graph", lambda chunks: [])
    monkeypatch.setattr(
        query_service,
        "assemble_context",
        lambda chunks, graph_records, max_tokens: "contexto suficiente para llm",
    )

    class _AnswerClient:
        def __init__(self, *args, **kwargs) -> None:
            _ = args, kwargs
            self.provider = "openai"
            self.answer_model = "gpt-test"
            self.verifier_model = "gpt-test"

        enabled = False

    monkeypatch.setattr(query_service, "AnswerClient", _AnswerClient)

    result = query_service.run_query(
        repo_id="repo1",
        query="dime algo",
        top_n=10,
        top_k=5,
    )

    assert result.citations
    assert result.citations[0].path == "docs"
    assert result.diagnostics["filtered_citations"] == 0
    assert result.diagnostics["raw_citations"] == 1


def test_run_query_uses_insufficient_context_fallback_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evita generación LLM cuando el contexto es insuficiente y deja razón diagnóstica."""

    def _fake_hybrid(
        repo_id: str,
        query: str,
        top_n: int,
        **kwargs,
    ) -> list[RetrievalChunk]:
        _ = kwargs
        return [
            RetrievalChunk(
                id="c1",
                text="tiny",
                score=0.7,
                metadata={"path": "core/a.py", "start_line": 1, "end_line": 2},
            )
        ]

    monkeypatch.setattr(query_service, "hybrid_search", _fake_hybrid)
    monkeypatch.setattr(
        query_service,
        "rerank",
        lambda query, chunks, top_k: chunks,
    )
    monkeypatch.setattr(query_service, "expand_with_graph", lambda chunks: [])
    monkeypatch.setattr(query_service, "assemble_context", lambda *args, **kwargs: "x")

    class _AnswerClient:
        def __init__(self, *args, **kwargs) -> None:
            _ = args, kwargs
            self.provider = "openai"
            self.answer_model = "gpt-test"
            self.verifier_model = "gpt-test"

        enabled = True

        def answer(self, *args, **kwargs):  # pragma: no cover - no debería ejecutarse
            raise AssertionError("LLM no debe ejecutarse con contexto insuficiente")

        def verify(self, *args, **kwargs):  # pragma: no cover - no debería ejecutarse
            raise AssertionError("verify no debe ejecutarse con contexto insuficiente")

    monkeypatch.setattr(query_service, "AnswerClient", _AnswerClient)

    result = query_service.run_query(
        repo_id="repo1",
        query="consulta",
        top_n=10,
        top_k=5,
    )

    assert result.diagnostics["context_sufficient"] is False
    assert result.diagnostics["fallback_reason"] == "insufficient_context"


def test_run_inventory_query_missing_target_includes_total_timing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incluye total_ms en diagnostics incluso cuando falta target de inventario."""
    monkeypatch.setattr(query_service, "_is_inventory_query", lambda query: True)
    monkeypatch.setattr(query_service, "_extract_inventory_target", lambda query: None)
    monkeypatch.setattr(
        query_service,
        "_is_inventory_explain_query",
        lambda query: False,
    )
    monkeypatch.setattr(query_service, "_extract_module_name", lambda query: None)
    monkeypatch.setattr(
        query_service,
        "_resolve_module_scope",
        lambda repo_id, module_name: None,
    )

    result = query_service.run_inventory_query(
        repo_id="repo1",
        query="inventario sin objetivo",
        page=1,
        page_size=50,
    )

    assert result.diagnostics["fallback_reason"] == "inventory_target_missing"
    timings = result.diagnostics["stage_timings_ms"]
    assert "parse_ms" in timings
    assert "total_ms" in timings
    assert timings["total_ms"] >= 0


def test_run_retrieval_query_returns_chunks_and_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retorna evidencia retrieval-only estructurada sin usar cliente LLM."""

    class _Settings:
        query_max_seconds = 30.0
        max_context_tokens = 512
        openai_embedding_model = "text-embedding-3-small"

        @staticmethod
        def resolve_embedding_provider(provider: str | None) -> str:
            return provider or "openai"

        @staticmethod
        def resolve_embedding_model(provider: str, model: str | None) -> str:
            _ = provider
            return model or "text-embedding-3-small"

    monkeypatch.setattr(query_service, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        query_service,
        "hybrid_search",
        lambda **kwargs: [
            RetrievalChunk(
                id="c1",
                text="class AuthService {}",
                score=0.92,
                metadata={
                    "path": "src/AuthService.java",
                    "start_line": 10,
                    "end_line": 20,
                },
            )
        ],
    )
    monkeypatch.setattr(
        query_service,
        "rerank",
        lambda query, chunks, top_k: chunks,
    )
    monkeypatch.setattr(query_service, "expand_with_graph", lambda chunks: [])

    result = query_service.run_retrieval_query(
        repo_id="repo1",
        query="auth service",
        top_n=10,
        top_k=5,
    )

    assert result.mode == "retrieval_only"
    assert len(result.chunks) == 1
    assert result.chunks[0].path == "src/AuthService.java"
    assert len(result.citations) == 1
    assert result.statistics.total_before_rerank == 1
    assert result.statistics.total_after_rerank == 1
    assert result.diagnostics["retrieved"] == 1
    assert result.context is None


def test_run_retrieval_query_includes_context_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incluye contexto ensamblado únicamente cuando include_context=true."""

    class _Settings:
        query_max_seconds = 30.0
        max_context_tokens = 256
        openai_embedding_model = "text-embedding-3-small"

        @staticmethod
        def resolve_embedding_provider(provider: str | None) -> str:
            return provider or "openai"

        @staticmethod
        def resolve_embedding_model(provider: str, model: str | None) -> str:
            _ = provider
            return model or "text-embedding-3-small"

    monkeypatch.setattr(query_service, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        query_service,
        "hybrid_search",
        lambda **kwargs: [
            RetrievalChunk(
                id="c1",
                text="x",
                score=0.88,
                metadata={"path": "src/a.py", "start_line": 1, "end_line": 2},
            )
        ],
    )
    monkeypatch.setattr(
        query_service,
        "rerank",
        lambda query, chunks, top_k: chunks,
    )
    monkeypatch.setattr(query_service, "expand_with_graph", lambda chunks: [{"n": 1}])
    monkeypatch.setattr(
        query_service,
        "assemble_context",
        lambda chunks, graph_records, max_tokens: "PATH: src/a.py\nLINES: 1-2",
    )

    result = query_service.run_retrieval_query(
        repo_id="repo1",
        query="a",
        top_n=5,
        top_k=3,
        include_context=True,
    )

    assert result.context is not None
    assert "PATH: src/a.py" in result.context
    assert "context_assembly_ms" in result.diagnostics["stage_timings_ms"]


def test_run_retrieval_query_routes_inventory_intent_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reutiliza el flujo de inventario cuando la consulta retrieval-only tiene intención de inventario."""

    class _Settings:
        query_max_seconds = 30.0
        max_context_tokens = 256
        inventory_page_size = 80

    monkeypatch.setattr(query_service, "get_settings", lambda: _Settings())
    monkeypatch.setattr(query_service, "_is_inventory_query", lambda query: True)
    monkeypatch.setattr(query_service, "_extract_inventory_target", lambda query: "modelo")

    def fake_run_inventory_query(
        repo_id: str,
        query: str,
        page: int,
        page_size: int,
    ) -> InventoryQueryResponse:
        assert repo_id == "repo1"
        assert "inventario" in query
        assert page == 1
        assert page_size == 80
        return InventoryQueryResponse(
            answer="Inventario de modelos",
            target="modelo",
            module_name="mall-mbg",
            total=2,
            page=1,
            page_size=80,
            items=[
                InventoryItem(
                    label="A.java",
                    path="mall-mbg/src/main/java/com/macro/mall/model/A.java",
                    kind="file",
                    start_line=1,
                    end_line=1,
                ),
                InventoryItem(
                    label="B.java",
                    path="mall-mbg/src/main/java/com/macro/mall/model/B.java",
                    kind="file",
                    start_line=1,
                    end_line=1,
                ),
            ],
            citations=[
                Citation(
                    path="mall-mbg/src/main/java/com/macro/mall/model/A.java",
                    start_line=1,
                    end_line=1,
                    score=1.0,
                    reason="inventory_graph_match",
                )
            ],
            diagnostics={"inventory_count": 2},
        )

    monkeypatch.setattr(query_service, "run_inventory_query", fake_run_inventory_query)

    result = query_service.run_retrieval_query(
        repo_id="repo1",
        query="inventario: cuales son los modelos de mall-mbg",
        top_n=10,
        top_k=5,
    )

    assert result.mode == "retrieval_only"
    assert result.answer == "Inventario de modelos"
    assert len(result.chunks) == 2
    assert result.statistics.total_before_rerank == 2
    assert result.statistics.total_after_rerank == 2
    assert result.diagnostics["inventory_route"] == "graph_first_retrieval"
    assert result.diagnostics["inventory_total"] == 2


def test_run_retrieval_query_inventory_includes_context_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incluye contexto textual en retrieval-only inventario cuando include_context=true."""

    class _Settings:
        query_max_seconds = 30.0
        max_context_tokens = 256
        inventory_page_size = 80

    monkeypatch.setattr(query_service, "get_settings", lambda: _Settings())
    monkeypatch.setattr(query_service, "_is_inventory_query", lambda query: True)
    monkeypatch.setattr(query_service, "_extract_inventory_target", lambda query: "controller")
    monkeypatch.setattr(
        query_service,
        "run_inventory_query",
        lambda **kwargs: InventoryQueryResponse(
            answer="Inventario de controllers",
            target="controller",
            module_name="mall-admin",
            total=1,
            page=1,
            page_size=80,
            items=[
                InventoryItem(
                    label="UserController.java",
                    path="mall-admin/src/main/java/com/macro/mall/admin/controller/UserController.java",
                    kind="file",
                    start_line=1,
                    end_line=1,
                )
            ],
            citations=[],
            diagnostics={"inventory_count": 1},
        ),
    )

    result = query_service.run_retrieval_query(
        repo_id="repo1",
        query="inventario: controllers",
        top_n=10,
        top_k=5,
        include_context=True,
    )

    assert result.context is not None
    assert "INVENTORY_CONTEXT:" in result.context
    assert "UserController.java" in result.context
    assert result.diagnostics["context_chars"] > 0


def test_is_literal_code_query_detection() -> None:
    """Detecta solicitudes explícitas de código literal completo."""
    assert query_service._is_literal_code_query(
        "dame el codigo completo de src/coderag/retrieval/hybrid_search.py"
    )
    assert query_service._is_literal_code_query(
        "show me the full code of app/main.py"
    )
    assert not query_service._is_literal_code_query("que hace hybrid_search")


def test_run_query_literal_code_returns_live_file_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """En modo literal devuelve contenido real del archivo con cita exacta."""
    repo_id = "repo1"
    target_path = tmp_path / repo_id / "src" / "coderag" / "retrieval"
    target_path.mkdir(parents=True)
    file_path = target_path / "hybrid_search.py"
    file_path.write_text(
        "def hybrid_search() -> str:\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )

    class _Settings:
        workspace_path = tmp_path

    monkeypatch.setattr(query_service, "get_settings", lambda: _Settings())

    def _fail_hybrid(*args, **kwargs):
        raise AssertionError("hybrid_search no debe correr en modo literal")

    monkeypatch.setattr(query_service, "hybrid_search", _fail_hybrid)

    result = query_service.run_query(
        repo_id=repo_id,
        query="dame el codigo completo de src/coderag/retrieval/hybrid_search.py",
        top_n=20,
        top_k=5,
    )

    assert result.diagnostics["literal_mode"] is True
    assert result.diagnostics["literal_exact_match"] is True
    assert "def hybrid_search()" in result.answer
    assert len(result.citations) == 1
    assert result.citations[0].path == "src/coderag/retrieval/hybrid_search.py"
    assert result.citations[0].start_line == 1


def test_run_query_literal_code_ambiguous_filename_returns_safe_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Rechaza en forma segura cuando el nombre de archivo no es único."""
    repo_id = "repo1"
    first = tmp_path / repo_id / "a"
    second = tmp_path / repo_id / "b"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "hybrid_search.py").write_text("x=1\n", encoding="utf-8")
    (second / "hybrid_search.py").write_text("x=2\n", encoding="utf-8")

    class _Settings:
        workspace_path = tmp_path

    monkeypatch.setattr(query_service, "get_settings", lambda: _Settings())

    result = query_service.run_query(
        repo_id=repo_id,
        query="dame el codigo completo de hybrid_search.py",
        top_n=20,
        top_k=5,
    )

    assert result.diagnostics["literal_mode"] is True
    assert result.diagnostics["literal_exact_match"] is False
    assert result.diagnostics["literal_failure_reason"] == "ambiguous_filename"
    assert "ruta exacta" in result.answer.lower()
    assert result.citations == []


def test_run_retrieval_query_literal_code_returns_live_file_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """En retrieval-only devuelve contenido literal exacto cuando hay match único."""
    repo_id = "repo1"
    target_path = tmp_path / repo_id / "src" / "coderag" / "retrieval"
    target_path.mkdir(parents=True)
    (target_path / "hybrid_search.py").write_text(
        "def hybrid_search() -> str:\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )

    class _Settings:
        workspace_path = tmp_path
        query_max_seconds = 30.0

    monkeypatch.setattr(query_service, "get_settings", lambda: _Settings())

    def _fail_hybrid(*args, **kwargs):
        raise AssertionError("hybrid_search no debe correr en modo literal")

    monkeypatch.setattr(query_service, "hybrid_search", _fail_hybrid)

    result = query_service.run_retrieval_query(
        repo_id=repo_id,
        query="dame el codigo completo de src/coderag/retrieval/hybrid_search.py",
        top_n=20,
        top_k=5,
        include_context=False,
    )

    assert result.mode == "retrieval_only"
    assert result.diagnostics["literal_mode"] is True
    assert result.diagnostics["literal_exact_match"] is True
    assert len(result.chunks) == 1
    assert result.chunks[0].path == "src/coderag/retrieval/hybrid_search.py"
    assert len(result.citations) == 1
    assert "def hybrid_search()" in result.answer
    assert result.context is None


def test_run_retrieval_query_literal_code_ambiguous_filename_returns_safe_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """En retrieval-only rechaza seguro cuando el archivo es ambiguo."""
    repo_id = "repo1"
    first = tmp_path / repo_id / "a"
    second = tmp_path / repo_id / "b"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "hybrid_search.py").write_text("x=1\n", encoding="utf-8")
    (second / "hybrid_search.py").write_text("x=2\n", encoding="utf-8")

    class _Settings:
        workspace_path = tmp_path
        query_max_seconds = 30.0

    monkeypatch.setattr(query_service, "get_settings", lambda: _Settings())

    result = query_service.run_retrieval_query(
        repo_id=repo_id,
        query="dame el codigo completo de hybrid_search.py",
        top_n=20,
        top_k=5,
    )

    assert result.diagnostics["literal_mode"] is True
    assert result.diagnostics["literal_exact_match"] is False
    assert result.diagnostics["literal_failure_reason"] == "ambiguous_filename"
    assert result.chunks == []
    assert result.citations == []


def test_run_retrieval_query_literal_code_respects_include_context_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Incluye contexto literal solo cuando include_context=true en modo literal."""
    repo_id = "repo1"
    target_path = tmp_path / repo_id / "src" / "coderag"
    target_path.mkdir(parents=True)
    content = "print('ok')\n"
    (target_path / "tool.py").write_text(content, encoding="utf-8")

    class _Settings:
        workspace_path = tmp_path
        query_max_seconds = 30.0

    monkeypatch.setattr(query_service, "get_settings", lambda: _Settings())

    with_context = query_service.run_retrieval_query(
        repo_id=repo_id,
        query="dame el codigo completo de src/coderag/tool.py",
        top_n=20,
        top_k=5,
        include_context=True,
    )
    without_context = query_service.run_retrieval_query(
        repo_id=repo_id,
        query="dame el codigo completo de src/coderag/tool.py",
        top_n=20,
        top_k=5,
        include_context=False,
    )

    assert with_context.context == content
    assert without_context.context is None


def test_run_query_literal_code_blocks_when_workspace_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bloquea modo literal sin intentar fallback cuando el workspace ya no existe."""

    class _Settings:
        workspace_path = tmp_path
        inventory_page_size = 25
        query_max_seconds = 30.0

    def _fail_hybrid_search(*args: object, **kwargs: object) -> None:
        raise AssertionError("hybrid_search no debe correr cuando literal se bloquea")

    monkeypatch.setattr(query_service, "get_settings", lambda: _Settings())
    monkeypatch.setattr(query_service, "hybrid_search", _fail_hybrid_search)

    result = query_service.run_query(
        repo_id="repo-sin-workspace",
        query="dame el codigo completo de settings.py",
        top_n=5,
        top_k=3,
    )

    assert "workspace local disponible" in result.answer
    assert result.citations == []
    assert result.diagnostics["literal_mode"] is True
    assert result.diagnostics["literal_exact_match"] is False
    assert result.diagnostics["literal_failure_reason"] == "workspace_unavailable"
    assert result.diagnostics["fallback_reason"] == "literal_workspace_required"


def test_run_retrieval_query_literal_code_blocks_when_workspace_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bloquea modo literal retrieval-only sin leer archivos locales inexistentes."""

    class _Settings:
        workspace_path = tmp_path
        inventory_page_size = 25
        query_max_seconds = 30.0

    def _fail_hybrid_search(*args: object, **kwargs: object) -> None:
        raise AssertionError("hybrid_search no debe correr cuando literal se bloquea")

    monkeypatch.setattr(query_service, "get_settings", lambda: _Settings())
    monkeypatch.setattr(query_service, "hybrid_search", _fail_hybrid_search)

    result = query_service.run_retrieval_query(
        repo_id="repo-sin-workspace",
        query="show me the full code of settings.py",
        top_n=5,
        top_k=3,
        include_context=True,
    )

    assert "workspace local disponible" in result.answer
    assert result.chunks == []
    assert result.citations == []
    assert result.context is None
    assert result.diagnostics["literal_mode"] is True
    assert result.diagnostics["literal_exact_match"] is False
    assert result.diagnostics["literal_failure_reason"] == "workspace_unavailable"
    assert result.diagnostics["fallback_reason"] == "literal_workspace_required"


def test_run_query_literal_code_exact_symbol_returns_symbol_span(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Devuelve solo el símbolo exacto cuando se solicita código completo por función."""
    repo_id = "repo1"
    target_path = tmp_path / repo_id / "src" / "coderag"
    target_path.mkdir(parents=True)
    (target_path / "tool.py").write_text(
        "def helper() -> int:\n"
        "    return 1\n\n"
        "def target_symbol() -> int:\n"
        "    return 42\n",
        encoding="utf-8",
    )

    class _Settings:
        workspace_path = tmp_path

    monkeypatch.setattr(query_service, "get_settings", lambda: _Settings())

    result = query_service.run_query(
        repo_id=repo_id,
        query="dame el codigo completo de la funcion target_symbol",
        top_n=20,
        top_k=5,
    )

    assert result.diagnostics["literal_mode"] is True
    assert result.diagnostics["literal_exact_match"] is True
    assert "Símbolo: target_symbol" in result.answer
    assert "def target_symbol()" in result.answer
    assert "def helper()" not in result.answer
    assert result.citations[0].start_line == 4
    assert result.citations[0].reason == "literal_symbol_exact_match"


def test_run_retrieval_query_literal_code_exact_symbol_returns_symbol_chunk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """En retrieval-only retorna chunk literal de símbolo exacto único."""
    repo_id = "repo1"
    target_path = tmp_path / repo_id / "src" / "coderag"
    target_path.mkdir(parents=True)
    (target_path / "tool.py").write_text(
        "def alpha() -> int:\n"
        "    return 1\n\n"
        "def exact_match_symbol() -> int:\n"
        "    return 7\n",
        encoding="utf-8",
    )

    class _Settings:
        workspace_path = tmp_path
        query_max_seconds = 30.0

    monkeypatch.setattr(query_service, "get_settings", lambda: _Settings())

    result = query_service.run_retrieval_query(
        repo_id=repo_id,
        query="dame el codigo completo de la funcion exact_match_symbol",
        top_n=20,
        top_k=5,
        include_context=True,
    )

    assert result.diagnostics["literal_mode"] is True
    assert result.diagnostics["literal_exact_match"] is True
    assert len(result.chunks) == 1
    assert result.chunks[0].kind == "literal_symbol"
    assert "def exact_match_symbol()" in result.chunks[0].text
    assert "def alpha()" not in result.chunks[0].text
    assert result.citations[0].reason == "literal_symbol_exact_match"
    assert result.context is not None
    assert "def exact_match_symbol()" in result.context


def test_run_query_literal_code_ambiguous_symbol_returns_safe_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Rechaza de forma segura cuando el símbolo exacto aparece en múltiples archivos."""
    repo_id = "repo1"
    first = tmp_path / repo_id / "a"
    second = tmp_path / repo_id / "b"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "tool_a.py").write_text(
        "def duplicated_symbol() -> int:\n"
        "    return 1\n",
        encoding="utf-8",
    )
    (second / "tool_b.py").write_text(
        "def duplicated_symbol() -> int:\n"
        "    return 2\n",
        encoding="utf-8",
    )

    class _Settings:
        workspace_path = tmp_path

    monkeypatch.setattr(query_service, "get_settings", lambda: _Settings())

    result = query_service.run_query(
        repo_id=repo_id,
        query="dame el codigo completo de la funcion duplicated_symbol",
        top_n=20,
        top_k=5,
    )

    assert result.diagnostics["literal_mode"] is True
    assert result.diagnostics["literal_exact_match"] is False
    assert result.diagnostics["literal_failure_reason"] == "ambiguous_symbol"
    assert result.citations == []
