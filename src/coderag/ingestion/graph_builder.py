"""Integración de Neo4j para la construcción de gráficos de conocimiento de código."""

import hashlib
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from typing import Any
from urllib.parse import urlparse

from neo4j import GraphDatabase

from coderag.core.models import (
    FileImportRelation,
    ScannedFile,
    SemanticRelation,
    SymbolChunk,
)
from coderag.core.settings import get_settings
from coderag.ingestion.component_metadata import infer_component_purpose


def describe_neo4j_target(settings: Any) -> str:
    """Resume el destino de Neo4j sin exponer credenciales."""
    raw_uri = str(getattr(settings, "neo4j_uri", "") or "").strip()
    if not raw_uri:
        return "<unknown-neo4j>"

    parsed = urlparse(raw_uri)
    host = parsed.hostname
    if host:
        port = parsed.port or 7687
        return f"{host}:{port}"

    sanitized = raw_uri.rsplit("@", 1)[-1].lstrip("/")
    return sanitized or "<unknown-neo4j>"


def describe_neo4j_auth_mode(settings: Any) -> str:
    """Resume el modo de autenticación efectivo configurado para Neo4j."""
    username = str(getattr(settings, "neo4j_user", "") or "").strip()
    password = str(getattr(settings, "neo4j_password", "") or "").strip()
    if username and password:
        return "basic"
    return "none"


def build_neo4j_error_message(
    settings: Any,
    *,
    operation: str,
    exc: Exception,
    repo_id: str | None = None,
) -> str:
    """Construye un mensaje sanitario y consistente para errores Neo4j."""
    target = describe_neo4j_target(settings)
    auth_mode = describe_neo4j_auth_mode(settings)
    repo_hint = f", repo_id={repo_id}" if repo_id else ""
    host = urlparse(str(getattr(settings, "neo4j_uri", "") or "").strip()).hostname
    compose_hint = ""
    if host == "neo4j":
        compose_hint = (
            " Si usas docker-compose, el host 'neo4j' solo existe "
            "cuando el servicio está en la red esperada."
        )
    return (
        "No se pudo completar la operación de Neo4j "
        f"'{operation}' en {target} "
        f"(auth={auth_mode}{repo_hint})."
        f"{compose_hint} Error original: {exc}"
    )


def build_neo4j_driver(
    settings: Any | None = None,
    *,
    operation: str = "crear driver",
    timeout_seconds: float | None = None,
) -> Any:
    """Crea un driver Neo4j con diagnóstico sanitario si falla."""
    runtime_settings = settings or get_settings()
    driver_kwargs: dict[str, Any] = {}
    if timeout_seconds is not None:
        driver_kwargs["connection_timeout"] = max(1.0, timeout_seconds)
    try:
        return GraphDatabase.driver(
            runtime_settings.neo4j_uri,
            auth=(runtime_settings.neo4j_user, runtime_settings.neo4j_password),
            **driver_kwargs,
        )
    except Exception as exc:
        raise RuntimeError(
            build_neo4j_error_message(
                runtime_settings,
                operation=operation,
                exc=exc,
            )
        ) from exc


def _is_wrapped_neo4j_error(exc: Exception) -> bool:
    """Detecta errores Neo4j ya enriquecidos para evitar doble wrapping."""
    return str(exc).startswith("No se pudo completar la operación de Neo4j ")


