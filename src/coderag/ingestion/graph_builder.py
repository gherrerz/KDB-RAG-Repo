"""Integración de Neo4j para la construcción de gráficos de conocimiento de código."""

import hashlib
from collections import defaultdict
from threading import Lock
from typing import Any

from neo4j import GraphDatabase

from coderag.core.models import ScannedFile, SemanticRelation, SymbolChunk
from coderag.core.settings import get_settings


class GraphBuilder:
    """Generador de gráficos para almacenar archivos, símbolos y relaciones."""

    _shared_driver: Any | None = None
    _shared_config: tuple[str, str, str] | None = None
    _driver_lock: Lock = Lock()

    def __init__(self) -> None:
        """Cree el controlador Neo4j desde la configuración."""
        settings = get_settings()
        config = (settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        with self._driver_lock:
            if self._shared_driver is None or self._shared_config != config:
                self.__class__._shared_driver = GraphDatabase.driver(
                    settings.neo4j_uri,
                    auth=(settings.neo4j_user, settings.neo4j_password),
                )
                self.__class__._shared_config = config
            self.driver = self._shared_driver

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
    ) -> None:
        """Inserte nodos, archivos, módulos y símbolos del gráfico del repositorio."""
        file_query = """
        MERGE (r:Repo {id: $repo_id})
        MERGE (m:Module {repo_id: $repo_id, path: $module_path})
        MERGE (r)-[:HAS_MODULE]->(m)
        MERGE (f:File {repo_id: $repo_id, path: $path})
        SET f.language = $language
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
        with self.driver.session() as session:
            for file_obj in scanned_files:
                module_path = file_obj.path.split("/", 1)[0] if "/" in file_obj.path else "."
                session.run(
                    file_query,
                    repo_id=repo_id,
                    module_path=module_path,
                    path=file_obj.path,
                    language=file_obj.language,
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
                "path": relation.path,
                "line": relation.line,
                "confidence": relation.confidence,
                "language": relation.language,
                "relation_id": self._relation_id(relation),
            }
            if relation.target_symbol_id:
                resolved_by_type[relation_type].append(row)
            else:
                unresolved_by_type[relation_type].append(row)

        for relation_type, rows in resolved_by_type.items():
            session.run(
                f"""
                UNWIND $rows AS row
                MATCH (s:Symbol {{id: row.source_symbol_id}})
                MATCH (t:Symbol {{id: row.target_symbol_id}})
                MERGE (s)-[r:{relation_type} {{repo_id: $repo_id, relation_id: row.relation_id}}]->(t)
                SET r.path = row.path,
                    r.line = row.line,
                    r.confidence = row.confidence,
                    r.target_ref = row.target_ref,
                    r.target_kind = row.target_kind,
                    r.language = row.language
                """,
                repo_id=repo_id,
                rows=rows,
            )

        for relation_type, rows in unresolved_by_type.items():
            session.run(
                f"""
                UNWIND $rows AS row
                MATCH (s:Symbol {{id: row.source_symbol_id}})
                MERGE (t:ExternalSymbol {{
                    repo_id: $repo_id,
                    ref: row.target_ref,
                    language: row.language
                }})
                MERGE (s)-[r:{relation_type} {{repo_id: $repo_id, relation_id: row.relation_id}}]->(t)
                SET r.path = row.path,
                    r.line = row.line,
                    r.confidence = row.confidence,
                    r.target_ref = row.target_ref,
                    r.target_kind = row.target_kind,
                    r.language = row.language
                """,
                repo_id=repo_id,
                rows=rows,
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
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def has_repo_data(self, repo_id: str) -> bool:
        """Indica si existen nodos asociados al repositorio en Neo4j."""
        query = "MATCH (n {repo_id: $repo_id}) RETURN count(n) AS total"
        with self.driver.session() as session:
            record = session.run(query, repo_id=repo_id).single()
            if record is None:
                return False
            return int(record.get("total", 0) or 0) > 0

    def delete_repo_subgraph(self, repo_id: str) -> int:
        """Elimina el subgrafo asociado al repo_id y devuelve nodos borrados."""
        query = "MATCH (n {repo_id: $repo_id}) DETACH DELETE n"
        with self.driver.session() as session:
            result = session.run(query, repo_id=repo_id)
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
        with self.driver.session() as session:
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
        with self.driver.session() as session:
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
        with self.driver.session() as session:
            records = session.run(
                query,
                repo_id=repo_id,
                module_name=module_name,
                limit=limit,
                offset=offset,
            )
            return [record.data() for record in records]

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
        with self.driver.session() as session:
            records = session.run(
                query,
                symbol_ids=symbol_ids,
                relation_types=relation_types,
                limit=max(1, int(limit)),
            )
            return [record.data() for record in records]
