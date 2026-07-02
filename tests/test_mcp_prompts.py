"""Pruebas de los prompts MCP (guías de uso de las tools de consulta)."""

import asyncio

import mcp.types as t
from mcp.server.lowlevel import Server

from coderag.api.mcp_prompts import register_mcp_prompts


def _server_with_prompts() -> Server:
    server = Server("test")
    register_mcp_prompts(server)
    return server


def test_register_returns_expected_count() -> None:
    server = Server("test")
    assert register_mcp_prompts(server) == 3


def test_list_prompts_exposes_the_three_guides() -> None:
    server = _server_with_prompts()
    handler = server.request_handlers[t.ListPromptsRequest]
    req = t.ListPromptsRequest(method="prompts/list")
    result = asyncio.run(handler(req)).root
    names = {p.name for p in result.prompts}
    assert names == {
        "query_repo_guide",
        "query_retrieval_guide",
        "hybrid_rag_workflow",
    }


def test_query_repo_guide_declares_required_arguments() -> None:
    server = _server_with_prompts()
    handler = server.request_handlers[t.ListPromptsRequest]
    result = asyncio.run(handler(t.ListPromptsRequest(method="prompts/list"))).root
    guide = next(p for p in result.prompts if p.name == "query_repo_guide")
    required = {a.name for a in guide.arguments if a.required}
    assert required == {"repo_id", "pregunta"}


def test_get_prompt_substitutes_placeholders() -> None:
    server = _server_with_prompts()
    handler = server.request_handlers[t.GetPromptRequest]
    req = t.GetPromptRequest(
        method="prompts/get",
        params=t.GetPromptRequestParams(
            name="query_repo_guide",
            arguments={"repo_id": "demo-main", "pregunta": "dónde está X"},
        ),
    )
    result = asyncio.run(handler(req)).root
    text = result.messages[0].content.text
    assert "demo-main" in text
    assert "dónde está X" in text
    assert result.messages[0].role == "user"


def test_hybrid_workflow_keeps_uri_template_literal() -> None:
    """El flujo sin args conserva {repo_id} literal en el patrón del URI."""
    server = _server_with_prompts()
    handler = server.request_handlers[t.GetPromptRequest]
    req = t.GetPromptRequest(
        method="prompts/get",
        params=t.GetPromptRequestParams(name="hybrid_rag_workflow", arguments=None),
    )
    result = asyncio.run(handler(req)).root
    assert "rag://repos/{repo_id}/status" in result.messages[0].content.text
