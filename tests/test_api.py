"""Pruebas API para puntos finales primarios."""

from fastapi.testclient import TestClient

from coderag.api import server

app = server.app


def test_get_missing_job_returns_404() -> None:
    """No se encontraron devoluciones para una identificación de trabajo de ingesta desconocida."""
    client = TestClient(app)
    response = client.get("/jobs/non-existent")
    assert response.status_code == 404


def test_admin_reset_returns_summary(monkeypatch) -> None:
    """Devuelve una carga útil resumida clara cuando la operación de reinicio se realiza correctamente."""

    def fake_reset_all_data() -> tuple[list[str], list[str]]:
        return ["BM25 en memoria", "Grafo Neo4j"], ["warning de prueba"]

    monkeypatch.setattr(server.jobs, "reset_all_data", fake_reset_all_data)
    client = TestClient(app)

    response = client.post("/admin/reset")
    assert response.status_code == 200

    payload = response.json()
    assert payload["message"] == "Limpieza total completada"
    assert "BM25 en memoria" in payload["cleared"]
    assert "warning de prueba" in payload["warnings"]


def test_list_repos_returns_repo_id_catalog(monkeypatch) -> None:
    """Devuelve identificadores de repositorio conocidos para el menú desplegable de consultas."""

    def fake_list_repo_ids() -> list[str]:
        return ["mall", "api-service"]

    monkeypatch.setattr(server.jobs, "list_repo_ids", fake_list_repo_ids)
    client = TestClient(app)

    response = client.get("/repos")
    assert response.status_code == 200
    assert response.json()["repo_ids"] == ["mall", "api-service"]


def test_inventory_query_endpoint_returns_paginated_payload(monkeypatch) -> None:
    """Devuelve una respuesta de inventario estructurada a través de un punto final dedicado."""
    from coderag.api import query_service

    def fake_run_inventory_query(
        repo_id: str,
        query: str,
        page: int,
        page_size: int,
    ) -> dict:
        assert repo_id == "mall"
        assert "modelos" in query
        assert page == 2
        assert page_size == 5
        return {
            "answer": "Respuesta inventario",
            "target": "modelo",
            "module_name": "mall-mbg",
            "total": 11,
            "page": 2,
            "page_size": 5,
            "items": [
                {
                    "label": "CmsHelp.java",
                    "path": "mall-mbg/src/main/java/com/macro/mall/model/CmsHelp.java",
                    "kind": "file",
                    "start_line": 1,
                    "end_line": 1,
                }
            ],
            "citations": [
                {
                    "path": "mall-mbg/src/main/java/com/macro/mall/model/CmsHelp.java",
                    "start_line": 1,
                    "end_line": 1,
                    "score": 1.0,
                    "reason": "inventory_graph_match",
                }
            ],
            "diagnostics": {"inventory_count": 11},
        }

    monkeypatch.setattr(query_service, "run_inventory_query", fake_run_inventory_query)
    client = TestClient(app)

    response = client.post(
        "/inventory/query",
        json={
            "repo_id": "mall",
            "query": "cuales son todos los modelos de mall-mbg",
            "page": 2,
            "page_size": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["target"] == "modelo"
    assert payload["total"] == 11
    assert payload["page"] == 2
    assert payload["page_size"] == 5
    assert len(payload["items"]) == 1