class GraphBuilder:
    """Generador de gráficos para almacenar archivos, símbolos y relaciones."""

    _WRITE_BATCH_SIZE = 500
    _DEPENDENCY_INVENTORY_TERMS = {
        "dependency",
        "dependencies",
        "dependencia",
        "dependencias",
    }

    _shared_driver: Any | None = None
    _shared_config: tuple[str, str, str] | None = None
    _driver_lock: Lock = Lock()

    def __init__(self) -> None:
        """Cree el controlador Neo4j desde la configuración."""
        settings = get_settings()
        config = (settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        with self._driver_lock:
            if self._shared_driver is None or self._shared_config != config:
                self.__class__._shared_driver = build_neo4j_driver(
                    settings,
                    operation="crear driver compartido",
                )
                self.__class__._shared_config = config
            self.driver = self._shared_driver

    @contextmanager
    def _managed_session(
        self,
        *,
        operation: str,
        repo_id: str | None = None,
    ):
        """Abre una sesión Neo4j y envuelve errores con contexto operativo."""
        try:
            with self.driver.session() as session:
                yield session
        except Exception as exc:
            if _is_wrapped_neo4j_error(exc):
                raise
            raise RuntimeError(
                build_neo4j_error_message(
                    get_settings(),
                    operation=operation,
                    exc=exc,
                    repo_id=repo_id,
                )
            ) from exc

    def close(self) -> None:
        """Mantiene compatibilidad de API; el driver se comparte por proceso."""
        return None

    @classmethod
    def close_shared_driver(cls) -> None:
        """Cierra explícitamente el driver compartido cuando sea necesario."""
        with cls._driver_lock:
            if cls._shared_driver is not None:
                cls._shared_driver.close()
                cls._shared_driver = None
                cls._shared_config = None

    def upsert_repo_graph(
        self,
        repo_id: str,
        scanned_files: list[ScannedFile],
        symbols: list[SymbolChunk],
        semantic_relations: list[SemanticRelation] | None = None,
        file_import_relations: list[FileImportRelation] | None = None,
    ) -> None:
        """Inserte nodos, archivos, módulos y símbolos del gráfico del repositorio."""
        file_query = """
        MERGE (r:Repo {id: $repo_id})
        MERGE (m:Module {repo_id: $repo_id, path: $module_path})
        SET m.name = $module_name,
            m.path_depth = $module_depth
        MERGE (r)-[:HAS_MODULE]->(m)
        MERGE (f:File {repo_id: $repo_id, path: $path})
        SET f.language = $language,
            f.file_name = $file_name,
            f.module_path = $module_path,
            f.path_depth = $path_depth,
            f.purpose_summary = $purpose_summary,
            f.purpose_source = $purpose_source,
            f.top_level_symbol_names = $top_level_symbol_names,
            f.top_level_symbol_types = $top_level_symbol_types
        MERGE (m)-[:CONTAINS]->(f)
        """
        symbol_query = """
        MERGE (r:Repo {id: $repo_id})
        MERGE (f:File {repo_id: $repo_id, path: $path})
        MERGE (s:Symbol {id: $symbol_id})
        SET s.repo_id = $repo_id,
            s.name = $symbol_name,
            s.name_lc = toLower($symbol_name),
            s.type = $symbol_type,
            s.start_line = $start_line,
            s.end_line = $end_line
        MERGE (f)-[:DECLARES]->(s)
        """
        with self._managed_session(
            operation="upsert de grafo por repositorio",
            repo_id=repo_id,
        ) as session:
            symbols_by_path: dict[str, list[SymbolChunk]] = defaultdict(list)
            for symbol in symbols:
                symbols_by_path[symbol.path].append(symbol)

            for file_obj in scanned_files:
                module_path = file_obj.path.split("/", 1)[0] if "/" in file_obj.path else "."
                module_name = module_path.rsplit("/", 1)[-1] if module_path != "." else "."
                module_depth = 0 if module_path == "." else module_path.count("/") + 1
                purpose_summary, purpose_source = infer_component_purpose(
                    file_obj.path,
                    file_obj.content,
                )
                file_symbols = symbols_by_path.get(file_obj.path, [])
                top_level_symbol_names = [item.symbol_name for item in file_symbols[:20]]
                top_level_symbol_types = sorted(
                    {item.symbol_type for item in file_symbols if item.symbol_type}
                )[:20]
                session.run(
                    file_query,
                    repo_id=repo_id,
                    module_path=module_path,
                    module_name=module_name,
                    module_depth=module_depth,
                    path=file_obj.path,
                    language=file_obj.language,
                    file_name=file_obj.path.rsplit("/", 1)[-1],
                    path_depth=file_obj.path.count("/") + 1,
                    purpose_summary=purpose_summary,
                    purpose_source=purpose_source,
                    top_level_symbol_names=top_level_symbol_names,
                    top_level_symbol_types=top_level_symbol_types,
                )
            for symbol in symbols:
                session.run(
                    symbol_query,
                    repo_id=repo_id,
                    path=symbol.path,
                    symbol_id=symbol.id,
                    symbol_name=symbol.symbol_name,
                    symbol_type=symbol.symbol_type,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                )
            self._upsert_semantic_relations(
                session=session,
                repo_id=repo_id,
                semantic_relations=semantic_relations or [],
            )
            settings = get_settings()
            if bool(getattr(settings, "semantic_graph_file_edges_enabled", False)):
                self._upsert_file_dependency_edges(
                    session=session,
                    repo_id=repo_id,
                    semantic_relations=semantic_relations or [],
                    symbols=symbols,
                    file_import_relations=file_import_relations or [],
                )
                self._upsert_file_external_imports(
                    session=session,
                    repo_id=repo_id,
                    file_import_relations=file_import_relations or [],
                )

    @staticmethod
    def _derive_file_dependency_edges(
        semantic_relations: list[SemanticRelation],
        symbols: list[SymbolChunk],
        file_import_relations: list[FileImportRelation] | None = None,
    ) -> list[dict[str, Any]]:
        """Collapse resolved semantic relations into deduplicated file edges."""
        symbol_path_by_id = {symbol.id: symbol.path for symbol in symbols}
        edges_by_pair: dict[tuple[str, str], dict[str, Any]] = {}

        for relation in semantic_relations:
            if relation.target_symbol_id is None:
                continue
            target_path = symbol_path_by_id.get(relation.target_symbol_id)
            if not target_path or target_path == relation.path:
                continue

            pair = (relation.path, target_path)
            edge = edges_by_pair.setdefault(
                pair,
                {
                    "source_path": relation.path,
                    "target_path": target_path,
                    "count": 0,
                    "relation_types": set(),
                },
            )
            edge["count"] += 1
            edge["relation_types"].add(relation.relation_type)

        for relation in file_import_relations or []:
            if relation.target_path is None or relation.target_path == relation.source_path:
                continue
            pair = (relation.source_path, relation.target_path)
            edge = edges_by_pair.setdefault(
                pair,
                {
                    "source_path": relation.source_path,
                    "target_path": relation.target_path,
                    "count": 0,
                    "relation_types": set(),
                },
            )
            edge["count"] += 1
            edge["relation_types"].add("IMPORTS")

        rows: list[dict[str, Any]] = []
        for edge in edges_by_pair.values():
            rows.append(
                {
                    "source_path": edge["source_path"],
                    "target_path": edge["target_path"],
                    "count": edge["count"],
                    "relation_types": sorted(edge["relation_types"]),
                }
            )
        rows.sort(key=lambda item: (item["source_path"], item["target_path"]))
        return rows

    @classmethod
    def _iter_write_batches(
        cls,
        rows: list[dict[str, Any]],
    ) -> Iterator[list[dict[str, Any]]]:
        """Trocea payloads grandes de escritura para reducir presión de memoria."""
        batch_size = max(1, cls._WRITE_BATCH_SIZE)
        for index in range(0, len(rows), batch_size):
            yield rows[index : index + batch_size]

    def _upsert_file_dependency_edges(
        self,
        session: Any,
        repo_id: str,
        semantic_relations: list[SemanticRelation],
        symbols: list[SymbolChunk],
        file_import_relations: list[FileImportRelation],
    ) -> None:
        """Persist derived file dependency edges from resolved semantic relations."""
        rows = self._derive_file_dependency_edges(
            semantic_relations,
            symbols,
            file_import_relations,
        )
        if not rows:
            return

        query = """
            UNWIND $rows AS row
            MATCH (source:File {repo_id: $repo_id, path: row.source_path})
            MATCH (target:File {repo_id: $repo_id, path: row.target_path})
            MERGE (source)-[r:IMPORTS_FILE {repo_id: $repo_id, source_path: row.source_path, target_path: row.target_path}]->(target)
            SET r.count = row.count,
                r.relation_types = row.relation_types
            """
        for batch in self._iter_write_batches(rows):
            session.run(
                query,
                repo_id=repo_id,
                rows=batch,
            )

    def _upsert_file_external_imports(
        self,
        session: Any,
        repo_id: str,
        file_import_relations: list[FileImportRelation],
    ) -> None:
        """Persist file-level external imports derived from top-level Python imports."""
        rows = [
            {
                "source_path": relation.source_path,
                "target_ref": relation.target_ref,
                "path": relation.path,
                "line": relation.line,
                "language": relation.language,
                "resolution_method": relation.resolution_method,
            }
            for relation in file_import_relations
            if relation.target_path is None
        ]
        if not rows:
            return

        query = """
            UNWIND $rows AS row
            MATCH (source:File {repo_id: $repo_id, path: row.source_path})
            MERGE (target:ExternalSymbol {
                repo_id: $repo_id,
                ref: row.target_ref,
                language: row.language
            })
            MERGE (source)-[r:IMPORTS_EXTERNAL_FILE {repo_id: $repo_id, source_path: row.source_path, target_ref: row.target_ref}]->(target)
            SET r.path = row.path,
                r.line = row.line,
                r.language = row.language,
                r.resolution_method = row.resolution_method
            """
        for batch in self._iter_write_batches(rows):
            session.run(
                query,
                repo_id=repo_id,
                rows=batch,
            )

    def _upsert_semantic_relations(
        self,
        session: Any,
        repo_id: str,
        semantic_relations: list[SemanticRelation],
    ) -> None:
        """Inserta relaciones semánticas en lotes con idempotencia por relación."""
        if not semantic_relations:
            return

        resolved_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        unresolved_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        supported_types = {"CALLS", "IMPORTS", "EXTENDS", "IMPLEMENTS"}

        for relation in semantic_relations:
            relation_type = relation.relation_type.strip().upper()
            if relation_type not in supported_types:
                continue
            row = {
                "source_symbol_id": relation.source_symbol_id,
                "target_symbol_id": relation.target_symbol_id,
                "target_ref": relation.target_ref,
                "target_kind": relation.target_kind,
                "source_path": relation.path,
                "path": relation.path,
                "line": relation.line,
                "confidence": relation.confidence,
                "language": relation.language,
                "resolution_method": relation.resolution_method,
                "relation_id": self._relation_id(relation),
            }
            if relation.target_symbol_id:
                resolved_by_type[relation_type].append(row)
            else:
                unresolved_by_type[relation_type].append(row)

        for relation_type, rows in resolved_by_type.items():
            query = f"""
                UNWIND $rows AS row
                MATCH (s:Symbol {{id: row.source_symbol_id}})
                MATCH (t:Symbol {{id: row.target_symbol_id}})
                MERGE (s)-[r:{relation_type} {{repo_id: $repo_id, relation_id: row.relation_id}}]->(t)
                SET r.path = row.path,
                    r.line = row.line,
                    r.confidence = row.confidence,
                    r.target_ref = row.target_ref,
                    r.target_kind = row.target_kind,
                    r.language = row.language,
                    r.resolution_method = row.resolution_method
                """
            for batch in self._iter_write_batches(rows):
                session.run(
                    query,
                    repo_id=repo_id,
                    rows=batch,
                )

        for relation_type, rows in unresolved_by_type.items():
            query = f"""
                UNWIND $rows AS row
                MATCH (s:Symbol {{id: row.source_symbol_id}})
                MERGE (t:ExternalSymbol {{
                    repo_id: $repo_id,
                    ref: row.target_ref,
                    language: row.language
                }})
                MERGE (s)-[r:{relation_type} {{repo_id: $repo_id, relation_id: row.relation_id}}]->(t)
                SET r.path = row.path,
                    r.source_path = row.source_path,
                    r.line = row.line,
                    r.confidence = row.confidence,
                    r.target_ref = row.target_ref,
                    r.target_kind = row.target_kind,
                    r.language = row.language,
                    r.resolution_method = row.resolution_method
                """
            for batch in self._iter_write_batches(rows):
                session.run(
                    query,
                    repo_id=repo_id,
                    rows=batch,
                )

    @staticmethod
    def _relation_id(relation: SemanticRelation) -> str:
        """Construye un ID determinista para evitar duplicados de relaciones."""
        payload = (
            f"{relation.repo_id}|{relation.source_symbol_id}|"
            f"{relation.relation_type}|{relation.target_symbol_id or ''}|"
            f"{relation.target_ref}|{relation.path}|{relation.line}|"
            f"{relation.language}"
        )
        return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()

    def has_repo_data(self, repo_id: str) -> bool:
        """Indica si existen nodos asociados al repositorio en Neo4j."""
        query = "MATCH (n {repo_id: $repo_id}) RETURN count(n) AS total"
        with self._managed_session(
            operation="verificar nodos por repo_id",
            repo_id=repo_id,
        ) as session:
            record = session.run(query, repo_id=repo_id).single()
            if record is None:
                return False
            return int(record.get("total", 0) or 0) > 0

    def delete_repo_subgraph(self, repo_id: str) -> int:
        """Elimina el subgrafo asociado al repo_id y devuelve nodos borrados."""
        query = "MATCH (n {repo_id: $repo_id}) DETACH DELETE n"
        with self._managed_session(
            operation="eliminar subgrafo por repo_id",
            repo_id=repo_id,
        ) as session:
            result = session.run(query, repo_id=repo_id)
            summary = result.consume()
            return int(summary.counters.nodes_deleted)

    def clear_graph(self) -> int:
        """Elimina el grafo completo y devuelve la cantidad de nodos borrados."""
        query = "MATCH (n) DETACH DELETE n"
        with self._managed_session(
            operation="eliminar grafo completo",
        ) as session:
            result = session.run(query)
            summary = result.consume()
            return int(summary.counters.nodes_deleted)

    def query_inventory(
        self,
        repo_id: str,
        target_term: str,
        module_name: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Gráfico de consulta para entidades que coincidan con el término objetivo dentro del módulo opcional."""
        normalized_target = (target_term or "").strip().lower()
        if normalized_target in self._DEPENDENCY_INVENTORY_TERMS:
            query = """
            CALL () {
                MATCH (source:File {repo_id: $repo_id})
                      -[:IMPORTS_FILE]->(target:File {repo_id: $repo_id})
                WHERE (
                    $module_name IS NULL OR
                    source.path STARTS WITH $module_name + '/' OR
                    source.path CONTAINS '/' + $module_name + '/' OR
                    split(source.path, '/')[0] = $module_name
                )
                WITH DISTINCT target
                RETURN split(target.path, '/')[size(split(target.path, '/')) - 1]
                           AS label,
                       target.path AS path,
                       'file_dependency' AS kind,
                       1 AS start_line,
                       1 AS end_line
                UNION
                MATCH (source:File {repo_id: $repo_id})
                      -[r:IMPORTS_EXTERNAL_FILE]->
                      (target:ExternalSymbol {repo_id: $repo_id})
                WHERE (
                    $module_name IS NULL OR
                    source.path STARTS WITH $module_name + '/' OR
                    source.path CONTAINS '/' + $module_name + '/' OR
                    split(source.path, '/')[0] = $module_name
                )
                RETURN DISTINCT target.ref AS label,
                       source.path AS path,
                       'external_dependency' AS kind,
                       coalesce(r.line, 1) AS start_line,
                       coalesce(r.line, 1) AS end_line
            }
            RETURN label, path, kind, start_line, end_line
            ORDER BY kind, path, label
            SKIP $offset
            LIMIT $limit
            """
            with self._managed_session(
                operation="consultar inventario de dependencias",
                repo_id=repo_id,
            ) as session:
                records = session.run(
                    query,
                    repo_id=repo_id,
                    module_name=module_name,
                    limit=limit,
                    offset=offset,
                )
                return [record.data() for record in records]

        query = """
        MATCH (f:File {repo_id: $repo_id})
        WHERE (
            $module_name IS NULL OR
            f.path STARTS WITH $module_name + '/' OR
            f.path CONTAINS '/' + $module_name + '/' OR
            split(f.path, '/')[0] = $module_name
        )
        OPTIONAL MATCH (f)-[:DECLARES]->(s:Symbol)
        WITH f,
             collect({
                 name: toLower(coalesce(s.name, '')),
                 type: toLower(coalesce(s.type, ''))
             }) AS symbols,
             split(f.path, '/')[size(split(f.path, '/')) - 1] AS file_name,
             toLower($target_term) AS target
        WHERE (
            toLower(f.path) CONTAINS target OR
            toLower(file_name) CONTAINS target OR
            any(symbol IN symbols WHERE
                (
                    symbol.type IN [
                        'class', 'interface', 'struct', 'enum',
                        'trait', 'record', 'type', 'component'
                    ]
                    AND symbol.name CONTAINS target
                )
                OR symbol.type = target
            )
        )
        RETURN DISTINCT
            file_name AS label,
            f.path AS path,
            'file' AS kind,
            1 AS start_line,
            1 AS end_line
        ORDER BY path
        SKIP $offset
        LIMIT $limit
        """
        with self._managed_session(
            operation="consultar inventario",
            repo_id=repo_id,
        ) as session:
            records = session.run(
                query,
                repo_id=repo_id,
                target_term=target_term,
                module_name=module_name,
                limit=limit,
                offset=offset,
            )
            return [record.data() for record in records]

    def query_inventory_total(
        self,
        repo_id: str,
        target_term: str,
        module_name: str | None = None,
    ) -> int:
        """Cuente las entidades del inventario del gráfico que coincidan con el término objetivo y el filtro del módulo."""
        query = """
        MATCH (f:File {repo_id: $repo_id})
        WHERE (
            $module_name IS NULL OR
            f.path STARTS WITH $module_name + '/' OR
            f.path CONTAINS '/' + $module_name + '/' OR
            split(f.path, '/')[0] = $module_name
        )
        OPTIONAL MATCH (f)-[:DECLARES]->(s:Symbol)
        WITH f,
             collect({
                 name: toLower(coalesce(s.name, '')),
                 type: toLower(coalesce(s.type, ''))
             }) AS symbols,
             split(f.path, '/')[size(split(f.path, '/')) - 1] AS file_name,
             toLower($target_term) AS target
        WHERE (
            toLower(f.path) CONTAINS target OR
            toLower(file_name) CONTAINS target OR
            any(symbol IN symbols WHERE
                (
                    symbol.type IN [
                        'class', 'interface', 'struct', 'enum',
                        'trait', 'record', 'type', 'component'
                    ]
                    AND symbol.name CONTAINS target
                )
                OR symbol.type = target
            )
        )
        RETURN count(DISTINCT f.path) AS total
        """
        with self._managed_session(
            operation="contar inventario",
            repo_id=repo_id,
        ) as session:
            record = session.run(
                query,
                repo_id=repo_id,
                target_term=target_term,
                module_name=module_name,
            ).single()
            if record is None:
                return 0
            return int(record.get("total", 0) or 0)

    def query_module_files(
        self,
        repo_id: str,
        module_name: str,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Enumere archivos dentro de una ruta de módulo para solicitudes de inventario amplias."""
        query = """
        MATCH (f:File {repo_id: $repo_id})
        WHERE (
            f.path STARTS WITH $module_name + '/' OR
            f.path CONTAINS '/' + $module_name + '/' OR
            split(f.path, '/')[0] = $module_name
        )
        WITH f, split(f.path, '/')[size(split(f.path, '/')) - 1] AS file_name
        RETURN DISTINCT
            file_name AS label,
            f.path AS path,
            'file' AS kind,
            1 AS start_line,
            1 AS end_line
        ORDER BY path
        SKIP $offset
        LIMIT $limit
        """
        with self._managed_session(
            operation="consultar archivos por módulo",
            repo_id=repo_id,
        ) as session:
            records = session.run(
                query,
                repo_id=repo_id,
                module_name=module_name,
                limit=limit,
                offset=offset,
            )
            return [record.data() for record in records]

    def query_repo_modules(self, repo_id: str) -> list[str]:
        """Lista módulos persistidos del repositorio sin depender del workspace."""
        query = """
        MATCH (:Repo {id: $repo_id})-[:HAS_MODULE]->(m:Module {repo_id: $repo_id})
        WHERE m.path <> '.'
        RETURN DISTINCT m.path AS module_path
        ORDER BY module_path ASC
        """
        with self._managed_session(
            operation="listar módulos del repositorio",
            repo_id=repo_id,
        ) as session:
            records = session.run(query, repo_id=repo_id)
            modules: list[str] = []
            for record in records:
                module_path = str(record.get("module_path", "") or "").strip()
                if module_path:
                    modules.append(module_path)
            return modules

    def query_external_import_source_paths(
        self,
        repo_id: str,
        candidates: list[str],
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Devuelve archivos que importan refs externas que solapan con candidatos de query."""
        normalized_candidates = [
            item.strip().lower() for item in candidates if item and item.strip()
        ]
        if not normalized_candidates:
            return []

        query = """
        MATCH (source:File {repo_id: $repo_id})-[r:IMPORTS_EXTERNAL_FILE]->
              (target:ExternalSymbol {repo_id: $repo_id})
        WITH source.path AS source_path, toLower(target.ref) AS target_ref
        WHERE any(candidate IN $candidates WHERE target_ref CONTAINS candidate)
        WITH source_path,
             max(size([candidate IN $candidates WHERE target_ref CONTAINS candidate | candidate])) AS match_score
        RETURN source_path, match_score
        ORDER BY match_score DESC, source_path ASC
        LIMIT $limit
        """
        with self._managed_session(
            operation="consultar importadores externos",
            repo_id=repo_id,
        ) as session:
            records = session.run(
                query,
                repo_id=repo_id,
                candidates=normalized_candidates,
                limit=max(1, int(limit)),
            )
            return [record.data() for record in records]

    def query_file_paths_by_suffix(
        self,
        repo_id: str,
        candidates: list[str],
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Resuelve archivos del repo por path exacto o sufijo relevante."""
        normalized_candidates = [
            item.strip().lower().lstrip("./")
            for item in candidates
            if item and item.strip()
        ]
        if not normalized_candidates:
            return []

        query = """
        MATCH (f:File {repo_id: $repo_id})
        WITH f, toLower(f.path) AS path_lower, $candidates AS candidates
        WHERE any(candidate IN candidates
                  WHERE path_lower = candidate
                     OR path_lower ENDS WITH '/' + candidate)
        WITH f, path_lower, candidates,
             reduce(
                 best = 0,
                 candidate IN candidates |
                 CASE
                     WHEN path_lower = candidate THEN 3
                     WHEN split(path_lower, '/')[size(split(path_lower, '/')) - 1] = candidate THEN
                         CASE WHEN 2 > best THEN 2 ELSE best END
                     WHEN path_lower ENDS WITH '/' + candidate THEN
                         CASE WHEN 1 > best THEN 1 ELSE best END
                     ELSE best
                 END
             ) AS match_score
        RETURN f.path AS path, match_score
        ORDER BY match_score DESC, size(split(f.path, '/')) ASC, f.path ASC
        LIMIT $limit
        """
        with self._managed_session(
            operation="resolver archivos por sufijo",
            repo_id=repo_id,
        ) as session:
            records = session.run(
                query,
                repo_id=repo_id,
                candidates=normalized_candidates,
                limit=max(1, int(limit)),
            )
            return [record.data() for record in records]

    def query_file_importers(
        self,
        repo_id: str,
        target_paths: list[str],
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Devuelve importadores directos de archivos objetivo vía IMPORTS_FILE."""
        normalized_target_paths = [
            item.strip() for item in target_paths if item and item.strip()
        ]
        if not normalized_target_paths:
            return []

        query = """
        MATCH (source:File {repo_id: $repo_id})-[r:IMPORTS_FILE]->(target:File {repo_id: $repo_id})
        WHERE target.path IN $target_paths
        RETURN DISTINCT target.path AS target_path,
               split(source.path, '/')[size(split(source.path, '/')) - 1] AS label,
               source.path AS path,
               'file_importer' AS kind,
               1 AS start_line,
               1 AS end_line
        ORDER BY target_path ASC, path ASC
        LIMIT $limit
        """
        with self._managed_session(
            operation="consultar importadores de archivos",
            repo_id=repo_id,
        ) as session:
            records = session.run(
                query,
                repo_id=repo_id,
                target_paths=normalized_target_paths,
                limit=max(1, int(limit)),
            )
            return [record.data() for record in records]

    def query_file_impact_paths(
        self,
        repo_id: str,
        target_paths: list[str],
        *,
        max_depth: int = 2,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Devuelve archivos impactados por IMPORTS_FILE a profundidad fija 1..2."""
        normalized_target_paths = [
            item.strip() for item in target_paths if item and item.strip()
        ]
        if not normalized_target_paths:
            return []

        effective_depth = 1 if int(max_depth) <= 1 else 2
        if effective_depth == 1:
            query = """
            MATCH (source:File {repo_id: $repo_id})-[:IMPORTS_FILE]->
                  (target:File {repo_id: $repo_id})
            WHERE target.path IN $target_paths
            RETURN DISTINCT
                   split(source.path, '/')[size(split(source.path, '/')) - 1] AS label,
                   source.path AS path,
                   'file_impact_direct' AS kind,
                   1 AS start_line,
                   1 AS end_line,
                   1 AS hop_distance
            ORDER BY hop_distance ASC, path ASC
            LIMIT $limit
            """
        else:
            query = """
            CALL {
                MATCH (source:File {repo_id: $repo_id})-[:IMPORTS_FILE]->
                      (target:File {repo_id: $repo_id})
                WHERE target.path IN $target_paths
                RETURN DISTINCT
                       split(source.path, '/')[size(split(source.path, '/')) - 1] AS label,
                       source.path AS path,
                       1 AS start_line,
                       1 AS end_line,
                       1 AS hop_distance
                UNION
                MATCH (source:File {repo_id: $repo_id})-[:IMPORTS_FILE]->
                      (middle:File {repo_id: $repo_id})-[:IMPORTS_FILE]->
                      (target:File {repo_id: $repo_id})
                WHERE target.path IN $target_paths
                  AND NOT source.path IN $target_paths
                RETURN DISTINCT
                       split(source.path, '/')[size(split(source.path, '/')) - 1] AS label,
                       source.path AS path,
                       1 AS start_line,
                       1 AS end_line,
                       2 AS hop_distance
            }
            WITH label, path, start_line, end_line, min(hop_distance) AS hop_distance
            RETURN label,
                   path,
                   CASE
                       WHEN hop_distance = 1 THEN 'file_impact_direct'
                       ELSE 'file_impact_transitive'
                   END AS kind,
                   start_line,
                   end_line,
                   hop_distance
            ORDER BY hop_distance ASC, path ASC
            LIMIT $limit
            """

        with self._managed_session(
            operation="consultar impacto por archivo",
            repo_id=repo_id,
        ) as session:
            records = session.run(
                query,
                repo_id=repo_id,
                target_paths=normalized_target_paths,
                limit=max(1, int(limit)),
            )
            return [record.data() for record in records]

    def query_file_purpose_summaries(
        self,
        repo_id: str,
        paths: list[str],
    ) -> dict[str, dict[str, str]]:
        """Devuelve propósitos persistidos por archivo para inventory explain."""
        normalized_paths = [path.strip() for path in paths if path and path.strip()]
        if not normalized_paths:
            return {}

        query = """
        UNWIND $paths AS requested_path
        MATCH (f:File {repo_id: $repo_id, path: requested_path})
        RETURN f.path AS path,
               f.purpose_summary AS purpose_summary,
               f.purpose_source AS purpose_source
        """
        with self._managed_session(
            operation="consultar propósitos de archivos",
            repo_id=repo_id,
        ) as session:
            records = session.run(
                query,
                repo_id=repo_id,
                paths=normalized_paths,
            )
            summaries: dict[str, dict[str, str]] = {}
            for record in records:
                path = str(record.get("path", "") or "").strip()
                summary = str(record.get("purpose_summary", "") or "").strip()
                source = str(record.get("purpose_source", "") or "").strip()
                if path and summary:
                    summaries[path] = {
                        "purpose_summary": summary,
                        "purpose_source": source,
                    }
            return summaries

    def expand_symbols(
        self,
        symbol_ids: list[str],
        hops: int = 2,
        relation_types: list[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Expanda vecindad de símbolos con filtros opcionales de relación."""
        safe_hops = max(1, int(hops))
        query = f"""
        MATCH (s:Symbol)
        WHERE s.id IN $symbol_ids
        MATCH p=(s)-[*1..{safe_hops}]-(n)
        WHERE (
            $relation_types IS NULL OR
            size($relation_types) = 0 OR
            all(rel IN relationships(p) WHERE type(rel) IN $relation_types)
        )
        RETURN DISTINCT s.id as seed,
               labels(n) as labels,
               properties(n) as props,
             size(relationships(p)) as edge_count,
             [rel IN relationships(p) | type(rel)] as relation_types,
             CASE size(relationships(p))
                  WHEN 0 THEN 1.0
                  ELSE reduce(
                   confidence = 0.0,
                   rel IN relationships(p) |
                   confidence + toFloat(coalesce(rel.confidence, 1.0))
                  ) / toFloat(size(relationships(p)))
             END as relation_confidence_avg
        LIMIT $limit
        """
        with self._managed_session(
            operation="expandir símbolos",
        ) as session:
            records = session.run(
                query,
                symbol_ids=symbol_ids,
                relation_types=relation_types,
                limit=max(1, int(limit)),
            )
            return [record.data() for record in records]

    def expand_symbol_file_context(
        self,
        symbol_ids: list[str],
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Expande dependencias de archivo alcanzables desde símbolos semilla."""
        query = """
        CALL () {
            MATCH (s:Symbol)
            WHERE s.id IN $symbol_ids
            MATCH (source:File)-[:DECLARES]->(s)
            MATCH (source)-[r:IMPORTS_FILE]->(target:File)
            RETURN DISTINCT s.id AS seed,
                   labels(target) AS labels,
                   properties(target) AS props,
                   1 AS edge_count,
                   ['IMPORTS_FILE'] AS relation_types,
                     1.0 AS relation_confidence_avg,
                     1 AS line,
                     '' AS source_path
            UNION
            MATCH (s:Symbol)
            WHERE s.id IN $symbol_ids
            MATCH (source:File)-[:DECLARES]->(s)
            MATCH (source)-[r:IMPORTS_EXTERNAL_FILE]->(target:ExternalSymbol)
            RETURN DISTINCT s.id AS seed,
                   labels(target) AS labels,
                   properties(target) AS props,
                   1 AS edge_count,
                   ['IMPORTS_EXTERNAL_FILE'] AS relation_types,
                   1.0 AS relation_confidence_avg,
                   coalesce(r.line, 1) AS line,
                   coalesce(r.source_path, source.path, '') AS source_path
        }
        RETURN seed,
               labels,
               props,
               edge_count,
               relation_types,
               relation_confidence_avg,
               line,
               source_path
        LIMIT $limit
        """
        with self._managed_session(
            operation="expandir contexto de archivos desde símbolos",
        ) as session:
            records = session.run(
                query,
                symbol_ids=symbol_ids,
                limit=max(1, int(limit)),
            )
            return [record.data() for record in records]

    def expand_file_path_context(
        self,
        repo_id: str,
        file_paths: list[str],
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Expande dependencias de archivo alcanzables desde archivos semilla."""
        if not file_paths:
            return []

        query = """
        CALL () {
            MATCH (source:File {repo_id: $repo_id})
            WHERE source.path IN $file_paths
            MATCH (source)-[r:IMPORTS_FILE]->(target:File)
            RETURN DISTINCT source.path AS seed,
                   labels(target) AS labels,
                   properties(target) AS props,
                   1 AS edge_count,
                   ['IMPORTS_FILE'] AS relation_types,
                   1.0 AS relation_confidence_avg,
                   1 AS line,
                   '' AS source_path
            UNION
            MATCH (source:File {repo_id: $repo_id})
            WHERE source.path IN $file_paths
            MATCH (source)-[r:IMPORTS_EXTERNAL_FILE]->(target:ExternalSymbol)
            RETURN DISTINCT source.path AS seed,
                   labels(target) AS labels,
                   properties(target) AS props,
                   1 AS edge_count,
                   ['IMPORTS_EXTERNAL_FILE'] AS relation_types,
                   1.0 AS relation_confidence_avg,
                   coalesce(r.line, 1) AS line,
                   coalesce(r.source_path, source.path, '') AS source_path
        }
        RETURN seed,
               labels,
               props,
               edge_count,
               relation_types,
               relation_confidence_avg,
               line,
               source_path
        LIMIT $limit
        """
        with self._managed_session(
            operation="expandir contexto de archivos",
            repo_id=repo_id,
        ) as session:
            records = session.run(
                query,
                repo_id=repo_id,
                file_paths=sorted({path for path in file_paths if path}),
                limit=max(1, int(limit)),
            )
            return [record.data() for record in records]
